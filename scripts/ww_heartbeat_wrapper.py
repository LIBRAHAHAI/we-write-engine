"""ww_heartbeat_wrapper.py — we-write-engine 心跳 wrapper（T8）。

与 flow-engine 的 engine_heartbeat_wrapper.py 同款语义：
扫描 we-write-engine/engine/instances/*.json 中 status∈{running, idle} 的文章实例，
逐个推进一拍（单步语义）。输出：有推进时打印一行；纯等待静默（cron 去重）。

20260921 修复：idle（API/面板新建、尚未首拍的实例）原先不在扫描范围 → 新建项目
没有任何机制驱动首拍，永久 idle（面板也不显示 idle）——article-4 实证。心跳补位：
新建实例 5 分钟内被自动首拍（建选题卡 → status=running），之后按 running 正常轮询。

接入 cron（no_agent 模式）：每 5 分钟跑一次本脚本，输出非空才投递。
拍板点到达（⏸ 拍板点）必投递（user 需拍板）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# ── 项目根解析（B1 修复 2026-09-15）─────────────────────────────────────────
# 教训：本脚本被 cron 以 <HERMES_HOME>/scripts/ww_heartbeat_wrapper.py 执行（部署副本
# 落在那里），原先用 Path(__file__).parent.parent 推项目根 → 解析成
# ./agents-home/engine/engine.py（不存在），instances 目录同样不存在 →
# _discover_running_instances() 发现 0 实例 → 空输出 exit 0 → cron 记 silent，
# 心跳自 9/12 挂牌起空转 796 次零告警（article-002 停滞 14 小时的真因，2026-09-15）。
# 故：① 项目根走「env 显式覆盖 → __file__ 可用则用 → 绝对锚点兜底」，与 cwd/副本位置
#        无关；② 路径不可用时响亮报错（stdout 非空 → cron 必投递），禁止再静默空转。
_ANCHOR_PROJECT_ROOT = Path(r".")


def _resolve_project_root() -> Path:
    env_root = os.environ.get("WW_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    cand = Path(__file__).resolve().parent.parent
    if (cand / "engine" / "engine.py").is_file():
        return cand
    return _ANCHOR_PROJECT_ROOT


PROJECT_ROOT = _resolve_project_root()
ENGINE_PY = PROJECT_ROOT / "engine" / "engine.py"
# G-2/031：与 engine.py 同源——env 覆盖优先（conftest 重定向后 wrapper 与 engine
# 看同一目录，否则 wrapper 看真实树、engine 看 temp，扫描对象错位）。
INSTANCES_DIR = Path(os.environ.get(
    "WW_INSTANCES_DIR", str(PROJECT_ROOT / "engine" / "instances")))

# 生命周期耦合（2026-10-10 任务书 C）：每次运行先 reconcile() 自愈——
# 有活动实例 ⇒ 心跳保证 scheduled；全停 ⇒ 本拍把自己 pause。job_id/活动口径
# 单一来源 = engine/cron_coupling.py（COUPLED_JOB_ID），不许在此散落硬编码。
# 模块缺失只降级耦合（心跳推进照旧），但响亮报错（stdout 非空 → cron 必投递），
# 与本脚本 preflight 的「配置故障必须响亮」哲学一致。
try:
    sys.path.insert(0, str(PROJECT_ROOT / "engine"))
    import cron_coupling
except Exception as _e:  # noqa: BLE001 — 耦合降级不拦心跳推进
    cron_coupling = None
    print(f"【we-write 引擎心跳 · 耦合模块缺失，心跳生命周期自愈不可用】\n"
          f"  ❌ import cron_coupling 失败: {_e}（期望位于 {PROJECT_ROOT / 'engine'}）")


# 2026-09-27（修规则不修事件，sc-pilot 实证）：终态黑名单——心跳推进「非终态」实例。
# 原白名单 (running/idle) 有个静默冻结面：实例 state 被外部写入非法状态值（如
# "done"，疑似 worker 把卡状态误写进实例）→ 心跳永久跳过该实例 → 静默停滞。
# 改为黑名单后，任何非终态状态值都不会冻住实例；终态仍严格不碰。
TERMINAL_STATUSES = ("completed", "failed")


def _discover_running_instances() -> list[str]:
    if not INSTANCES_DIR.is_dir():
        return []
    out = []
    for fp in sorted(INSTANCES_DIR.glob("*.json")):
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 撕裂实例容忍跳过
            continue
        # 20260921：idle 一并纳入——新建实例没有机制驱动首拍会永久 idle
        #（article-4 实证）；首拍后引擎置 running，之后按原语义轮询。
        # 20260927：白名单 → 黑名单（非终态一律推进，防非法状态值静默冻结）。
        if str(st.get("status")) not in TERMINAL_STATUSES and st.get("instance"):
            # G-2/031 守卫：t-* 前缀 = 测试实例（pytest 残留/冒烟），心跳永不驱动
            if st["instance"].startswith("t-") or st["instance"].startswith("t_"):
                continue
            out.append(st["instance"])
    return out


def _advance(instance: str) -> str | None:
    try:
        # Z5/031：显式 --mode auto = 跟随实例 state.mode（live 不降级、不冲突拒跑）
        r = subprocess.run(
            [sys.executable, str(ENGINE_PY), "--instance", instance, "--mode", "auto"],
            capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return f"[ww:{instance}] 心跳超时"
    if r.returncode != 0:
        return f"[ww:{instance}] 心跳异常 exit={r.returncode}: {r.stderr[-200:]}"
    out = r.stdout.strip()
    if not out:
        return None
    # 纯等待静默；拍板点/escalate 必投递——但同一拍板点只投一次（0919 去重：
    # gate+instance+iter 做 key，state 落 sandbox 文件，已播过的组合不再刷屏。
    # 状态变化（iter 前进/拍板落地/新 gate）自然产生新 key → 会再投）。
    is_wait = ("等待" in out or "⏳" in out) and "拍板点" not in out and "🚨" not in out
    if is_wait:
        return None
    # 拍板点/escalate 幂等去重
    import hashlib as _hl, json as _json
    seen_f = PROJECT_ROOT / "scripts" / ".heartbeat_seen.json"
    key = _hl.sha256(out.encode("utf-8")).hexdigest()[:16]
    seen = {}
    try:
        seen = _json.loads(seen_f.read_text(encoding="utf-8"))
    except Exception:
        pass
    # 同 key 24h 内只投一次；超 24h 再投（user 可能真的忘了）
    import time as _time
    now = _time.time()
    if key in seen and now - seen[key] < 86400:
        return None
    seen[key] = now
    # 只留最近 64 条
    if len(seen) > 64:
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-64:])
    try:
        seen_f.write_text(_json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 去重失败不阻塞播报（fail-open）
    return out


def _preflight() -> list[str]:
    """启动前自检：路径类故障必须响亮（stdout 非空 → cron 必投递），不得静默空转。"""
    errs = []
    if not ENGINE_PY.is_file():
        errs.append(f"引擎脚本不存在: {ENGINE_PY}（项目根解析={PROJECT_ROOT}）")
    if not INSTANCES_DIR.is_dir():
        errs.append(f"实例目录不存在: {INSTANCES_DIR}（心跳将无法发现任何实例）")
    return errs


def main() -> int:
    errs = _preflight()
    if errs:
        print("【we-write 引擎心跳 · 配置错误：心跳不会推进任何实例】")
        for e in errs:
            print(f"  ❌ {e}")
        print("  提示: 检查 scripts/ww_heartbeat_wrapper.py 的项目根锚点"
              "（env WW_PROJECT_ROOT / __file__ / 内建绝对锚点）与部署副本位置")
        # 返回 0：保证 stdout 原样投递（rc 非零可能只记错误而丢弃输出）
        return 0
    # 生命周期耦合（2026-10-10 任务书 C）：先对齐心跳开关，再推进。
    # CRON_COUPLING_DISABLE=1（测试回归/紧急人工关断）时本段整体跳过。
    active = _discover_running_instances()
    coupling = None
    if cron_coupling is not None and not cron_coupling.disabled():
        try:
            coupling = cron_coupling.reconcile()
        except Exception as e:  # noqa: BLE001 — 异常隔离：耦合失败不拦心跳推进
            cron_coupling.log_error("ww wrapper reconcile", e)
    if not active:
        # 自停告知（有输出=会投递，属正常告知非错误）：reconcile 成功时 job 已
        # paused，本行只会投递一次；仅当 reconcile 也失败（hermes 不可用）才会
        # 每拍重复——那本身就是要被人看见的故障信号。
        job_id = getattr(cron_coupling, "COUPLED_JOB_ID", "?") if cron_coupling else "?"
        print(f"[coupling] 无活动实例，心跳已自停（job={job_id}，reconcile={coupling}）")
        return 0
    reports = []
    for inst in active:
        out = _advance(inst)
        if out:
            reports.append(out)
    if not reports:
        return 0
    print("【we-write 引擎心跳】")
    for r in reports:
        print(f"  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
