# -*- coding: utf-8 -*-
"""A2（20260920）标记撤销方案验收：done 卡误重开根治。

任务书验收：
  - test_reject_reopens_done_card_exactly_once 仍通过（在 test_dispatch_wait.py）；
  - test_no_reopen_after_normal_advance：推进过 ww_factcheck（history 有
    dispatch_done）后 step 到 ww_headline，headline done 卡 → dispatch_done
    而非 dispatch_reopen；
  - test_verdict_reject_pops_dispatch_mark：verdict 拒绝后该步标记已移除，
    打回目标步下一拍走重开；
  - e2e 空跑（mock 全部）连续推进 5 步断言 0 次 dispatch_reopen。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
from engine import WeWriteEngine

PIPELINE = Path(__file__).parent.parent / "config" / "templates" / "hot-topic-commentary.yaml"

APPROVED_MD = ("---\nauthor: xuanwu\nfactcheck_result: approved\nstatus: done\n---\n\n"
               "结论：四判定全过，引用页均含所引数据。" * 20 + "\n")  # 016 #3/#2 实体门


class DispatchMarksBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_inst = ww_engine.INSTANCES_DIR
        self._orig_exch = ww_engine.EXCHANGE_DIR
        ww_engine.INSTANCES_DIR = self.tmp / "instances"
        ww_engine.EXCHANGE_DIR = self.tmp / "exchange"
        (ww_engine.EXCHANGE_DIR / "artifacts").mkdir(parents=True)
        ww_engine.INSTANCES_DIR.mkdir(parents=True)

    def tearDown(self):
        ww_engine.INSTANCES_DIR = self._orig_inst
        ww_engine.EXCHANGE_DIR = self._orig_exch

    def _eng(self, name="t-marks") -> WeWriteEngine:
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = name
        eng.mode = "test"
        eng.template = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        eng.instance_path = ww_engine.INSTANCES_DIR / f"{name}.json"
        return eng

    def _state(self, eng, step, iteration=0, dispatched=None, history=None):
        st = {"instance": eng.instance, "step": step, "iteration": iteration,
              "history": history or [], "status": "running",
              "dispatched": dispatched or {}, "mode": "test"}
        eng._save_state(st)
        return st

    def _load(self, eng):
        return __import__("json").loads(eng.instance_path.read_text(encoding="utf-8"))

    def _approved_artifact(self):
        p = ww_engine.EXCHANGE_DIR / "artifacts" / "001-xuanwu-ww-factcheck-report.md"
        p.write_text(APPROVED_MD, encoding="utf-8")
        orig = ww_engine._resolve_artifact
        patcher = mock.patch.object(ww_engine, "_resolve_artifact",
                                    lambda pattern: p)
        return patcher


class TestNoReopenAfterNormalAdvance(DispatchMarksBase):
    def test_no_reopen_after_normal_advance(self):
        """推进过 factcheck 后到 headline：done 卡正常放行（dispatch_done），不重开。"""
        eng = self._eng("t-no-reopen")
        marks = {"ww_topic": 1, "ww_mat_news": 2, "ww_mat_history": 2,
                 "ww_mat_classic": 2, "ww_mat_law": 2, "ww_mat_econ": 2,
                 "ww_mat_select": 3, "ww_write": 4, "ww_polish": 5,
                 "ww_factcheck": 6, "ww_headline": 7}
        hist = [{"step": "ww_factcheck", "iteration": 6,
                 "action": "dispatch_done", "log": "dispatch ww_factcheck: done → 推进"}]
        self._state(eng, "ww_headline", iteration=7,
                    dispatched=dict(marks), history=hist)
        done = {"id": "t_head", "status": "done"}
        with mock.patch.object(eng, "_resolve_step_card", return_value=done), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card") as mk_reopen:
            out = eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_dual_review")
        self.assertIn("done → 推进", out)
        actions = [h["action"] for h in st["history"]]
        self.assertIn("dispatch_done", actions)
        self.assertNotIn("dispatch_reopen", actions)
        mk_reopen.assert_not_called()
        self.assertEqual(st["dispatched"]["ww_headline"], 7)  # 标记保持


class TestVerdictRejectPopsDispatchMark(DispatchMarksBase):
    def test_verdict_reject_pops_dispatch_mark(self):
        """verdict 拒绝 → 本步与打回目标步标记移除；下一拍打回目标步走重开。"""
        eng = self._eng("t-reject-pop")
        st = self._state(eng, "ww_factcheck", iteration=6,
                         dispatched={"ww_write": 4, "ww_polish": 5,
                                     "ww_factcheck": 6})
        art = self._approved_artifact()
        bad = ("---\nauthor: xuanwu\nfactcheck_result: changes_requested\n"
               "---\n\n# 报告\n")
        fp = ww_engine.EXCHANGE_DIR / "artifacts" / "001-xuanwu-ww-factcheck-report.md"
        fp.write_text(bad, encoding="utf-8")
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_factcheck")
        with mock.patch.object(ww_engine, "_resolve_artifact", lambda pattern: fp), \
             mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_fc", "status": "done"}):
            out = eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_write")
        self.assertNotIn("ww_factcheck", st["dispatched"])  # 本步标记已移除
        self.assertNotIn("ww_write", st["dispatched"])      # 打回目标步标记已移除
        # 016 #5（D1 修复）：打回范围扩到上游整链——polish 不再是「无关步」，
        # 其标记随链撤销（否则返工稿回流 ww_polish 时 done 卡不重开，终稿取旧稿）
        self.assertNotIn("ww_polish", st["dispatched"])
        # 下一拍：ww_write 的 done 卡（上一轮残留）→ 必须走重开
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_w", "status": "done"}), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=True) as mk_reopen:
            out2 = eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_write")  # 重开后留步
        self.assertIn("重开重做", out2)
        mk_reopen.assert_called_once()


class TestDecisionReworkPopsDispatchMark(DispatchMarksBase):
    def test_gate_c_rework_pops_rework_target_mark(self):
        """gate_final 拍 C|意见 → 返工靶点 ww_polish 标记撤销，done 卡下一拍重开。"""
        eng = self._eng("t-gate-c")
        self._state(eng, "ww_gate_final", iteration=12,
                    dispatched={"ww_polish": 5, "ww_write": 4, "ww_headline": 7})
        (ww_engine.INSTANCES_DIR / "t-gate-c.decision").write_text(
            "C|标题选候选2 + 按题改内容", encoding="utf-8")
        eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_polish")
        self.assertNotIn("ww_polish", st["dispatched"])
        # 下一拍 polish done 卡走重开
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_p", "status": "done"}), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=True) as mk_reopen:
            out = eng.run()
        self.assertIn("重开重做", out)
        mk_reopen.assert_called_once()

    def test_gate_topic_reselect_pops_topic_mark(self):
        """gate_topic 拍「重选」（非顺延导航）→ ww_topic 标记撤销。"""
        eng = self._eng("t-reselect")
        self._state(eng, "ww_gate_topic", iteration=2,
                    dispatched={"ww_topic": 1, "ww_outline": 2})
        (ww_engine.INSTANCES_DIR / "t-reselect.decision").write_text(
            "重选", encoding="utf-8")
        eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_topic")
        self.assertNotIn("ww_topic", st["dispatched"])

    def test_gate_accept_does_not_pop(self):
        """gate 拍 A（顺延导航）→ 不撤任何标记（正常推进语义）。"""
        eng = self._eng("t-accept")
        self._state(eng, "ww_gate_outline", iteration=3,
                    dispatched={"ww_outline": 2, "ww_mat_news": 3})
        (ww_engine.INSTANCES_DIR / "t-accept.decision").write_text(
            "A", encoding="utf-8")
        eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_mat_group")
        self.assertEqual(st["dispatched"], {"ww_outline": 2, "ww_mat_news": 3})


class TestE2EZeroReopen(DispatchMarksBase):
    def test_e2e_five_beats_zero_reopen(self):
        """e2e 空跑（mock 全部）：选材→写文→润色→事实核→标题→双审 连续推进，
        断言全程 0 次 dispatch_reopen（A2 收益直达验收）。"""
        eng = self._eng("t-e2e")
        marks = {"ww_topic": 1, "ww_mat_news": 2, "ww_mat_history": 2,
                 "ww_mat_classic": 2, "ww_mat_law": 2, "ww_mat_econ": 2,
                 "ww_mat_select": 3, "ww_write": 3, "ww_polish": 3,
                 "ww_factcheck": 3, "ww_headline": 3,
                 "ww_review_audit": 3, "ww_review_value": 3}
        self._state(eng, "ww_mat_select", iteration=3, dispatched=dict(marks))
        fp = ww_engine.EXCHANGE_DIR / "artifacts" / "001-xuanwu-ww-factcheck-report.md"
        fp.write_text(APPROVED_MD, encoding="utf-8")
        with mock.patch.object(ww_engine, "_resolve_artifact", lambda pattern: fp), \
             mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_any", "status": "done"}), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card") as mk_reopen:
            for _ in range(6):
                eng.run()
        st = self._load(eng)
        reopens = [h for h in st["history"] if h.get("action") == "dispatch_reopen"]
        self.assertEqual(reopens, [])
        mk_reopen.assert_not_called()
        self.assertEqual(st["step"], "ww_review_barrier")  # 5 步推进到位


if __name__ == "__main__":
    unittest.main()
