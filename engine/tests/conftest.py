"""conftest.py — 测试根路径注入（D3 收敛后的唯一测试树入口，2026-09-11）。

收敛前仓库根另有一棵 tests/，其 conftest 注入 <repo>/engine。收敛后本文件承担同样
职责，并额外注入 <repo>/scripts（test_resume.py 要 import ww_heartbeat_wrapper）。

⚠️ 实弹纪律：本仓 kanban DB 是**生产板**且本机 dispatcher 常驻。任何走 dispatch
路径的用例必须 mock `_resolve_step_card` + `_create_card`（或整体重定向
HERMES_KANBAN_DB 到一次性快照），否则一次 pytest 就会在活跃板留下 ready 卡并
spawn 真 worker。新增用例前先确认这一点。
"""
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent

for _p in (ENGINE_DIR, REPO_ROOT / "scripts"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

# ---- G-1/G-2/031：测试树落盘重定向（在 engine import 前生效）----
# EXCHANGE_DIR / INSTANCES_DIR 均 env 可覆盖：测试的落盘（mock report、收官简报、
# 屏障用例临时产物、实例 JSON）全部进一次性 temp，交付树零写入。
# wrapper 守卫（G-2）：t-* 前缀测试实例即使残留也不被心跳误驱动。
import os as _os
import tempfile as _tempfile

_TEST_SANDBOX = _tempfile.mkdtemp(prefix="ww_test_sandbox_")
_os.environ.setdefault("WW_EXCHANGE_DIR", _os.path.join(_TEST_SANDBOX, "exchange"))
_os.environ.setdefault("WW_INSTANCES_DIR", _os.path.join(_TEST_SANDBOX, "instances"))
# 066 §8：成果归档双保险——测试实例（t- 前缀）本身已关断归档（archive.archive_enabled），
# 此处再把成果区根重定向进一次性 temp，任何路径都不写真实 Wechat-project/。
_os.environ.setdefault("WW_WECHAT_PROJECT_DIR", _os.path.join(_TEST_SANDBOX, "wechat-project"))
# 生命周期耦合（2026-10-10）：engine._save_state / wrapper main 的 reconcile 挂钩
# 在回归中一律静默跳过——pytest 绝不触达真实 cron job（不得 pause 真实心跳）。
_os.environ.setdefault("CRON_COUPLING_DISABLE", "1")


# ---------------------------------------------------------------------------
# 2026-09-14 交付树清理后：真实例用例改读「归档基准」（方案A）
#
# exchange/artifacts 的生成性文件已清空归档到 Wechat-project/2026-09-13/01-jiagecike/
# （文件名与原 artifacts 完全一致）。test_ww_panel 的 realdata 用例与 test_archive 的
# 真数据用例原先直读真实 artifacts，清理后断言被打穿——改为把归档映射回填进一次性
# temp 只读副本，行为与清理前逐字节等价。
# ⚠ 铁律：归档目录只读，任何测试代码禁止向 Wechat-project 写入。
# ---------------------------------------------------------------------------
import shutil as _shutil
import unittest as _unittest

import pytest as _pytest

# 2026-09-26 三层布局迁移后新路径（<模板名>/<日期>/<NN-slug>）
ARCHIVE_BASE = Path("Wechat-project/hot-topic-commentary/2026-09-13/01-jiagecike")

# 回填白名单：编号产物 0*-*.md / 0*-*.html、封面 cover-*.png、排版散件 *-formatted.html、
# 配图目录 imgs-*/ 整目录（sources.json + png）。归档自身的标配件（meta.yaml、imgs/）
# 不是 exchange 源形态，不回填。
_ARCHIVE_FILE_GLOBS = ("0*-*.md", "0*-*.html", "cover-*.png", "*-formatted.html")
_ARCHIVE_DIR_GLOBS = ("imgs-*",)


def archive_baseline_available() -> bool:
    """归档基准在位判定（未来归档迁移后用例自动 skip，不假失败）。"""
    return ARCHIVE_BASE.is_dir()


def backfill_artifacts_from_archive(dest: Path) -> Path:
    """把归档基准映射回填为 temp 下的 artifacts/（原样拷贝，只读归档，绝不写回）。

    归档缺失时抛 unittest.SkipTest——pytest 与 unittest 两种 runner 下都落成 skip
    而非 fail（等价于 pytest.skipif 守卫，且对 unittest 风格用例同样生效）。
    """
    if not archive_baseline_available():
        raise _unittest.SkipTest(
            f"归档基准不存在: {ARCHIVE_BASE}（已迁移？依赖其回填的用例整体 skip）")
    dest.mkdir(parents=True, exist_ok=True)
    for g in _ARCHIVE_FILE_GLOBS:
        for p in ARCHIVE_BASE.glob(g):
            if p.is_file():
                _shutil.copy2(p, dest / p.name)
    for g in _ARCHIVE_DIR_GLOBS:
        for p in ARCHIVE_BASE.glob(g):
            if p.is_dir():
                _shutil.copytree(p, dest / p.name, dirs_exist_ok=True)
    return dest


def build_archived_exchange(dest: Path, real_exchange: Path) -> Path:
    """在 temp 重建 exchange 骨架：artifacts = 归档回填；verdicts 未被清理 →
    从真实树原样拷贝；reports / drafts / notify-ack 建空骨架（面板只读不依赖其内容）。"""
    backfill_artifacts_from_archive(dest / "artifacts")
    for sub in ("verdicts", "reports", "drafts", "notify-ack"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
        src_sub = real_exchange / sub
        if src_sub.is_dir():
            for p in src_sub.iterdir():
                if p.is_file():
                    _shutil.copy2(p, dest / sub / p.name)
    return dest


@_pytest.fixture
def archived_exchange(tmp_path) -> Path:
    """realdata 用例的 exchange 基准：归档 artifacts 回填 + 真实 verdicts 拷贝（全 temp）。"""
    return build_archived_exchange(tmp_path / "exchange", REPO_ROOT / "exchange")
