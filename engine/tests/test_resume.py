"""T8 断点恢复 + 心跳 wrapper 扫描（D3 收敛：自仓库根 tests/test_resume.py 并入）。

kill 重启语义：状态全部落盘在实例 JSON，新 WeWriteEngine 对象即"重启"。
心跳 wrapper：running 实例被扫描推进，非 running 被跳过。

2026-09-11 并入时两处必要适配（断言意图均未放宽）：

1. `test_resume_after_crash_full_chain` 按 B2 两拍语义补第二拍——原用例断言
   "建卡即推进"（e1.run() 一次就落 ww_gate_topic），B2 修复后 dispatch 建卡留本步、
   卡 done 才推进。补拍后仍验证原意图：状态只依赖 JSON，新对象续跑一致。

2. `test_wrapper_silence_on_pure_wait` 的触发方式改为**撕裂实例 JSON**。
   原用例调 `_advance("nonexist")`，它的绿灯其实**依赖遗留文件
   engine/instances/nonexist.json 恰好停在 decision 门**（不建卡、输出含"拍板点"
   → 不被静默 → 返回非 None）。该遗留文件本次按 017 §三.3 删除后，用例会从
   ww_topic 走真 dispatch：`hermes kanban create` 在**活跃板**建实卡（本机 dispatcher
   随即 claim + spawn 真 worker），而 B2 的留步日志含"等待"会被 wrapper 判为纯等待
   静默 → 返回 None → 断言反而变红。即"删遗留文件"与"保持全绿"在原写法下互斥。
   改用撕裂 JSON 后：engine.py 在 `_load_state()` 即抛错非零退出，**全程零 kanban
   接触**，且更精确地验证原意图「异常实例要报错不静默」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(_REPO_ROOT / "engine"), str(_REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ww_heartbeat_wrapper as hb  # noqa: E402


def test_wrapper_discovers_running_only(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "INSTANCES_DIR", tmp_path)
    (tmp_path / "a.json").write_text(json.dumps(
        {"instance": "a", "status": "running", "step": "ww_topic"}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(
        {"instance": "b", "status": "completed"}), encoding="utf-8")
    (tmp_path / "c.json").write_text("{broken", encoding="utf-8")  # 撕裂实例容忍
    assert hb._discover_running_instances() == ["a"]


def test_wrapper_silence_on_pure_wait():
    """异常实例要报错不静默（`_advance` 必须返回非 None 的报告串）。

    触发方式见模块 docstring 第 2 条：撕裂 JSON 让 engine.py 非零退出，零 kanban 接触。
    """
    name = "wwtest-torn-resume"
    torn = hb.INSTANCES_DIR / f"{name}.json"
    torn.parent.mkdir(parents=True, exist_ok=True)
    torn.write_text("{broken", encoding="utf-8")
    try:
        # 撕裂实例不得被 _discover_running_instances 当成 running（否则心跳会反复撞它）
        assert name not in hb._discover_running_instances()
        out = hb._advance(name)
        assert out is not None, "异常实例被静默吞掉（心跳会漏报）"
        assert "心跳异常" in out, f"应报心跳异常，实际: {out!r}"
    finally:
        torn.unlink(missing_ok=True)


def test_resume_after_crash_full_chain(tmp_path, monkeypatch):
    """全链断点恢复：推进多步 → 每一步状态只依赖 JSON → 新对象续跑一致。"""
    import engine as eng_mod
    monkeypatch.setattr(eng_mod, "INSTANCES_DIR", tmp_path)
    monkeypatch.setattr(eng_mod.WeWriteEngine, "_create_card",
                        lambda self, sid, step, it, body_suffix="": f"t_{sid}")
    monkeypatch.setattr(eng_mod.WeWriteEngine, "_do_done",
                        lambda self, step, state: state.update(status="completed")
                        or {"next": None, "log": "🎉 完成(测试)"})
    state_file = tmp_path / "crash-test.json"

    e1 = eng_mod.WeWriteEngine("crash-test")
    e1.instance_path = state_file
    # 拍1：ww_topic 建卡 → B2 留本步
    with mock.patch.object(eng_mod.WeWriteEngine, "_resolve_step_card", return_value=None):
        e1.run()
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert st["step"] == "ww_topic", f"B2：建卡后应留本步，实际 {st['step']}"

    # "crash"：只用 JSON 重建引擎；拍2：卡 done → 推进门1
    e2 = eng_mod.WeWriteEngine("crash-test")
    e2.instance_path = state_file
    with mock.patch.object(eng_mod.WeWriteEngine, "_resolve_step_card",
                           return_value={"id": "t_ww_topic", "status": "done"}):
        e2.run()
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert st["step"] == "ww_gate_topic"

    # 拍板后续跑（decision 门不涉卡，单拍即推进）
    (tmp_path / "crash-test.decision").write_text("1", encoding="utf-8")
    out = e2.run()
    assert "ww_outline" in out
