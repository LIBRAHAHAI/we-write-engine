"""test_m3_closeout.py — M3 行为级单测（013 Δ9/Δ10/Δ11）。

覆盖：
  - Δ9 C4 render_check：占位词拦截 / img 资产缺失 / 封面规格 / 干净样本零误报
  - Δ10 upload mode 分流：test 强制 mock（不建卡不触 API）/ report 标 mock:true
  - Δ9 _do_check 分支：PASS 推进 / FAIL 打回 on_fail / 超限 escalate
  - Δ11 done 原子收官：completed + 简报落盘 + 收官简报字段
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import engine as eng_mod  # noqa: E402
import render_check  # noqa: E402

GOOD_HTML = """<!doctype html><html><body>
<p>正文段落，无占位。</p>
<img src="https://mmbiz.qpic.cn/mmbiz_png/a1/640?wx_fmt=png"><img src="https://mmbiz.qpic.cn/mmbiz_png/a2/640?wx_fmt=png">
</body></html>"""


def _mkdir(files: dict[str, bytes]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    for name, data in files.items():
        p = Path(tmp.name) / name
        p.write_bytes(data)
    return tmp


def _png(width=900, height=383) -> bytes:
    """构造真实 PNG（PIL 可读）或最小头（PIL 缺失时走文件名降级）。"""
    try:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return b"\x89PNG\r\n\x1a\n" + b"0" * 2048  # >1KB 占位


class TestRenderCheck(unittest.TestCase):
    def test_clean_sample_zero_false_positive(self):
        with _mkdir({"formatted.html": GOOD_HTML.encode(),
                     "img1.png": _png(400, 300), "img2.png": _png(400, 300),
                     "cover-900x383.png": _png()}) as d:
            r = render_check.run_render_check(d)
            self.assertTrue(r["ok"], r["violations"])

    def test_placeholder_blocked(self):
        bad = GOOD_HTML.replace("正文段落", "此处待补图")
        with _mkdir({"formatted.html": bad.encode(),
                     "img1.png": _png(400, 300), "img2.png": _png(400, 300),
                     "cover-900x383.png": _png()}) as d:
            r = render_check.run_render_check(d)
            self.assertFalse(r["ok"])
            self.assertTrue(any("待补图" in v for v in r["violations"]))

    def test_missing_img_blocked(self):
        # 本地路径 img：img_assets_exist（缺失）与 wechat_reachable（微信不可达）双命中
        local_img = GOOD_HTML.replace("https://mmbiz.qpic.cn/mmbiz_png/a1/640?wx_fmt=png", "img1.png") \
                             .replace("https://mmbiz.qpic.cn/mmbiz_png/a2/640?wx_fmt=png", "img2.png")
        with _mkdir({"formatted.html": local_img.encode(),  # img1/img2 不存在
                     "cover-900x383.png": _png()}) as d:
            r = render_check.run_render_check(d)
            self.assertFalse(r["ok"])
            self.assertTrue(any("缺失" in v for v in r["violations"]))
            self.assertTrue(any("微信不可达" in v for v in r["violations"]))

    def test_bad_cover_spec_blocked(self):
        with _mkdir({"formatted.html": GOOD_HTML.encode(),
                     "cover-800x400.png": _png(800, 400)}) as d:
            r = render_check.run_render_check(d)
            self.assertFalse(r["ok"])
            self.assertTrue(any("封面" in v for v in r["violations"]))

    def test_no_html_blocked(self):
        with _mkdir({"cover-900x383.png": _png()}) as d:
            r = render_check.run_render_check(d)
            self.assertFalse(r["ok"])


class TestUploadModeSplit(unittest.TestCase):
    """Δ10：test 实例上传强制 mock。"""

    def test_test_mode_mocks_upload(self):
        eng = eng_mod.WeWriteEngine("t-m3-mock", mode="test")
        # Z8/025+031：走 dispatch 的用例必须按 conftest 纪律隔离——
        # mock 卡通道两函数，隔离不押守卫行（守卫回归时本用例红而非真建卡）。
        eng._resolve_step_card = lambda sid: None
        eng._create_card = lambda sid, step, it, body_suffix="": (_ for _ in ()).throw(
            AssertionError("test 模式 upload 必须 mock 短路，不应走到建卡分支"))
        step = {"id": "ww_upload", "next": "ww_closing_gate"}
        state = {"instance": "t-m3-mock", "step": "ww_upload", "iteration": 0,
                 "history": [], "status": "running", "dispatched": {}}
        res = eng._do_dispatch(step, state)
        self.assertEqual(res["next"], "ww_closing_gate")
        self.assertIn("mock", res["log"])
        uploads = state["uploads"]
        self.assertTrue(uploads["mock"])
        self.assertTrue(any(h["action"] == "upload_mock" for h in state["history"]))

    def test_default_mode_is_test(self):
        eng = eng_mod.WeWriteEngine("t-m3-default")
        self.assertEqual(eng.mode, "test")  # P3 防呆：缺省不触真箱


class TestModePersistence(unittest.TestCase):
    """Z5/025：mode 落实例 state + 冲突拒跑 fail-closed。全部用 temp 实例，零活跃板触碰。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.orig_instances = eng_mod.INSTANCES_DIR
        eng_mod.INSTANCES_DIR = Path(self.tmp.name)

    def tearDown(self):
        eng_mod.INSTANCES_DIR = self.orig_instances

    def test_mode_persisted_and_followed(self):
        eng = eng_mod.WeWriteEngine("t-z5-live", mode="live")
        st = eng._load_state()  # 首跑落 state.mode=live
        self.assertEqual(st["mode"], "live")
        eng2 = eng_mod.WeWriteEngine("t-z5-live", mode=None)  # 无参续跑跟随 state
        st2 = eng2._load_state()
        self.assertEqual(eng2.mode, "live")  # Z5 核心断言：live 不被降级

    def test_mode_conflict_rejects(self):
        eng = eng_mod.WeWriteEngine("t-z5-conflict", mode="live")
        eng._load_state()
        eng2 = eng_mod.WeWriteEngine("t-z5-conflict", mode="test")  # 显式冲突
        with self.assertRaises(ValueError):
            eng2._load_state()

    def test_upload_not_mocked_in_live(self):
        eng = eng_mod.WeWriteEngine("t-z5-liveup", mode="live")
        st = eng._load_state()
        # live 模式下 upload 步不进 mock 短路（走真卡分支：mock _create_card 拦截）
        calls = []
        eng._resolve_step_card = lambda sid: None
        eng._create_card = lambda sid, step, it, body_suffix="": calls.append(sid) or "t_fake"
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_upload")
        res = eng._do_dispatch(step, st)
        self.assertEqual(calls, ["ww_upload"])  # 建卡而非 mock（后续无 done 卡故留步）


