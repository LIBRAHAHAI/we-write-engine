"""test_cron_coupling.py — 引擎心跳生命周期耦合单测（2026-10-10 任务书）。

全部 mock hermes 子进程（_run_hermes），绝不触达真实 cron job；
reconcile_silent 的禁用开关（CRON_COUPLING_DISABLE，conftest 已置 1）
在需要实测的用例内临时移除（monkeypatch 自动还原）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import cron_coupling as cc  # noqa: E402


class FakeHermes:
    """替身 _run_hermes：记账调用并模拟 job 状态转移。"""

    def __init__(self, state: str = "paused"):
        self.state = state
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(tuple(args))
        if args == ("cron", "list", "--all"):
            return f"  {cc.COUPLED_JOB_ID} [{self.state}]\n    Name: fake\n"
        if args == ("cron", "resume", cc.COUPLED_JOB_ID):
            self.state = "active"
            return "ok"
        if args == ("cron", "pause", cc.COUPLED_JOB_ID):
            self.state = "paused"
            return "ok"
        raise AssertionError(f"意外调用: {args}")


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "instances"
    d.mkdir()
    monkeypatch.setattr(cc, "_instances_dir", lambda: d)
    return d


def _write_inst(d: Path, name: str, status: str) -> None:
    (d / f"{name}.json").write_text(
        json.dumps({"instance": name, "status": status}), encoding="utf-8")


def test_reconcile_on_resumes(sandbox, monkeypatch):
    _write_inst(sandbox, "a1", "running")
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "on"
    assert fake.state == "active"
    assert ("cron", "resume", cc.COUPLED_JOB_ID) in fake.calls


def test_reconcile_idempotent_when_on(sandbox, monkeypatch):
    _write_inst(sandbox, "a1", "running")
    fake = FakeHermes("active")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "unchanged"
    assert fake.calls == [("cron", "list", "--all")]  # 未做任何 resume/pause


def test_reconcile_off_pauses_when_no_active(sandbox, monkeypatch):
    _write_inst(sandbox, "a1", "completed")  # 终态实例不算活动
    fake = FakeHermes("active")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "off"
    assert fake.state == "paused"


def test_reconcile_idempotent_when_off(sandbox, monkeypatch):
    fake = FakeHermes("paused")  # 空目录 + 已 paused
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "unchanged"
    assert fake.calls == [("cron", "list", "--all")]


def test_active_predicate_is_terminal_blacklist(sandbox, monkeypatch):
    """ww 口径：非终态（含非法状态值）一律算活动——与 wrapper 黑名单同源。"""
    _write_inst(sandbox, "a1", "weird-status")
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "on"


def test_test_prefix_instances_not_active(sandbox, monkeypatch):
    """只有 t-*/t_* 测试实例 → 不算活动（wrapper 不推进它们）→ 心跳应停。"""
    _write_inst(sandbox, "t-x", "running")
    fake = FakeHermes("active")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "off"
    assert fake.state == "paused"


def test_reconcile_silent_swallows_hermes_failure(sandbox, monkeypatch):
    _write_inst(sandbox, "a1", "running")
    monkeypatch.delenv("CRON_COUPLING_DISABLE", raising=False)

    def boom(*args):
        raise FileNotFoundError("hermes 不存在")

    monkeypatch.setattr(cc, "_run_hermes", boom)
    cc.reconcile_silent()  # 不得上抛（异常隔离红线）


def test_reconcile_silent_respects_disable_switch(sandbox, monkeypatch):
    monkeypatch.setenv("CRON_COUPLING_DISABLE", "1")
    called: list[int] = []
    monkeypatch.setattr(cc, "reconcile", lambda: called.append(1) or "on")
    cc.reconcile_silent()
    assert called == []  # 开关置位 → 完全跳过


def test_job_state_parses_all_flag_output(monkeypatch):
    monkeypatch.setattr(
        cc, "_run_hermes",
        lambda *a: f"  other1234abcd [active]\n  {cc.COUPLED_JOB_ID} [paused]\n")
    assert cc.job_state() == "paused"


def test_job_state_raises_when_job_missing(monkeypatch):
    monkeypatch.setattr(cc, "_run_hermes", lambda *a: "  other1234abcd [active]\n")
    with pytest.raises(RuntimeError, match="找不到 job"):
        cc.job_state()


def test_engine_hooks_creation_and_terminal(tmp_path, monkeypatch):
    """端到端（hermes mock）：engine 建档钩子 resume、终态落盘钩子 pause。"""
    import engine as ww_engine

    d = tmp_path / "instances"
    d.mkdir()
    monkeypatch.setattr(ww_engine, "INSTANCES_DIR", d)
    monkeypatch.setattr(cc, "_instances_dir", lambda: d)
    monkeypatch.delenv("CRON_COUPLING_DISABLE", raising=False)
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)

    eng = ww_engine.WeWriteEngine("cc-hook-test", mode="test")
    eng.instance_path = d / "cc-hook-test.json"
    eng.status()  # _load_state 首跑建档 → 挂钩应 resume 心跳
    assert fake.state == "active"

    st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
    st["status"] = "completed"
    eng._save_state(st)  # 终态落盘 → 挂钩应 pause 心跳
    assert fake.state == "paused"
