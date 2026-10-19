"""test_m2_verdict_consume.py — M2 行为级单测（013 Δ3/Δ5/Δ7/Δ8）。

覆盖：
  - Δ3 factcheck verdict_consume：approved 放行 / changes_requested 打回 ww_write /
    字段缺失视同打回 / 超 max_retry 带警示放行
  - Δ5 verdict_gate：屏障全齐后任一 changes_requested → 打回 ww_polish
  - Δ7 禁配：audit 步 minimax → fallback 绕行 + DENIED 日志；非 audit 不受限；
    fallback 全禁配拒派
  - Δ8 slug 同构校验：坏配置拒启 / 显式 slug 声明豁免 / registry 生成

测试树单源铁律：本文件在 engine/tests/ 内（禁止第二棵树）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import engine as eng_mod  # noqa: E402
import model_router  # noqa: E402
from config import slug_registry  # noqa: E402

FC_GLOB = "./exchange/artifacts/*-ww-factcheck-report*.md"


def _state():
    return {"instance": "t", "step": "ww_factcheck", "iteration": 0,
            "history": [], "status": "running", "dispatched": {}}


class TestFactcheckVerdictConsume(unittest.TestCase):
    def setUp(self):
        self.eng = eng_mod.WeWriteEngine("t-m2-fc")
        self.step = {"id": "ww_factcheck", "max_retry": 2,
                     "artifact": FC_GLOB,
                     "verdict_consume": {"field": "factcheck_result",
                                         "pass_value": "approved",
                                         "on_reject": "ww_write"}}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _patch_artifact(self, path: Path | None):
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = (lambda p: path) if path else (lambda p: None)
        self.addCleanup(setattr, eng_mod, "_resolve_artifact", orig)

    def test_approved_passes(self):
        # 016 #3：字段放行须过实体门——正文足量 + status=done
        p = Path(self.tmp.name) / "001-xuanwu-ww-factcheck-report.md"
        p.write_text("---\nauthor: xuanwu\nfactcheck_result: approved\nstatus: done\n---\n\n"
                     "结论：" + "四判定全过，引用页均含所引数据。" * 30 + "\n",
                     encoding="utf-8")
        self._patch_artifact(p)
        self.assertIsNone(self.eng._consume_verdict(
            "ww_factcheck", FC_GLOB, self.step["verdict_consume"], _state(), self.step))

    def test_shell_body_with_approved_field_rejects(self):
        """016 #3 验收用例：字段齐但正文空壳 → 拒（141-duanmu 形态）。"""
        p = Path(self.tmp.name) / "002-xuanwu-ww-factcheck-report.md"
        p.write_text("---\nauthor: xuanwu\nfactcheck_result: approved\nstatus: done\n---\n\n# 报告\n",
                     encoding="utf-8")
        self._patch_artifact(p)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_factcheck", FC_GLOB, self.step["verdict_consume"], st, self.step)
        self.assertEqual(res["next"], "ww_write")
        self.assertTrue(any(h["action"] == "verdict_reject" for h in st["history"]))

    def test_status_not_done_rejects(self):
        """016 #3：字段 approved 但 status 未 done → 视同未交付，打回。"""
        p = Path(self.tmp.name) / "003-xuanwu-ww-factcheck-report.md"
        p.write_text("---\nauthor: xuanwu\nfactcheck_result: approved\nstatus: draft\n---\n\n"
                     "结论：" + "四判定全过，引用页均含所引数据。" * 30 + "\n",
                     encoding="utf-8")
        self._patch_artifact(p)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_factcheck", FC_GLOB, self.step["verdict_consume"], st, self.step)
        self.assertEqual(res["next"], "ww_write")

    def test_changes_requested_rejects_to_write(self):
        p = Path(self.tmp.name) / "001-xuanwu-ww-factcheck-report.md"
        p.write_text("---\nauthor: xuanwu\nfactcheck_result: changes_requested\n---\n\n# 报告\n",
                     encoding="utf-8")
        self._patch_artifact(p)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_factcheck", FC_GLOB, self.step["verdict_consume"], st, self.step)
        self.assertEqual(res["next"], "ww_write")
        self.assertTrue(any(h["action"] == "verdict_reject" for h in st["history"]))
        self.assertEqual(st["iteration"], 1)  # 打回计轮

    def test_missing_field_treated_as_reject(self):
        self._patch_artifact(None)
        st = _state()
        res = self.eng._consume_verdict(
            "ww_factcheck", FC_GLOB, self.step["verdict_consume"], st, self.step)
        self.assertEqual(res["next"], "ww_write")
        self.assertIn("缺失", res["log"])

    def test_over_retry_escalates_with_warning(self):
        p = Path(self.tmp.name) / "001-xuanwu-ww-factcheck-report.md"
        p.write_text("---\nauthor: xuanwu\nfactcheck_result: changes_requested\n---\n\n# 报告\n",
                     encoding="utf-8")
        self._patch_artifact(p)
        st = _state()
        st["history"] = [{"step": "ww_factcheck", "action": "verdict_reject"},
                         {"step": "ww_factcheck", "action": "verdict_reject"}]
        res = self.eng._consume_verdict(
            "ww_factcheck", FC_GLOB, self.step["verdict_consume"], st, self.step)
        self.assertIsNone(res)  # 带警示放行（不阻断）
        self.assertTrue(any(h["action"] == "verdict_escalate" for h in st["history"]))

    def test_reject_body_injection(self):
        body = eng_mod.build_reject_body("ww_factcheck", ["年份错", "引用页无数据"])
        self.assertIn("[引擎打回单 ww_factcheck]", body)
        self.assertIn("1. 年份错", body)


