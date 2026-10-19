# -*- coding: utf-8 -*-
"""拍板门默认预设（decision_defaults / default_decision）单测。

机制（问题清单 #6）：user 预设默认拍板后，decision 步无 decision 文件时
自动按预设落拍（history 留痕 source=auto_default），不再等待 user。

测试纯内存构造 template/state，不落真实产物、不碰活跃板（铁律 5）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
from engine import WeWriteEngine


def _mk_engine(template: dict, opt_in: bool = True) -> WeWriteEngine:
    """opt_in=True → 沙箱 instances 写 .unattended（无人值守授权自动拍）。

    20260921：预设默认需实例 opt-in（defaults.json / .unattended 任一在），
    无授权时即使模板声明了 decision_defaults 也必须停在拍板点。
    """
    tmp = Path(tempfile.mkdtemp())
    ww_engine.INSTANCES_DIR = tmp
    if opt_in:
        (tmp / "test-defaults.unattended").write_text("", encoding="utf-8")
    eng = WeWriteEngine.__new__(WeWriteEngine)
    eng.instance = "test-defaults"
    eng.template = template
    eng._load_state = MagicMock(return_value={
        "instance": "test-defaults", "step": "g1", "iteration": 0,
        "history": [], "status": "running"})
    eng._save_state = MagicMock()
    eng._escalation_warnings = MagicMock(return_value=[])
    return eng


GATE = {
    "id": "g1", "type": "decision", "question": "q?",
    "options": [
        {"key": "A", "label": "通过", "next": "s_next"},
        {"key": "B", "label": "驳回", "next": "s_back"},
    ],
    "on_reject": "s_back", "next": "s_next",
}


class DecisionDefaultsTest(unittest.TestCase):
    def test_no_default_waits(self):
        """无预设 → 维持原语义等待拍板（next=None）。"""
        eng = _mk_engine({"steps": [dict(GATE)]})
        res = eng._do_decision(GATE, eng._load_state())
        self.assertIsNone(res["next"])
        self.assertIn("等待拍板", res["log"])

    def test_no_opt_in_waits_even_with_template_defaults(self):
        """20260921：未勾选预设默认/无人值守（无 opt-in 标记）→ 即使模板声明
        decision_defaults 也必须停在拍板点（article-4 第二次选题被自动放行实证）。"""
        eng = _mk_engine({"steps": [dict(GATE)],
                          "decision_defaults": {"g1": "A"}}, opt_in=False)
        res = eng._do_decision(dict(GATE), eng._load_state())
        self.assertIsNone(res["next"])
        self.assertIn("等待拍板", res["log"])

    def test_defaults_json_opt_in_alone_suffices(self):
        """只勾「预设默认」（defaults.json 在、无 .unattended）→ 模板默认生效。"""
        eng = _mk_engine({"steps": [dict(GATE)],
                          "decision_defaults": {"g1": "A"}}, opt_in=False)
        (ww_engine.INSTANCES_DIR / "test-defaults.defaults.json").write_text(
            "{}", encoding="utf-8")
        res = eng._do_decision(dict(GATE), eng._load_state())
        self.assertEqual(res["next"], "s_next")

    def test_step_level_default(self):
        """步骤级 default_decision 命中 → 自动落拍到选项 next。"""
        gate = dict(GATE, default_decision="A")
        eng = _mk_engine({"steps": [gate]})
        st = eng._load_state()
        res = eng._do_decision(gate, st)
        self.assertEqual(res["next"], "s_next")
        self.assertIn("拍板(预设默认)", res["log"])
        self.assertEqual(st["history"][-1]["source"], "auto_default")
        self.assertEqual(st["history"][-1]["decision"], "A")

    def test_top_level_default(self):
        """顶层 decision_defaults 按门 id 命中 → 同样自动落拍。"""
        eng = _mk_engine({"steps": [dict(GATE)],
                          "decision_defaults": {"g1": "A"}})
        res = eng._do_decision(dict(GATE), eng._load_state())
        self.assertEqual(res["next"], "s_next")
        self.assertIn("拍板(预设默认)", res["log"])

    def test_step_level_overrides_top(self):
        """步骤级优先于顶层映射。"""
        gate = dict(GATE, default_decision="B")
        eng = _mk_engine({"steps": [gate], "decision_defaults": {"g1": "A"}})
        res = eng._do_decision(gate, eng._load_state())
        self.assertEqual(res["next"], "s_back")  # B → 驳回路径

    def test_default_reject_marks_source(self):
        """预设为驳回键也照落（on_reject 路径），留痕可审计。"""
        eng = _mk_engine({"steps": [dict(GATE)],
                          "decision_defaults": {"g1": "B"}})
        st = eng._load_state()
        res = eng._do_decision(dict(GATE), st)
        self.assertEqual(res["next"], "s_back")
        self.assertEqual(st["history"][-1]["source"], "auto_default")


if __name__ == "__main__":
    unittest.main()
