# -*- coding: utf-8 -*-
"""A1①（20260920）权威标题机械化验收：upload/cover 建卡 body 必带引擎解析的权威标题。

任务书验收：
  test_upload_card_carries_authoritative_title：
    a) 拍板未提标题时，卡 body 标题 == 候选1 文本（非 polish H1）；
    b) 拍板 note 含「候选2」时，卡 body 标题 == 候选2 文本；
    c) 无候选文件时回退 split_article(polish) 且 body 注明来源。
  另覆盖：候选解析器（表格/编号列表两格式）、解析全失败 fail-closed、
  重开拍 fix_hint 补投递、ww_cover_ai 同口径（E2）。

测试隔离：EXCHANGE_DIR/INSTANCES_DIR 重定向 temp；dispatch 用例 mock
_resolve_step_card + _create_card（conftest 实弹纪律）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
from engine import WeWriteEngine, _parse_title_candidates

PIPELINE = Path(__file__).parent.parent / "config" / "templates" / "hot-topic-commentary.yaml"

CAND_1 = "12306拒绝了133万张票，被拒的不全是黄牛？"
CAND_2 = "抢不到票，可能不怪手慢：算法分不清黄牛，和替你买票的亲戚"
POLISH_H1 = "旧原稿标题绝不能成为上传标题"

CAND_MD_TABLE = f"""---
title: "ww_headline 5 候选标题呈报"
---

## ② 候选标题矩阵（2 条）

| # | 候选标题 | 方法 | 字数 |
|---|---|---|---:|
| 1 | {CAND_1} | 数字反差 | 24 |
| 2 | {CAND_2} | 隐藏机制 | 28 |

## 评分分项

| 1 | 24 | 14 |
|---|---|---|
"""

CAND_MD_LIST = f"""---
title: "候选呈报"
---

## 候选标题

候选1：{CAND_1}
候选2：{CAND_2}
"""

POLISH_MD = f"""---
author: athena
---

# {POLISH_H1}