class TestVerdictGateOnBarrier(unittest.TestCase):
    def setUp(self):
        self.eng = eng_mod.WeWriteEngine("t-m2-vg")

    def _wait_step(self):
        for s in self.eng.template["steps"]:
            if s["id"] == "ww_review_barrier":
                return s
        raise KeyError

    def test_gate_rejects_on_changes_requested(self):
        # 真屏障 glob 指向真实 exchange/verdicts——本测试不写真板目录，
        # 构造 monkeypatch：替换 _resolve_artifact 与 read_frontmatter_field
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: Path("fake.md")
        try:
            orig_read = eng_mod.read_frontmatter_field
            vals = iter(["approved", "changes_requested"])
            eng_mod.read_frontmatter_field = lambda p, f: next(vals)
            st = _state()
            st["step"] = "ww_review_barrier"
            res = self.eng._do_wait(self._wait_step(), st)
            self.assertEqual(res["next"], "ww_polish")
            self.assertTrue(any(h["action"] == "verdict_gate_reject"
                                for h in st["history"]))
        finally:
            eng_mod._resolve_artifact = orig
            # t_d1627b5a：read_frontmatter_field 也须恢复——否则污染后续文件
            # （test_016_rectify 消费路径被 exhausted iterator 打穿成 StopIteration）
            eng_mod.read_frontmatter_field = orig_read

    def test_gate_passes_when_all_approved(self):
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: Path("fake.md")
        try:
            orig_read = eng_mod.read_frontmatter_field
            eng_mod.read_frontmatter_field = lambda p, f: "approved"
            st = _state()
            st["step"] = "ww_review_barrier"
            res = self.eng._do_wait(self._wait_step(), st)
            self.assertEqual(res["next"], "ww_logic_review")
        finally:
            eng_mod._resolve_artifact = orig
            eng_mod.read_frontmatter_field = orig_read

    def test_gate_missing_field_treated_as_reject(self):
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: Path("fake.md")
        orig_read = eng_mod.read_frontmatter_field
        try:
            eng_mod.read_frontmatter_field = lambda p, f: None
            st = _state()
            st["step"] = "ww_review_barrier"
            res = self.eng._do_wait(self._wait_step(), st)
            self.assertEqual(res["next"], "ww_polish")
        finally:
            eng_mod._resolve_artifact = orig
            eng_mod.read_frontmatter_field = orig_read


class TestDenyProviders(unittest.TestCase):
    """Δ7：audit 类禁配绕行（S5 判据：DENIED 日志实证）。"""

    def test_audit_step_minimax_denied_routes_fallback(self):
        route, log = model_router.resolve_routing("ww_factcheck")
        self.assertIsNotNone(route)
        self.assertNotEqual(route["provider"], "minimax")
        self.assertIn("DENIED", log)
        self.assertIn("fallback", log)

    def test_audit_step_non_denied_unchanged(self):
        route, log = model_router.resolve_routing("ww_review_audit")
        self.assertEqual(route["provider"], "deepseek")
        self.assertNotIn("DENIED", log)

    def test_normal_step_unaffected(self):
        route, log = model_router.resolve_routing("ww_write")
        self.assertEqual(route["provider"], "deepseek")
        self.assertNotIn("DENIED", log)

    def test_all_denied_rejects(self):
        orig = model_router.load_classes
        model_router.load_classes = lambda: {
            "audit": {"deny_providers": ["minimax", "deepseek"]}}
        try:
            route, log = model_router.resolve_routing("ww_factcheck")
            self.assertIsNone(route)  # 拒派，不静默降级
            self.assertIn("拒派", log)
        finally:
            model_router.load_classes = orig

    def test_routing_log_line_has_denied_for_factcheck(self):
        self.assertIn("DENIED", model_router.routing_log_line("ww_factcheck"))


class TestSlugRegistry(unittest.TestCase):
    """Δ8：同构校验 + registry 生成。"""

    def test_current_pipeline_passes(self):
        self.assertEqual(slug_registry.validate_slug_homogeneity(), [])

    def test_bad_config_rejected(self):
        bad = {"steps": [{"id": "ww_write",
                          "artifact": "D:/x/*-totally-different*.md"}]}
        errs = slug_registry.validate_slug_homogeneity(bad)
        self.assertEqual(len(errs), 1)
        self.assertIn("ww_write", errs[0])

    def test_explicit_slug_declaration_exempts(self):
        ok = {"steps": [{"id": "ww_write",
                         "artifact": "D:/x/*-custom-slug*.md",
                         "slug": "custom-slug"}]}
        self.assertEqual(slug_registry.validate_slug_homogeneity(ok), [])

    def test_registry_contains_factcheck_slug(self):
        reg = slug_registry.build_registry()
        self.assertIn("ww-factcheck-report", reg)

    def test_engine_body_injects_registry_and_verdict_contract(self):
        eng = eng_mod.WeWriteEngine("t-m2-body")
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_factcheck")
        body = eng._step_goal(step)
        self.assertIn("verdict 契约", body)
        self.assertIn("factcheck_result", body)

    def test_engine_body_skill_path_owner(self):
        eng = eng_mod.WeWriteEngine("t-m2-body")
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_write")
        body = eng._step_goal(step)
        # Δ6：skill 名注入（路径段：存在则完整路径，不存在则"未解析"占位）
        self.assertIn("duanmu-gzh-writer", body)


if __name__ == "__main__":
    unittest.main()
