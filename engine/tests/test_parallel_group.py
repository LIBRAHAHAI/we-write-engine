"""T4 并行组测试：成员幂等键独立 + 屏障全齐放行 + 单路缺失不放行。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine import WeWriteEngine

PIPELINE = Path(__file__).parent.parent / "config" / "templates" / "hot-topic-commentary.yaml"

# t_a6da5fa1：引擎新增「正文非空」硬门（空壳产物视同未交付）——屏障用例的夹具
# 产物必须具备真实正文（旧夹具正文仅 "t" 会被硬门拒收，属夹具失真非代码回归）。
_MAT_BODY = ("---\nstatus: done\n---\n\n# 素材夹具\n\n"
             "本行正文用于验证正文非空硬门对正常产物放行（sandbox 用例专用）。" * 8 + "\n")  # 016 #2 阈值 200


class ParallelGroupTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        # 0915 假红根治（016 复审 §一 B2 同源）：屏障/产物 glob 重定向到 sandbox，
        # 不再写真实 exchange/artifacts/（09-14 清理后真实池已回填 article-002 五路，
        # 旧用例的假产物文件名与真实池 glob 撞车 → 假绿/假红洗牌）。
        self.sandbox_artifacts = self.tmp / "artifacts"
        self.sandbox_artifacts.mkdir(parents=True, exist_ok=True)

    def _eng(self):
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = "test-pg"
        eng.mode = "test"  # M3/Δ10：run() 消费
        eng.template = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        # 方案 A（同 realdata fixture 原则）：把 pipeline 内写死的真实池 glob
        # 全部重写到 sandbox —— 引擎 _resolve_artifact 直读 yaml 串，无法用
        # WW_EXCHANGE_DIR 重定向，故在 template 层替换路径前缀。
        real_prefix = "./exchange/artifacts/"
        sandbox_prefix = str(self.sandbox_artifacts).replace("\\", "/") + "/"
        def _rewrite(obj):
            if isinstance(obj, str):
                return obj.replace(real_prefix, sandbox_prefix)
            if isinstance(obj, list):
                return [_rewrite(x) for x in obj]
            if isinstance(obj, dict):
                return {k: _rewrite(v) for k, v in obj.items()}
            return obj
        eng.template = _rewrite(eng.template)
        eng.instance_path = self.tmp / "test-pg.json"
        return eng

    def _mat(self, area: str) -> Path:
        return self.sandbox_artifacts / f"001-hermes-ww-mat-{area}-test.md"

    def test_member_keys_independent(self):
        """5 成员幂等键各含 member id（engine:we-write-engine:<inst>:<mid>）。"""
        eng = self._eng()
        step = eng._step_by_id("ww_mat_group")
        ids = [m["id"] for m in step["members"]]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        # 模拟 _create_card 的幂等键构造逻辑
        for mid in ids:
            key = f"engine:we-write-engine:{eng.instance}:{mid}"
            self.assertTrue(key.endswith(mid))

    def test_group_skips_inflight_and_done(self):
        """在途卡幂等跳过；done 卡不重派。"""
        eng = self._eng()
        st = {"instance": "test-pg", "step": "ww_mat_group", "iteration": 0,
              "history": [], "status": "running"}
        eng._save_state(st)
        statuses = {
            "ww_mat_news": {"id": "t_a", "status": "ready"},
            "ww_mat_history": {"id": "t_b", "status": "done"},
            "ww_mat_classic": {"id": "t_c", "status": "running"},
            "ww_mat_law": {"id": "t_d", "status": "blocked"},
            "ww_mat_econ": {"id": "t_e", "status": "todo"},
        }
        created = []
        with mock.patch.object(eng, "_resolve_step_card",
                               side_effect=lambda sid: statuses.get(sid)), \
             mock.patch.object(eng, "_create_card",
                               side_effect=lambda s, st2, it: created.append(s) or "t_x"):
            out = eng.run()
        self.assertEqual(created, [])  # 全部在途/done，零建卡
        self.assertIn("5 卡就绪", out)

    def test_group_failfast_on_missing_id(self):
        eng = self._eng()
        st = {"instance": "test-pg", "step": "ww_mat_group", "iteration": 0,
              "history": [], "status": "running"}
        eng._save_state(st)
        eng.template["steps"][eng.template["steps"].index(
            eng._step_by_id("ww_mat_group"))]["members"] = [{"assignee": "default"}]
        with self.assertRaises(RuntimeError):
            eng.run()

    def test_barrier_partial_missing_blocks(self):
        """单路缺失不放行（S2 屏障语义）。0915 起产物写 sandbox，不碰真实池。"""
        eng = self._eng()
        st = {"instance": "test-pg", "step": "ww_mat_barrier", "iteration": 1,
              "history": [], "status": "running"}
        eng._save_state(st)
        # 造 4/5 产物（sandbox 池；文件名匹配 pipeline glob *-ww-mat-<路>*.md）
        for a in ["news", "history", "classic", "law"]:
            self._mat(a).write_text("t", encoding="utf-8")
        eng.run()
        s2 = eng._load_state()
        self.assertEqual(s2["step"], "ww_mat_barrier")  # econ 缺 → 不放行

    def test_barrier_all_present_passes(self):
        eng = self._eng()
        st = {"instance": "test-pg", "step": "ww_mat_barrier", "iteration": 1,
              "history": [], "status": "running"}
        eng._save_state(st)
        for a in ["news", "history", "classic", "law", "econ"]:
            self._mat(a).write_text(_MAT_BODY, encoding="utf-8")
        eng.run()
        s2 = eng._load_state()
        self.assertEqual(s2["step"], "ww_mat_select")


if __name__ == "__main__":
    unittest.main()
