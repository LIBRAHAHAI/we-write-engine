"""T6 模型路由测试：声明与 pipeline.yaml 对拍。

2026-09-11 D3 收敛：原仓库根 tests/test_model_router.py 的两条「卡 body 注入」用例
已并入本文件末段（声明→实际调用的对拍锚点）。其余 4 条与本文件既有断言同意图且
本文件覆盖更宽（routing_log_line 另查 step 名与"默认模型"分支），未重复搬运。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import model_router


class ModelRouterTest(unittest.TestCase):
    def test_write_routed_deepseek(self):
        r = model_router.get_routing("ww_write")
        self.assertEqual(r, {"provider": "deepseek", "model": "deepseek-v4-pro"})

    def test_polish_routed_qwen(self):
        r = model_router.get_routing("ww_polish")
        self.assertEqual(r, {"provider": "qwen", "model": "qwen3.8-max"})

    def test_unconfigured_step_returns_none(self):
        self.assertIsNone(model_router.get_routing("ww_topic"))

    def test_log_line_format(self):
        line = model_router.routing_log_line("ww_write")
        self.assertIn("ww_write", line)
        self.assertIn("provider=deepseek", line)
        self.assertIn("model=deepseek-v4-pro", line)
        line2 = model_router.routing_log_line("ww_topic")
        self.assertIn("默认模型", line2)

    # ---- 并自用例（原仓库根 tests/test_model_router.py，D3 收敛）----
    # _step_goal 是纯字符串组装：不建卡、不触 kanban，故无需 mock。
    def test_engine_body_injects_routing_hint(self):
        """dispatch 卡 body 应包含模型路由提示（声明→实际调用对拍锚点）。"""
        import engine as eng_mod
        e = eng_mod.WeWriteEngine("test-route")
        step = next(s for s in e.template["steps"] if s["id"] == "ww_write")
        body = e._step_goal(step)
        self.assertIn("provider=deepseek", body)
        self.assertIn("deepseek-v4-pro", body)

    def test_engine_body_injects_skills(self):
        """卡 body 应带 skills_map 声明的 skill 注入前缀。"""
        import engine as eng_mod
        e = eng_mod.WeWriteEngine("test-route")
        step = next(s for s in e.template["steps"] if s["id"] == "ww_topic")
        body = e._step_goal(step)
        self.assertIn("athena-topic-scout", body)


if __name__ == "__main__":
    unittest.main()
