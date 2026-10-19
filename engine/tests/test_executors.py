"""T3/T7 执行器框架分派一致性（D3 收敛：自仓库根 tests/test_executors.py 并入）。

run_step() 分派结果必须与 engine.run() 内部分派逐类型一致（回归防两处分派漂移）。

B2 两拍语义在本文件**无需补拍**：用例对照的是同一份处理器代码的两条调用路径
（executors.run_step vs engine.run() 内部的类型 dict 分派），B2 变更同时作用于两侧，
"next/log 相等"的断言意图不受影响（dispatch 类两侧同为 next=None 留步）。
（并入时删去原文件里一行永不生效的死代码：`engine.WeWriteEngine.run.__wrapped__`
  ——run 无 wrapper，该表达式恒为 None 且从未被断言。）
"""
from __future__ import annotations

import pytest

import engine
import executors


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    """隔离实例目录 + mock 一切 kanban 写操作（不触真实板）。"""
    e = engine.WeWriteEngine("exec-test")
    e.instance_path = tmp_path / "exec-test.json"
    monkeypatch.setattr(engine, "INSTANCES_DIR", tmp_path)
    monkeypatch.setattr(engine.WeWriteEngine, "_create_card",
                        lambda self, sid, step, it, body_suffix="": f"t_{sid}")
    monkeypatch.setattr(engine.WeWriteEngine, "_resolve_step_card",
                        lambda self, sid: None)
    monkeypatch.setattr(engine.WeWriteEngine, "_do_done",
                        lambda self, step, state: state.update(status="completed")
                        or {"next": None, "log": "🎉 完成(测试)"})
    return e


@pytest.mark.parametrize("step_id", [
    "ww_topic", "ww_mat_group", "ww_mat_barrier", "ww_done"])
def test_run_step_matches_internal_dispatch(eng, step_id):
    """run_step 分派 == engine 内部分派（同一状态、同一 log）。"""
    step = next(s for s in eng.template["steps"] if s["id"] == step_id)
    state_a = {"instance": "exec-test", "step": step_id, "iteration": 1,
               "history": [], "status": "running"}
    state_b = dict(state_a, history=[], iteration=1)
    res_a = executors.run_step(eng, step, state_a)
    t = step["type"]
    internal = {
        "dispatch": eng._do_dispatch,
        "dispatch_group": eng._do_dispatch_group,
        "wait": eng._do_wait,
        "decision": eng._do_decision,
        "done": eng._do_done,
    }[t](step, state_b)
    assert res_a["next"] == internal["next"]
    assert res_a["log"] == internal["log"]


def test_run_step_unknown_type(eng):
    with pytest.raises(ValueError):
        executors.run_step(eng, {"id": "x", "type": "mystery"}, {})


def test_lib_eval_recorded():
    assert executors.EXECUTOR_LIB_EVAL["decision"] in ("no-extract", "extract")


def test_artifact_globs_shape():
    g = executors.artifact_globs("D:/x/exchange", ["ww-mat-news", "ww-mat-law"])
    assert g == ["D:/x/exchange/artifacts/*-ww-mat-news*.md",
                 "D:/x/exchange/artifacts/*-ww-mat-law*.md"]