class TestCheckRejectHint(unittest.TestCase):
    """Z6/025：C4 FAIL 注单进返工卡。"""

    def test_fix_hint_contains_violations(self):
        eng = eng_mod.WeWriteEngine("t-z6", mode="test")
        import render_check as rc
        orig = rc.run_render_check
        rc.run_render_check = lambda d, rules=None: {"ok": False,
                                         "violations": ["占位词命中 待补图"],
                                         "warns": []}
        try:
            step = next(s for s in eng.template["steps"] if s["id"] == "ww_check_render")
            st = {"instance": "t", "step": "ww_check_render", "iteration": 0,
                  "history": [], "status": "running", "dispatched": {}}
            eng._do_check(step, st)
        finally:
            rc.run_render_check = orig
        self.assertIn("待补图", eng._last_check_reject)
        # reopen 分支拼装：ww_layout 打回时 fix_hint 含注单
        fix_hint = "本轮为打回重做：只做增量修复，勿全量重跑"
        if eng._last_check_reject:
            fix_hint += "\n" + eng._last_check_reject
        self.assertIn("待补图", fix_hint)


class TestMockUploadIsolation(unittest.TestCase):
    """Z7/025：mock 上传产物禁入 NNN 审计空间。"""

    def test_mock_report_goes_to_reports_dir(self):
        eng = eng_mod.WeWriteEngine("t-z7", mode="test")
        st = {"instance": "t-z7", "step": "ww_upload", "iteration": 0,
              "history": [], "status": "running", "dispatched": {}}
        step = {"id": "ww_upload", "next": "ww_closing_gate"}
        # Z7 + G-1/031：落盘走 EXCHANGE_DIR（env 可重定向），断言 NNN 空间零新增
        artifacts_dir = eng_mod.EXCHANGE_DIR / "artifacts"
        artifacts_before = set(artifacts_dir.glob("*"))
        res = eng._mock_upload(step, st)
        artifacts_after = set(artifacts_dir.glob("*"))
        self.assertEqual(artifacts_before, artifacts_after)  # NNN 空间零新增
        mock_rep = eng_mod.EXCHANGE_DIR / "reports" / "t-z7-upload-mock.md"
        self.assertTrue(mock_rep.exists())
        self.assertIn("mock: true", mock_rep.read_text(encoding="utf-8"))
        mock_rep.unlink(missing_ok=True)  # 测试自清理


