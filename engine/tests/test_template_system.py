"""test_template_system.py — 005 需求1 模板体系测试（2026-09-15）。

覆盖：
  - 模板目录与默认模板（hot-topic-commentary 抽取后 20 节点语义不丢）
  - 旧实例兼容：state 无 template 字段 → 落默认模板，行为不变（兼容硬要求）
  - 实例绑定模板首跑落盘定终身；显式与绑定冲突 fail-closed
  - 未知模板 id 拒启；骨架模板 run() 拒跑（--status 只读不受影响）
  - 需求2：材料注入选题卡 comment（幂等 + 失败下拍重试）
  - 需求4：门拍板 C|意见 → 返工卡 comment 注入（pending 消费防重复）

实弹纪律（conftest 铁律）：dispatch 路径用例一律 mock _resolve_step_card /
_create_card / kanban 重开 / _post_card_comment（subprocess 外呼全mock，零活跃板接触）。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from config import template_loader
import engine as eng_mod
from engine import WeWriteEngine

TPL_DIR = Path(__file__).parent.parent / "config" / "templates"
DEFAULT_TPL = TPL_DIR / "hot-topic-commentary.yaml"

MATS = [
    {"type": "text", "content": "聚焦年轻人消费降级中的「平替」现象"},
    {"type": "url", "content": "https://example.com/statistics-2026"},
    {"type": "file", "content": "D:/materials/notes.md"},
    {"type": "folder", "content": "D:/materials/cases"},
]


def _mk_engine(tmp: Path, name: str = "t-tpl", **kw) -> WeWriteEngine:
    """临时实例目录构造引擎（不碰真实 instances/）。"""
    eng_mod.INSTANCES_DIR = tmp
    return WeWriteEngine(name, **kw)


class TemplateLoaderTest(unittest.TestCase):
    def test_seven_templates_default_first(self):
        ids = template_loader.available_template_ids()
        self.assertEqual(ids[0], "hot-topic-commentary")
        self.assertEqual(
            set(ids),
            {"hot-topic-commentary", "finance-analysis", "novel-long", "novel-short",
             "book-movie-review", "screenplay", "history-narrative"})

    def test_default_template_keeps_21_nodes(self):
        """结构口径钉死：21 节点（20 节点 + E1/20260920 新增 ww_cover_ai 封面 AI 出图步）。"""
        tpl = yaml.safe_load(DEFAULT_TPL.read_text(encoding="utf-8"))
        types = [s["type"] for s in tpl["steps"]]
        self.assertEqual(len(types), 21)
        self.assertEqual(types.count("dispatch"), 11)
        self.assertEqual(types.count("dispatch_group"), 2)
        self.assertEqual(types.count("decision"), 4)
        self.assertEqual(types.count("wait"), 2)
        self.assertEqual(types.count("check"), 1)
        self.assertEqual(types.count("done"), 1)
        self.assertIn("skills_map", tpl)  # Δ6 绑定随迁
        self.assertIn("models", tpl)      # T6 路由随迁

    def test_illegal_template_id_rejected(self):
        with self.assertRaises(ValueError):
            template_loader.template_path("../pipeline")
        with self.assertRaises(ValueError):
            template_loader.template_path("no-such-template")

    def test_resolve_conflict_fail_closed(self):
        with self.assertRaises(ValueError):
            template_loader.resolve_template_id("novel-short", "hot-topic-commentary")
        # 旧实例（无绑定）→ 默认模板
        self.assertEqual(template_loader.resolve_template_id(None, None),
                         "hot-topic-commentary")


class TemplateBindingTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = eng_mod.INSTANCES_DIR
        eng_mod.INSTANCES_DIR = self.tmp

    tearDown_restore = None

    def tearDown(self):
        eng_mod.INSTANCES_DIR = self._orig

    def test_legacy_instance_falls_back_to_default(self):
        """兼容硬要求：005 前落盘的实例（无 template 字段）照常加载运行。"""
        (self.tmp / "t-legacy.json").write_text(json.dumps({
            "instance": "t-legacy", "step": "ww_topic", "iteration": 3,
            "history": [], "status": "running", "dispatched": {}, "mode": "test"}),
            encoding="utf-8")
        eng = WeWriteEngine("t-legacy")
        self.assertEqual(eng.template_id, "hot-topic-commentary")
        # --status 可读，且本拍落盘补 template 绑定（定终身）
        out = eng.status()
        self.assertIn("hot-topic-commentary", out)
        st = json.loads((self.tmp / "t-legacy.json").read_text(encoding="utf-8"))
        self.assertEqual(st["template"], "hot-topic-commentary")
        self.assertEqual(st["step"], "ww_topic")  # 断点不动
        self.assertEqual(st["iteration"], 3)

    def test_fresh_instance_binds_explicit_template(self):
        eng = WeWriteEngine("t-bind", template="hot-topic-commentary")
        st = eng._load_state()
        self.assertEqual(st["template"], "hot-topic-commentary")
        st2 = json.loads((self.tmp / "t-bind.json").read_text(encoding="utf-8"))
        self.assertEqual(st2["template"], "hot-topic-commentary")

    def test_explicit_conflict_with_bound_state_raises(self):
        (self.tmp / "t-conf.json").write_text(json.dumps({
            "instance": "t-conf", "step": None, "iteration": 0, "history": [],
            "status": "idle", "dispatched": {}, "mode": "test",
            "template": "hot-topic-commentary"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            WeWriteEngine("t-conf", template="novel-short")

    def test_unknown_bound_template_raises(self):
        (self.tmp / "t-ghost.json").write_text(json.dumps({
            "instance": "t-ghost", "step": None, "iteration": 0, "history": [],
            "status": "idle", "dispatched": {}, "mode": "test",
            "template": "no-such-tpl"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            WeWriteEngine("t-ghost")

    def test_skeleton_refuses_run_but_status_ok(self):
        # 骨架 fixture 自建自清（2026-09-26：novel-short 等 6 模板已完善去
        # skeleton，测试不得再耦合真实模板的 skeleton 状态——修规则不修事件）
        skel_id = "zz-test-skeleton"
        skel_path = TPL_DIR / f"{skel_id}.yaml"
        skel_path.write_text(yaml.safe_dump({
            "name": skel_id, "title": "测试骨架", "status": "skeleton",
            "max_iterations": 10,
            "steps": [{"id": "zz_step", "type": "dispatch", "assignee": "default",
                       "description": "占位", "next": "zz_done"},
                      {"id": "zz_done", "type": "done", "description": "完"}],
        }, allow_unicode=True), encoding="utf-8")
        try:
            eng = WeWriteEngine("t-skel", template=skel_id)
            with self.assertRaises(ValueError):
                eng.run()
            # --status 只读不受拒跑影响
            self.assertIn(skel_id, eng.status())
        finally:
            skel_path.unlink(missing_ok=True)


class MaterialsInjectTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = eng_mod.INSTANCES_DIR
        eng_mod.INSTANCES_DIR = self.tmp

    def tearDown(self):
        eng_mod.INSTANCES_DIR = self._orig

    def test_materials_commented_on_first_dispatch_once(self):
        eng = WeWriteEngine("t-mat", materials=MATS)
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_matcard") as mk, \
             mock.patch.object(eng, "_post_card_comment", return_value=True) as pc:
            eng.run()
            mk.assert_called_once()
            pc.assert_called_once()
        body = pc.call_args[0][1]
        self.assertIn("材料注入", body)
        for m in MATS:
            self.assertIn(m["content"], body)
        self.assertIn("[url]", body)
        st = json.loads((self.tmp / "t-mat.json").read_text(encoding="utf-8"))
        self.assertTrue(st["materials_injected"])
        self.assertEqual(st["materials"], MATS)
        # 拍2（卡在途）：幂等——不重复注入
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_matcard", "status": "ready"}), \
             mock.patch.object(eng, "_post_card_comment", return_value=True) as pc2:
            eng.run()
            pc2.assert_not_called()

    def test_materials_retry_next_beat_on_failure(self):
        eng = WeWriteEngine("t-matf", materials=MATS[:1])
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_matf1"), \
             mock.patch.object(eng, "_post_card_comment", return_value=False):
            eng.run()
        st = json.loads((self.tmp / "t-matf.json").read_text(encoding="utf-8"))
        self.assertNotIn("materials_injected", st)  # 未置位 → 下拍重试
        # 下拍（在途）：重试成功
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_matf1", "status": "running"}), \
             mock.patch.object(eng, "_post_card_comment", return_value=True) as pc:
            eng.run()
            pc.assert_called_once()
        st = json.loads((self.tmp / "t-matf.json").read_text(encoding="utf-8"))
        self.assertTrue(st["materials_injected"])

    def test_no_materials_no_comment(self):
        eng = WeWriteEngine("t-mat0")
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_m0"), \
             mock.patch.object(eng, "_post_card_comment", return_value=True) as pc:
            eng.run()
            pc.assert_not_called()


class ReworkNoteTest(unittest.TestCase):
    """需求4：门3 拍板 C|意见 → 打回 ww_polish → 返工卡 comment 收到意见。"""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp()
                        )
        self._orig = eng_mod.INSTANCES_DIR
        eng_mod.INSTANCES_DIR = self.tmp

    def tearDown(self):
        eng_mod.INSTANCES_DIR = self._orig

    def _at_gate_final(self, name: str) -> WeWriteEngine:
        (self.tmp / f"{name}.json").write_text(json.dumps({
            "instance": name, "step": "ww_gate_final", "iteration": 5,
            "history": [], "status": "running", "dispatched": {},
            "mode": "test", "template": "hot-topic-commentary"}),
            encoding="utf-8")
        return WeWriteEngine(name)

    def test_c_decision_pends_note_then_flushes_to_rework_card(self):
        eng = self._at_gate_final("t-cnote")
        (self.tmp / "t-cnote.decision").write_text(
            "C|开头钩子太弱，前 100 字必须抓人", encoding="utf-8")
        out = eng.run()
        self.assertIn("带修改意见修改", out)
        st = json.loads((self.tmp / "t-cnote.json").read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_polish")  # rework_target
        self.assertEqual(st["pending_rework_note"]["gate"], "ww_gate_final")
        self.assertIn("钩子", st["pending_rework_note"]["note"])
        # 下一拍：ww_polish 有 done 残卡 → 重开分支注入意见 comment
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_polish", "status": "done"}), \
             mock.patch.object(eng_mod.kanban_client, "reopen_done_card",
                               return_value=True) as rk, \
             mock.patch.object(eng, "_post_card_comment", return_value=True) as pc:
            eng.run()
            rk.assert_called_once()
            pc.assert_called_once()
        self.assertIn("开头钩子太弱", pc.call_args[0][1])
        self.assertIn("ww_gate_final", pc.call_args[0][1])
        st2 = json.loads((self.tmp / "t-cnote.json").read_text(encoding="utf-8"))
        self.assertIsNone(st2["pending_rework_note"])  # 已消费防重复
        # 再拍（在途）：不重复投递
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_polish", "status": "ready"}), \
             mock.patch.object(eng, "_post_card_comment", return_value=True) as pc2:
            eng.run()
            pc2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
