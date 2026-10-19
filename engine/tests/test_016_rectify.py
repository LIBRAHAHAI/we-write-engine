# -*- coding: utf-8 -*-
"""test_016_rectify.py — 016 批次整改回归（2026-09-29 任务书批次一 #3/#5/#6 + 批次三 #11）。

覆盖（验收标准逐条）：
  - #5 D1 返工链 pop：verdict_reject / verdict_gate 打回时，返工靶点起整链
    派发标记撤销（含 polish），双审打回场景返工稿能进终稿取件；
  - #6 escalate 警示清除：同 step 后续 verdict_pass 覆盖旧警示，不再上门3；
  - #3 verdict 正文绑定：「字段齐但正文空壳 → 拒」（consume 路径，gate 路径
    在 test_m2_verdict_consume 覆盖）；
  - #11 B|意见：拍板 `B|说明` 落 pending_rework_note + history 留痕；纯 B
    维持选项表原语义。

测试树单源铁律：本文件在 engine/tests/ 内；全部落一次性 temp，零活跃板写入
（conftest 已重定向 EXCHANGE/INSTANCES/WECHAT 三根）。
"""
import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import engine as eng_mod  # noqa: E402


def _state():
    return {"instance": "t", "step": "x", "iteration": 0,
            "history": [], "status": "running", "dispatched": {}}


class TestD1ChainPop(unittest.TestCase):
    """#5 D1 修复：打回范围扩到上游整链（含 polish 类步骤）。"""

    def setUp(self):
        self.eng = eng_mod.WeWriteEngine("t-016-d1")

    def test_consume_reject_pops_polish_chain(self):
        """factcheck 打回 ww_write → ww_write/ww_polish/ww_headline/双审… 整链标记撤。"""
        step = self.eng._step_by_id("ww_factcheck")
        st = _state()
        # 模拟首轮已派发的整链标记（polish 在 factcheck 之前已交付——D1 实证场景）
        st["dispatched"] = {"ww_write": 1, "ww_polish": 2, "ww_factcheck": 3,
                            "ww_headline": 4, "ww_topic": 1}
        # 测试隔离：产物解析指向 None（字段缺失 → 打回），不读真实 exchange
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: None
        try:
            vc = step["verdict_consume"]  # on_reject: ww_write
            res = self.eng._consume_verdict(
                "ww_factcheck", step["artifact"],
                {**vc, "pass_value": "approved"}, st, step)
        finally:
            eng_mod._resolve_artifact = orig
        self.assertEqual(res["next"], "ww_write")
        for gone in ("ww_write", "ww_polish", "ww_factcheck", "ww_headline"):
            self.assertNotIn(gone, st["dispatched"], gone)
        # 靶点上游（不回流重跑）的标记保留，不触发无谓重做
        self.assertIn("ww_topic", st["dispatched"])

    def test_gate_reject_pops_polish_mark(self):
        """双审打回场景：verdict_gate changes_requested → ww_polish 标记撤销。"""
        step = self.eng._step_by_id("ww_review_barrier")
        st = _state()
        st["step"] = "ww_review_barrier"
        st["dispatched"] = {"ww_polish": 2, "ww_headline": 3}
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: Path("fake.md")
        orig_read = eng_mod.read_frontmatter_field
        vals = iter(["changes_requested", "changes_requested"])
        eng_mod.read_frontmatter_field = lambda p, f: next(vals)
        try:
            res = self.eng._do_wait(step, st)
        finally:
            eng_mod._resolve_artifact = orig
            eng_mod.read_frontmatter_field = orig_read
        self.assertEqual(res["next"], "ww_polish")
        self.assertNotIn("ww_polish", st["dispatched"])
        self.assertNotIn("ww_headline", st["dispatched"])
        self.assertTrue(any(h["action"] == "verdict_gate_reject" for h in st["history"]))


class TestEscalationWarningCleared(unittest.TestCase):
    """#6：警示来源轮次之后同 step 有 verdict_pass → 不再上门3。"""

    def _eng_with_hist(self, name, hist):
        eng = eng_mod.WeWriteEngine(name)
        st = {"instance": name, "step": "ww_gate_final", "iteration": 3,
              "history": hist, "status": "running", "dispatched": {}}
        eng.instance_path = eng_mod.INSTANCES_DIR / f"{name}.json"
        eng._save_state(st)
        return eng

    ESC = {"step": "ww_factcheck", "action": "verdict_escalate",
           "detail": "factcheck_result=FAIL", "log": "⚠🚨 连续超限"}

    def test_pass_after_escalate_suppresses_warning(self):
        """escalate → 后轮同 step verdict_pass：警示清除（article-005 门3 假警示实证）。"""
        eng = self._eng_with_hist(
            "t-016-esc1",
            [self.ESC,
             {"step": "ww_factcheck", "action": "verdict_pass",
              "detail": "factcheck_result=approved"}])
        self.assertEqual(eng._escalation_warnings(), [])

    def test_escalate_without_pass_still_warns(self):
        """无后续通过 → 警示保留（终审权通道不被误清）。"""
        eng = self._eng_with_hist("t-016-esc2", [self.ESC])
        warns = eng._escalation_warnings()
        self.assertEqual(len(warns), 1)
        self.assertIn("事实核查未全清", warns[0])

    def test_pass_of_other_step_does_not_suppress(self):
        """他步的 verdict_pass 不清本步警示（按 step 匹配，宁误报不漏报）。"""
        eng = self._eng_with_hist(
            "t-016-esc3",
            [self.ESC,
             {"step": "ww_logic_review", "action": "verdict_pass",
              "detail": "logic_result=approved"}])
        self.assertEqual(len(eng._escalation_warnings()), 1)