正文第一段。
"""


class AuthTitleBase(unittest.TestCase):
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

    def _eng(self, name="t-authtitle", mode="live") -> WeWriteEngine:
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = name
        eng.mode = mode
        eng.template = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        eng.instance_path = ww_engine.INSTANCES_DIR / f"{name}.json"
        return eng

    def _state(self, eng, step, **kw):
        st = {"instance": eng.instance, "step": step, "iteration": 0,
              "history": [], "status": "running", "dispatched": {},
              "mode": eng.mode}
        st.update(kw)
        eng._save_state(st)
        return st

    def _write_cands(self, text=CAND_MD_TABLE, name="001-hermes-ww-title-x.md"):
        (ww_engine.EXCHANGE_DIR / "artifacts" / name).write_text(
            text, encoding="utf-8")

    def _write_polish(self, name="002-athena-ww-polish-x.md"):
        (ww_engine.EXCHANGE_DIR / "artifacts" / name).write_text(
            POLISH_MD, encoding="utf-8")

    def _capture_create(self, eng):
        calls = {}

        def fake_create(sid, step, it, body_suffix=""):
            calls["sid"] = sid
            calls["body_suffix"] = body_suffix
            return "t_new"
        return calls, mock.patch.object(eng, "_create_card", side_effect=fake_create)


class TestUploadCardCarriesAuthoritativeTitle(AuthTitleBase):
    """任务书 A1 验收主用例（a/b/c 三断言）。"""

    def test_a_no_pinned_note_defaults_to_candidate1(self):
        eng = self._eng()
        self._write_cands()
        self._write_polish()
        self._state(eng, "ww_upload")
        calls, mk = self._capture_create(eng)
        with mk, mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng.run()
        body = calls["body_suffix"]
        self.assertIn(CAND_1, body)          # 候选1，非 polish H1
        self.assertNotIn(POLISH_H1, body)
        self.assertIn("候选1", body)          # 来源注明
        self.assertIn("ww-title", body)      # 文件名来源
        self.assertIn("权威标题", body)

    def test_b_note_candidate2_wins(self):
        eng = self._eng()
        self._write_cands()
        self._write_polish()
        self._state(eng, "ww_upload", history=[
            {"step": "ww_gate_final", "action": "decision", "decision": "C",
             "note": "标题选候选2 + 按题改内容"}])
        calls, mk = self._capture_create(eng)
        with mk, mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng.run()
        body = calls["body_suffix"]
        self.assertIn(CAND_2, body)
        self.assertNotIn(CAND_1, body)
        self.assertIn("拍板候选2", body)

    def test_c_no_candidates_falls_back_to_polish_h1_with_source(self):
        eng = self._eng()
        self._write_polish()  # 只造 polish，不造候选文件
        self._state(eng, "ww_upload")
        calls, mk = self._capture_create(eng)
        with mk, mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng.run()
        body = calls["body_suffix"]
        self.assertIn(POLISH_H1, body)
        self.assertIn("polish H1", body)     # 来源注明（不静默）
        self.assertIn("ww-polish", body)     # 文件名

    def test_total_failure_fails_closed(self):
        eng = self._eng()
        self._state(eng, "ww_upload")  # 无候选文件、无 polish 产物
        calls, mk = self._capture_create(eng)
        with mk, mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng.run()
        self.assertIn("权威标题解析失败，禁止上传", calls["body_suffix"])

    def test_explicit_title_text_in_note_wins(self):
        eng = self._eng()
        self._write_cands()
        self._write_polish()
        self._state(eng, "ww_upload", history=[
            {"step": "ww_gate_final", "action": "decision", "decision": "C",
             "note": "标题选：一个全新的手拍标题文本示例用于测试"}])
        calls, mk = self._capture_create(eng)
        with mk, mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng.run()
        self.assertIn("一个全新的手拍标题文本示例用于测试", calls["body_suffix"])
        self.assertIn("拍板指定标题文本", calls["body_suffix"])


class TestCoverStepSameInjection(AuthTitleBase):
    """E2：ww_cover_ai 与 ww_upload 同口径受益（模板步加入前后都走 sid 集合）。"""

    def test_cover_ai_card_carries_title(self):
        eng = self._eng()
        self._write_cands()
        step = {"id": "ww_cover_ai", "next": "ww_check_render"}
        self._state(eng, "ww_cover_ai")
        calls, mk = self._capture_create(eng)
        with mk, mock.patch.object(eng, "_resolve_step_card", return_value=None):
            eng._do_dispatch(step, dict(eng._load_state()))
        self.assertIn(CAND_1, calls["body_suffix"])
        self.assertIn("权威标题", calls["body_suffix"])

    def test_reopen_fix_hint_carries_title(self):
        eng = self._eng()
        self._write_cands()
        self._write_polish()
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_upload")
        st = self._state(eng, "ww_upload")  # dispatched 空 → done 卡视为回流重开
        captured = {}

        def fake_reopen(card_id, new_title=None, reason=None, mode=None, fix_hint=None):
            captured["fix_hint"] = fix_hint
            return True
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_u", "status": "done"}), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               side_effect=fake_reopen):
            eng._do_dispatch(step, st)
        self.assertIn(CAND_1, captured["fix_hint"])
        self.assertIn("权威标题", captured["fix_hint"])


class TestParseTitleCandidates(unittest.TestCase):
    def test_table_format_059_style(self):
        cands = _parse_title_candidates(CAND_MD_TABLE)
        self.assertEqual(cands, [CAND_1, CAND_2])

    def test_list_format(self):
        cands = _parse_title_candidates(CAND_MD_LIST)
        self.assertEqual(cands, [CAND_1, CAND_2])

    def test_scoring_table_numbers_filtered(self):
        cands = _parse_title_candidates(CAND_MD_TABLE)
        self.assertNotIn("24", cands)
        self.assertNotIn("14", cands)


if __name__ == "__main__":
    unittest.main()