class TestCheckStep(unittest.TestCase):
    def _check_step(self):
        for s in eng_mod.WeWriteEngine("t-m3-ck").template["steps"]:
            if s["id"] == "ww_check_render":
                return s
        raise KeyError

    def test_pass_advances(self, ):
        eng = eng_mod.WeWriteEngine("t-m3-ck")
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: None
        try:
            import render_check as rc
            orig_run = rc.run_render_check
            rc.run_render_check = lambda d, rules=None: {"ok": True, "violations": [], "warns": []}
            try:
                st = {"instance": "t", "step": "ww_check_render", "iteration": 0,
                      "history": [], "status": "running", "dispatched": {}}
                res = eng._do_check(self._check_step(), st)
                self.assertEqual(res["next"], "ww_upload")
            finally:
                rc.run_render_check = orig_run
        finally:
            eng_mod._resolve_artifact = orig

    def test_fail_rejects_to_layout(self):
        eng = eng_mod.WeWriteEngine("t-m3-ck")
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: None
        try:
            import render_check as rc
            orig_run = rc.run_render_check
            rc.run_render_check = lambda d, rules=None: {"ok": False,
                                             "violations": ["img 资产缺失: img1.png"],
                                             "warns": []}
            try:
                st = {"instance": "t", "step": "ww_check_render", "iteration": 0,
                      "history": [], "status": "running", "dispatched": {}}
                res = eng._do_check(self._check_step(), st)
                self.assertEqual(res["next"], "ww_layout")
            finally:
                rc.run_render_check = orig_run
        finally:
            eng_mod._resolve_artifact = orig

    def test_over_retry_escalates(self):
        eng = eng_mod.WeWriteEngine("t-m3-ck")
        orig = eng_mod._resolve_artifact
        eng_mod._resolve_artifact = lambda p: None
        try:
            import render_check as rc
            orig_run = rc.run_render_check
            rc.run_render_check = lambda d, rules=None: {"ok": False, "violations": ["X"], "warns": []}
            try:
                st = {"instance": "t", "step": "ww_check_render", "iteration": 0,
                      "history": [{"step": "ww_check_render", "action": "check_render"}] * 3,
                      "status": "running", "dispatched": {}}
                res = eng._do_check(self._check_step(), st)
                self.assertIsNone(res["next"])
                self.assertTrue(any(h["action"] == "check_escalate" for h in st["history"]))
            finally:
                rc.run_render_check = orig_run
        finally:
            eng_mod._resolve_artifact = orig


class TestAtomicClosing(unittest.TestCase):
    """Δ11：done 原子收官——completed + 简报 + 不留中间态。"""

    def test_done_sets_completed_and_writes_brief(self):
        eng = eng_mod.WeWriteEngine("t-m3-done")
        state = {"instance": "t-m3-done", "step": "ww_done", "iteration": 3,
                 "history": [{"step": "ww_gate_topic", "action": "decision", "decision": "1"},
                             {"step": "ww_upload", "action": "upload_mock"}],
                 "status": "running", "dispatched": {},
                 "uploads": {"mock": True, "draft_media_id": None}}
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_done")
        # G1/031：调用序真断言——终态落盘(_save_state 含 completed)必须先于简报写盘。
        # 事后存在性检查对 M4 倒置免疫（简报先写则事后检查必过），只有调用序能抓。
        order = []
        orig_save = eng._save_state
        orig_write = eng._write_closing_brief

        def spy_save(st):
            if st.get("status") == "completed":
                order.append("completed_saved")
            return orig_save(st)

        def spy_write(b):
            order.append("brief_written")
            return orig_write(b)

        eng._save_state = spy_save
        eng._write_closing_brief = spy_write
        eng._do_done(step, state)
        eng._save_state = orig_save
        eng._write_closing_brief = orig_write
        self.assertEqual(order, ["completed_saved", "brief_written"],
                         f"收官顺序倒置（M4）：{order}")
        self.assertEqual(state["status"], "completed")
        # 简报落盘验证（G-1/031：走 EXCHANGE_DIR，conftest 重定向 temp，零写真实树）
        brief = eng_mod.EXCHANGE_DIR / "reports" / "t-m3-done-closing-brief.md"
        self.assertTrue(brief.exists())
        content = brief.read_text(encoding="utf-8")
        self.assertIn("draft_media_id", content)
        self.assertIn("4门拍板", content)
        brief.unlink(missing_ok=True)  # 测试自清理

    def test_brief_contains_intercept_count(self):
        eng = eng_mod.WeWriteEngine("t-m3-done2")
        state = {"instance": "t", "step": "ww_done", "iteration": 5,
                 "history": [{"step": "ww_factcheck", "action": "verdict_reject"},
                             {"step": "ww_review_barrier", "action": "verdict_gate_reject"}],
                 "status": "running", "dispatched": {}}
        brief = eng._closing_brief(state)
        self.assertIn("verdict 打回拦截: 2 次", brief)

    def test_closing_order_guard(self):
        """G1/025：completed ⇒ 简报已落盘（顺序倒置变红）。"""
        eng = eng_mod.WeWriteEngine("t-g1")
        state = {"instance": "t-g1", "step": "ww_done", "iteration": 1,
                 "history": [], "status": "running", "dispatched": {}}
        self.assertTrue(eng.closing_order_guard(state))  # 未到终态不适用
        # completed 但简报未落盘（顺序倒置场景）→ 守卫 False
        state["status"] = "completed"
        brief = eng_mod.EXCHANGE_DIR / "reports" / "t-g1-closing-brief.md"
        brief.unlink(missing_ok=True)
        self.assertFalse(eng.closing_order_guard(state))
        # 正常收官后 → True
        eng._write_closing_brief(eng._closing_brief(state))
        self.assertTrue(eng.closing_order_guard(state))
        brief.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
