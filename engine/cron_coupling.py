"""cron_coupling.py — 引擎心跳 cron 与引擎生命周期的自动耦合（2026-10-10 任务书）。

背景：写作引擎的推进依赖常驻心跳 cron（ff9ab3a8adf6，每 5 分钟扫 instances/
推一拍），但心跳此前与"引擎是否真的在跑"无关——实例全部终态后心跳仍在空转；
而人工判断"引擎死活"又不可靠（2026-10-10 hermes 据 exchange/ledger.md 的 mtime
误判写作引擎已死 10 天并暂停心跳，实例其实一直在被推进）。本模块把两件事焊死：

    引擎在跑（存在活动实例）⇒ 心跳 job 处于 scheduled
    引擎全停（无活动实例）  ⇒ 心跳 job 自动 pause

接入点（缺一不可，见任务书 §二.2）：
  A. 实例创建入口（ww-frontend api.create_instance / engine._load_state 隐式建档
     / 冒烟脚本）→ 创建成功后 reconcile_silent()
  B. 实例终态收口（engine._save_state 落 completed/failed 后；--reset 删除后）
     → reconcile_silent()
  C. 心跳 wrapper 每次运行先 reconcile() 自愈（防 A/B 漏挂）

硬性约束：
  - 异常隔离：hermes 不在 PATH / 超时 / 非零退出 → 只写 logs/cron_coupling.log，
    绝不抛给调用方、绝不阻塞引擎推进或实例创建（reconcile_silent 兜底）。
  - 幂等：目标态已达成时不再 resume/pause（返回 'unchanged'），重复调用无副作用。
  - 子进程限时 20s（HERMES_TIMEOUT_S）。
  - hermes 路径：shutil.which 解析，失败回退 venv 绝对路径；再失败写日志并由
    reconcile_silent 静默跳过，禁止硬编码猜测后静默失败。

活动实例口径（与 ww_heartbeat_wrapper._discover_running_instances 严格同源，
"心跳在跑 ⟺ wrapper 有活干"）：status ∉ {completed, failed}（wrapper 终态
黑名单）且非 t-*/t_* 测试前缀（wrapper G-2 守卫同款）。

测试隔离：环境变量 CRON_COUPLING_DISABLE=1（两仓 conftest 已设置）时
reconcile_silent 直接跳过——pytest 回归绝不触达真实 cron job。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# ── 本引擎对应的 cron job（wrapper 从这里读，不许散落硬编码）──
COUPLED_JOB_ID = "ff9ab3a8adf6"  # ww-engine-heartbeat（hermes cron）

# hermes 可执行文件兜底路径（本机无系统级 Python；hermes 装在 hermes-agent venv）
HERMES_PATH_FALLBACK = Path(
    str(Path.home() / "AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe"))
HERMES_TIMEOUT_S = 20

# 终态口径（与 ww_heartbeat_wrapper.TERMINAL_STATUSES 同值同语义）
TERMINAL_STATUSES = ("completed", "failed")
_TEST_PREFIXES = ("t-", "t_")

LOG_FILE = Path(__file__).resolve().parent / "logs" / "cron_coupling.log"
_DISABLE_ENV = "CRON_COUPLING_DISABLE"
_TRUTHY = ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    """追加一行日志；日志失败静默（绝不因日志问题影响主流程）。"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def log_error(where: str, exc: BaseException) -> None:
    """公开给 wrapper 的错误落日志入口（wrapper 自身 except 分支复用）。"""
    _log(f"[ERROR] {where}: {exc.__class__.__name__}: {exc}")


def disabled() -> bool:
    """测试禁用开关：CRON_COUPLING_DISABLE 置真时 reconcile_silent 直接跳过。"""
    return os.environ.get(_DISABLE_ENV, "").strip().lower() in _TRUTHY


def _instances_dir() -> Path:
    """与 engine.py / wrapper 同源：WW_INSTANCES_DIR env 覆盖优先（调用时读，
    便于测试沙箱重定向），缺省 = 本模块所在 engine 目录下的 instances/。"""
    env = os.environ.get("WW_INSTANCES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "instances"


def active_instances() -> list[str]:
    """当前活动实例名（口径与 ww_heartbeat_wrapper 发现逻辑一致）。"""
    d = _instances_dir()
    if not d.is_dir():
        return []
    out: list[str] = []
    for fp in sorted(d.glob("*.json")):
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 撕裂实例容忍跳过（wrapper 同款）
            continue
        name = str(st.get("instance") or "")
        if not name or str(st.get("status")) in TERMINAL_STATUSES:
            continue
        if name.startswith(_TEST_PREFIXES):  # G-2 守卫：测试实例不算活动
            continue
        out.append(name)
    return out


def _hermes_exe() -> str:
    exe = shutil.which("hermes")
    if exe:
        return exe
    if HERMES_PATH_FALLBACK.is_file():
        return str(HERMES_PATH_FALLBACK)
    raise FileNotFoundError(
        f"hermes 可执行文件解析失败（shutil.which('hermes') 与兜底 "
        f"{HERMES_PATH_FALLBACK} 均不可用）")


def _run_hermes(*args: str) -> str:
    """子进程调 hermes CLI，限时 20s，非零退出抛错（调用方兜底）。"""
    exe = _hermes_exe()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([exe, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=HERMES_TIMEOUT_S, env=env)
    if r.returncode != 0:
        raise RuntimeError(
            f"hermes {' '.join(args)} 退出码 {r.returncode}: "
            f"{(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def job_state() -> str:
    """本 job 当前状态（'active' / 'paused' / 其他）。

    必须子进程实查 `hermes cron list --all`（paused job 在默认列表里不显示，
    --all 才可见，实测），禁止靠猜。找不到 job 抛错（调用方兜底）。
    """
    out = _run_hermes("cron", "list", "--all")
    m = re.search(rf"^\s*{re.escape(COUPLED_JOB_ID)}\s+\[(\w+)\]", out, re.M)
    if not m:
        raise RuntimeError(f"hermes cron list 输出中找不到 job {COUPLED_JOB_ID}")
    return m.group(1)


def ensure_heartbeat_on() -> None:
    """有活动实例时保证心跳 job 处于 scheduled；已是则什么都不做（幂等）。"""
    state = job_state()
    if state == "active":
        return
    _run_hermes("cron", "resume", COUPLED_JOB_ID)
    _log(f"resume {COUPLED_JOB_ID}（原状态 [{state}]，存在活动实例）")


def reconcile() -> str:
    """按当前实例状态对齐心跳开关，返回 'on'（本次 resume）/ 'off'（本次 pause）
    / 'unchanged'（已是目标态）。

    本函数可能抛错（hermes 不可用等）——引擎/面板/wrapper 侧统一走
    reconcile_silent()，wrapper 自身 except 后走降级路径。
    """
    active = active_instances()
    state = job_state()
    if active:
        if state == "active":
            return "unchanged"
        ensure_heartbeat_on()
        return "on"
    if state == "paused":
        return "unchanged"
    _run_hermes("cron", "pause", COUPLED_JOB_ID)
    _log(f"pause {COUPLED_JOB_ID}（无活动实例，原状态 [{state}]）")
    return "off"


def reconcile_silent() -> None:
    """reconcile 的静默包装：任何异常只写日志，绝不抛给调用方、绝不阻塞引擎主流程。"""
    if disabled():
        return
    try:
        reconcile()
    except Exception as exc:  # noqa: BLE001 — 异常隔离红线（任务书 §二.1）
        log_error("reconcile_silent", exc)
