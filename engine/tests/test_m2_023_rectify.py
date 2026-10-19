"""test_m2_023_rectify.py — 023 判决整改回归（Z1-Z4 / G1-G4）。

放行条件（023 §四 2-6）逐条对应：
  Z1 卡 body 注入 EXWRITE_SLUG_REGISTRY 且值 == build_registry()
  Z2b 屏障失配近似候选诊断（escalate 行含「期望 slug=X，发现近似候选 Y」）
  Z3  构造 WeWriteEngine 时坏配置拒启 raise（正反用例）
  Z4  门3 拍板输出含「事实核查未全清」警示（verdict_escalate history）
  G1  fallback 显式 model 不随 models 段插入顺序漂移
  G2  组级 load_class 成员继承（get_load_class 上溯父组）
  G3  slug 豁免池作用域限定（跨步声明不再豁免他步漂移）
  G4  severity=warn 分流 + 高频误报词豁免
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ENGINE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "config"))

import engine as eng_mod  # noqa: E402
from config import slug_registry  # noqa: E402
import model_router  # noqa: E402
import discipline  # noqa: E402

PIPELINE = ENGINE_DIR / "config" / "templates" / "hot-topic-commentary.yaml"


class TestZ1RegistryInjection(unittest.TestCase):
    def test_body_contains_registry_and_value_matches(self):
        eng = eng_mod.WeWriteEngine("t-023-z1")
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_topic")
        body = eng._step_goal(step)
        expected = slug_registry.build_registry(eng.template)
        self.assertIn("EXWRITE_SLUG_REGISTRY=", body)
        self.assertIn(f"EXWRITE_SLUG_REGISTRY={expected}", body)

    def test_registry_covers_all_work_slugs(self):
        reg = slug_registry.build_registry()
        for slug in ("ww-topic-candidates", "ww-outline", "ww-mat-news",
                     "ww-selection", "ww-draft", "ww-polish",
                     "ww-factcheck-report", "ww-title",
                     "ww-audit-verdict", "ww-value-verdict"):
            self.assertIn(slug, reg, f"注册表缺 {slug}")


class TestZ2bApproximateDiagnosis(unittest.TestCase):
    def test_diagnose_returns_hint_for_close_slug(self):
        reg = slug_registry.build_registry()
        hint = eng_mod.diagnose_slug_mismatch("ww-mat-newss", reg)  # 多打个 s
        self.assertIn("期望 slug=ww-mat-newss", hint)
        self.assertIn("近似候选", hint)
        self.assertIn("ww-mat-news", hint)

    def test_wait_escalate_log_contains_hint(self):
        eng = eng_mod.WeWriteEngine("t-023-z2b")
        st = {"instance": "t-023-z2b", "step": "ww_mat_barrier", "iteration": 5,
              "history": [], "status": "running", "dispatched": {}}
        eng.instance_path = eng_mod.INSTANCES_DIR / "t-023-z2b.json"
        eng._save_state(st)
        step = eng._step_by_id("ww_mat_barrier")
        # 构造：产物 glob 写错一个字母 → wait 连续超限
        bad_step = dict(step)
        bad_step["artifacts"] = [
            a.replace("ww-mat-news", "ww-mat-newss") if "ww-mat-news" in a else a
            for a in step["artifacts"]]
        res = None
        for _ in range(6):  # 超过 wait_escalate_max=5
            res = eng._do_wait(bad_step, st)
        self.assertIsNotNone(res)
        self.assertIn("期望 slug=ww-mat-newss", res["log"])
        self.assertIn("发现近似候选 ww-mat-news", res["log"])


class TestZ3ValidateGate(unittest.TestCase):
    def test_good_config_constructs(self):
        eng = eng_mod.WeWriteEngine("t-023-z3-ok")
        self.assertEqual(len(eng.template["steps"]), 21)  # E1：20→21（ww_cover_ai）

    def test_bad_config_raises_on_construct(self):
        """坏配置（slug 漂移）构造 WeWriteEngine 必须 raise（拒启）。"""
        real_load = yaml.safe_load

        def bad_load(*a, **kw):
            tpl = real_load(PIPELINE.read_text(encoding="utf-8"))
            tpl["steps"][0]["artifact"] = "D:/x/*-totally-different*.md"
            return tpl

        with mock.patch.object(eng_mod.yaml, "safe_load", side_effect=bad_load):
            with self.assertRaises(ValueError) as cm:
                eng_mod.WeWriteEngine("t-023-z3-bad")
            self.assertIn("拒启", str(cm.exception))
            self.assertIn("totally-different", str(cm.exception))


class TestZ4EscalationReachable(unittest.TestCase):
    def test_decision_pending_shows_factcheck_warning(self):
        eng = eng_mod.WeWriteEngine("t-023-z4")
        st = {"instance": "t-023-z4", "step": "ww_gate_final", "iteration": 3,
              "history": [{"step": "ww_factcheck", "action": "verdict_escalate",
                           "detail": "factcheck_result=FAIL",
                           "log": "⚠🚨 verdict ww_factcheck: FAIL 连续 2 轮"}],
              "status": "running", "dispatched": {}}
        eng.instance_path = eng_mod.INSTANCES_DIR / "t-023-z4.json"
        eng._save_state(st)
        step = eng._step_by_id("ww_gate_final")
        res = eng._do_decision(step, st)
        self.assertIn("事实核查未全清", res["log"])
        self.assertIn("警示", res["log"])

    def test_no_escalation_no_warning(self):
        eng = eng_mod.WeWriteEngine("t-023-z4b")
        st = {"instance": "t-023-z4b", "step": "ww_gate_final", "iteration": 1,
              "history": [], "status": "running", "dispatched": {}}
        eng.instance_path = eng_mod.INSTANCES_DIR / "t-023-z4b.json"
        eng._save_state(st)
        step = eng._step_by_id("ww_gate_final")
        res = eng._do_decision(step, st)
        self.assertNotIn("事实核查未全清", res["log"])


class TestG1FallbackExplicitModel(unittest.TestCase):
    def test_fallback_uses_explicit_model_regardless_of_order(self):
        """fallback 显式 {provider, model} → 模型不随 models 段插入顺序变化。"""
        r1, log1 = model_router.resolve_routing("ww_factcheck")
        # 模拟 023 §三 G1 的变异：在 models 段最前插一条 provider=deepseek 条目
        tpl = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        tpl["models"] = {"zz_fake_first": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                         **tpl["models"]}
        with mock.patch.object(model_router, "load_models", return_value=tpl["models"]):
            r2, log2 = model_router.resolve_routing("ww_factcheck")
        self.assertEqual(r1["provider"], "deepseek")
        self.assertEqual(r1["model"], "deepseek-chat")
        self.assertEqual(r1, r2, "fallback 模型随 models 段插入顺序漂移（G1 未根治）")
        self.assertIn("DENIED", log1)


class TestG2GroupLoadClassInherit(unittest.TestCase):
    def test_member_inherits_group_load_class(self):
        self.assertEqual(model_router.get_load_class("ww_review_value"), "audit")
        self.assertEqual(model_router.get_load_class("ww_review_audit"), "audit")

    def test_member_minimax_route_denied_via_inheritance(self):
        """023 G2 反证场景：ww_review_value 配 minimax → 须 DENIED（组级继承生效）。"""
        with mock.patch.object(
                model_router, "load_models",
                return_value={"ww_review_value": {"provider": "minimax",
                                                  "model": "abab6.5s-chat"}}):
            r, log = model_router.resolve_routing("ww_review_value")
        self.assertIsNone(r, "audit 组成员配 minimax 未被拒派（G2 未闭环）")
        self.assertIn("拒派", log)


class TestG3ExemptScope(unittest.TestCase):
    def test_cross_step_declaration_no_longer_exempts(self):
        """023 G3 实证场景：ww_topic 声明 totally-different 不得豁免 ww_write 漂移。"""
        tpl = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        for s in tpl["steps"]:
            if s["id"] == "ww_topic":
                s["slug"] = "totally-different"
            if s["id"] == "ww_write":
                s["artifact"] = "D:/x/*-totally-different*.md"
        errs = slug_registry.validate_slug_homogeneity(tpl)
        self.assertTrue(any("ww_write" in e for e in errs),
                        f"跨步声明豁免了 ww_write 漂移（G3 未闭环）: {errs}")

    def test_barrier_still_exempted_by_upstream_group(self):
        """屏障的 artifacts 是上游组成员产物 → 上游组声明豁免仍生效。"""
        self.assertEqual(slug_registry.validate_slug_homogeneity(), [])


class TestG4WarnSeverity(unittest.TestCase):
    def test_real_prose_no_block(self):
        """023 §三 G4 十误报样本：violations=0（合法表述不再被拦）。"""
        samples = [
            "这是他最好的时代，也是最坏的时代。",
            "最高级是一种语法范畴。",
            "顶级配置的服务器。",
            "请勿擅自停药，遵医嘱用药。",
            "困在派单算法里的骑手。",
            "订单卡单了，平台该负责。",
            "独一无二的魏晋风度。",
            "内幕消息公开后股价下跌。",
            "所谓「财富密码」不过是幸存者偏差。",
            "反诈科普：刷单话术拆解。",
        ]
        for s in samples:
            res = discipline.check(s, stage="post")
            block = [v for v in res["violations"] if "字数" not in v]
            self.assertEqual(block, [], f"合法表述被拦: {s!r} → {block}")

    def test_warn_branch_detected_not_blocking(self):
        """真违规词命中 → 进 warns（severity=warn 试运行），不进 violations。"""
        bad = "本品包治百病，稳赚不赔。"
        res = discipline.check(bad, stage="post")
        self.assertTrue(res.get("warns"), "真违规词未进 warns（severity=warn 未生效）")
        self.assertTrue(all(w.startswith("postcheck-warn") for w in res["warns"]))
        block = [v for v in res["violations"] if "字数" not in v]
        self.assertEqual(block, [], "warn 级命中不应进 violations（试运行期不 block）")

    def test_severity_block_still_blocks(self):
        """severity 显式 block（缺省）→ 命中仍进 violations。"""
        res = discipline._check_wordlist(
            "全网第一的品牌", {"name": "t", "wordlist": "ad_law.txt"})  # 无 severity → block
        self.assertTrue(res)
        self.assertTrue(all(v.startswith("postcheck:") for v in res))


if __name__ == "__main__":
    unittest.main()