class TestVerdictDeliverableGate(unittest.TestCase):
    """#3：verdict 消费绑正文——字段齐但正文空壳 → 拒（consume 路径）。"""

    def setUp(self):
        self.eng = eng_mod.WeWriteEngine("t-016-deliv")
        self.step = self.eng._step_by_id("ww_logic_review")
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _patch(self, path):
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: path
        self.addCleanup(setattr, eng_mod, "_resolve_artifact", orig)

    def _write(self, body, status="done", field="logic_result", val="approved"):
        import tempfile
        p = Path(self.tmp.name) / "001-x-ww-logic-verdict.md"
        p.write_text(f"---\nauthor: hermes\n{field}: {val}\nstatus: {status}\n---\n{body}",
                     encoding="utf-8")
        return p

    def test_shell_body_with_approved_field_rejects(self):
        """141-duanmu 形态：字段 approved + 正文空壳 → 拒（不再读 frontmatter 即放行）。"""
        p = self._write("\n# 结论\n\n（正文空壳占位）\n")
        self._patch(p)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_logic_review", "glob", self.step["verdict_consume"], st, self.step)
        self.assertEqual(res["next"], "ww_polish")  # on_reject
        self.assertTrue(any(h["action"] == "verdict_reject" for h in st["history"]))

    def test_status_not_done_rejects(self):
        """字段 approved + 正文足量但 status=draft（未定稿）→ 拒。"""
        p = self._write("\n结论：" + "逻辑链完整，论据充分。" * 60 + "\n", status="draft")
        self._patch(p)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_logic_review", "glob", self.step["verdict_consume"], st, self.step)
        self.assertEqual(res["next"], "ww_polish")

    def test_real_done_verdict_passes_and_records(self):
        """正文足量 + status=done + 字段 approved → 放行，且 verdict_pass 留痕（#6）。"""
        p = self._write("\n结论：" + "逻辑链完整，论据充分。" * 60 + "\n")
        self._patch(p)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_logic_review", "glob", self.step["verdict_consume"], st, self.step)
        self.assertIsNone(res)
        self.assertTrue(any(h["action"] == "verdict_pass" for h in st["history"]))


class TestBNoteDecision(unittest.TestCase):
    """#11：拍板 `B|说明` 复用 C 机制（pending_rework_note + history 留痕）。"""

    def setUp(self):
        self.eng = eng_mod.WeWriteEngine("t-016-bnote")

    def _decide(self, decision):
        name = f"t-016-bnote-{abs(hash(decision)) % 10000}"
        (eng_mod.INSTANCES_DIR / f"{name}.decision").write_text(decision, encoding="utf-8")
        eng = eng_mod.WeWriteEngine(name)
        st = {"instance": name, "step": "ww_gate_final", "iteration": 1,
              "history": [], "status": "running",
              "dispatched": {"ww_write": 1, "ww_polish": 1}}
        eng.instance_path = eng_mod.INSTANCES_DIR / f"{name}.json"
        eng._save_state(st)
        step = eng._step_by_id("ww_gate_final")
        return eng._do_decision(step, st), st

    def test_b_note_lands_pending_rework_note(self):
        """B|说明 → pending_rework_note 落 state，返工卡重开时意见注入卡 comment。"""
        res, st = self._decide("B|第四节论据与主旨错位，驳回重做")
        self.assertEqual(res["next"], "ww_polish")  # rework_target 优先（与 C 路径同机制）
        self.assertEqual(st["pending_rework_note"]["gate"], "ww_gate_final")
        self.assertIn("论据与主旨错位", st["pending_rework_note"]["note"])
        entry = [h for h in st["history"]
                 if h.get("action") == "decision" and h.get("decision") == "B"]
        self.assertEqual(len(entry), 1)
        self.assertIn("论据与主旨错位", entry[0]["note"])
        self.assertNotIn("ww_polish", st["dispatched"])  # 返工靶点标记撤销

    def test_plain_b_keeps_option_semantics(self):
        """无意见纯 B 维持选项表原语义（B → 选项表 next，不落 pending note）。"""
        res, st = self._decide("B")
        step = self.eng._step_by_id("ww_gate_final")
        b_next = [o["next"] for o in step["options"]
                  if str(o["key"]).upper() == "B"][0]
        self.assertEqual(res["next"], b_next)
        self.assertFalse(st.get("pending_rework_note"))
        self.assertTrue(any(h.get("decision") == "B" and h.get("action") == "decision"
                            for h in st["history"]))


if __name__ == "__main__":
    unittest.main()
