# -*- coding: utf-8 -*-
"""E1/E2（20260920）封面 AI 出图步验收。

E1：模板 steps 含 ww_cover_ai，next 链 ww_layout→ww_cover_ai→ww_check_render→ww_upload
    完整；test 模式对 ww_cover_ai mock 短路（不真调 bl）。
E2：建 ww_cover_ai 卡时 body 携带权威标题（与 ww_upload 同口径）。
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

CAND_1 = "12306拒绝了133万张票，被拒的不全是黄牛？"
CAND_MD = f"""---
title: "候选呈报"
---

## 候选标题矩阵

| # | 候选标题 |
|---|---|
| 1 | {CAND_1} |
"""


class TestCoverAiStep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_inst = ww_engine.INSTANCES_DIR
        self._orig_exch = ww_engine.EXCHANGE_DIR
        ww_engine.INSTANCES_DIR = self.tmp / "instances"
        ww_engine.EXCHANGE_DIR = self.tmp / "exchange"
        (ww_engine.EXCHANGE_DIR / "artifacts").mkdir(parents=True)
        ww_engine.INSTANCES_DIR.mkdir(parents=True)
        self.tpl = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))

    def tearDown(self):
        ww_engine.INSTANCES_DIR = self._orig_inst
        ww_engine.EXCHANGE_DIR = self._orig_exch

    def _eng(self, name="t-cover", mode="live"):
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = name
        eng.mode = mode
        eng.template = self.tpl
        eng.instance_path = ww_engine.INSTANCES_DIR / f"{name}.json"
        return eng

    def test_template_has_cover_ai_and_full_next_chain(self):
        """E1：next 链 ww_layout→ww_cover_ai→ww_check_render→ww_upload 完整。"""
        nxt = {s["id"]: s.get("next") for s in self.tpl["steps"] if s.get("next")}
        ids = [s["id"] for s in self.tpl["steps"]]
        self.assertIn("ww_cover_ai", ids)
        self.assertEqual(nxt["ww_layout"], "ww_cover_ai")
        self.assertEqual(nxt["ww_cover_ai"], "ww_check_render")
        self.assertEqual(nxt["ww_check_render"], "ww_upload")
        # 失败回排版步（旧 PIL 封面路径兜底）
        step = next(s for s in self.tpl["steps"] if s["id"] == "ww_cover_ai")
        self.assertEqual(step.get("on_fail"), "ww_layout")
        self.assertEqual(step.get("assignee"), "jingwei")

    def test_cover_ai_mock_short_circuit_in_test_mode(self):
        """E1：test 模式 ww_cover_ai 短路 mock（不建卡不真调 bl），推进到校验步。"""
        eng = self._eng(mode="test")
        st = {"instance": eng.instance, "step": "ww_cover_ai", "iteration": 0,
              "history": [], "status": "running", "dispatched": {}, "mode": "test"}
        eng._save_state(st)
        with mock.patch.object(eng, "_resolve_step_card") as mk_card, \
             mock.patch.object(eng, "_create_card") as mk_create:
            out = eng.run()
        st = self._load_state(eng)
        self.assertEqual(st["step"], "ww_check_render")   # mock 后照常推进
        self.assertTrue(any(h["action"] == "upload_mock" for h in st["history"]))
        self.assertIn("mock", out)
        mk_card.assert_not_called()
        mk_create.assert_not_called()

    def _load_state(self, eng):
        import json
        return json.loads(eng.instance_path.read_text(encoding="utf-8"))

    def test_cover_ai_card_body_carries_authoritative_title(self):
        """E2：live 模式建 ww_cover_ai 卡 → body_suffix 携带权威标题（候选1）。"""
        (ww_engine.EXCHANGE_DIR / "artifacts" / "001-hermes-ww-title-x.md").write_text(
            CAND_MD, encoding="utf-8")
        eng = self._eng()
        st = {"instance": eng.instance, "step": "ww_cover_ai", "iteration": 0,
              "history": [], "status": "running", "dispatched": {}, "mode": "live"}
        captured = {}

        def fake_create(sid, step, it, body_suffix=""):
            captured["body_suffix"] = body_suffix
            return "t_cover"
        step = next(s for s in self.tpl["steps"] if s["id"] == "ww_cover_ai")
        with mock.patch.object(eng, "_create_card", side_effect=fake_create), \
             mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng._do_dispatch(step, st)
        self.assertIn(CAND_1, captured["body_suffix"])
        self.assertIn("权威标题", captured["body_suffix"])
        self.assertIn("禁止 worker 自行取题", captured["body_suffix"])


if __name__ == "__main__":
    unittest.main()
