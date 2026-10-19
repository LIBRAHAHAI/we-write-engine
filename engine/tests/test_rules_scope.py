"""t_caa2bc28 回归：render_check 规则阶段错位根治。

a) layout 阶段合规产物（本地路径 img）→ rules 声明子集 PASS
b) 上传后产物无 mmbiz URL（本地图残留）→ wechat_img_domain 仍被拦
c) 声明消费反例：未知规则名 fail-fast；_do_check 真实读取 step["rules"]
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import render_check  # noqa: E402


def _png(width=900, height=383) -> bytes:
    try:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return b"\x89PNG\r\n\x1a\n" + b"0" * 2048


GOOD_LOCAL_HTML = (
    "<html><body><p>正文段落</p>"
    '<img src="imgs/01.png"><img src="imgs/01.png">'
    "<p>参考来源见文中灰色上标</p></body></html>"
)
UPLOADED_HTML_NO_MMBIZ = (
    "<html><body><p>正文段落</p>"
    '<img src="imgs/01.png">'
    "</body></html>"
)
# 排版步真实产物形态：本地图路径 + 全部其它规则合规（模拟 article-002 被误判产物）
LAYOUT_STAGE_HTML = (
    "<html><body><p>正文段落一</p><p>正文段落二</p>"
    '<img src="imgs/01-timeline-thirty-years.png">'
    '<img src="imgs/02-char-intro.png">'
    '<img src="imgs/03-map-song.png"></body></html>'
)


class TestRulesScopeRegression(unittest.TestCase):
    """卡 body 修复要求 1/2/4：rules 声明被真实消费 + 阶段拆分。"""

    def test_a_layout_stage_local_img_passes_with_declared_rules(self):
        """a) 排版阶段合规产物（本地路径 img 实存）→ 模板声明子集 PASS。"""
        declared = ["no_placeholder", "img_assets_exist", "cover_spec", "wechat_refs"]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "imgs").mkdir()
            (d / "imgs" / "01.png").write_bytes(_png(400, 300))
            (d / "formatted.html").write_text(GOOD_LOCAL_HTML, encoding="utf-8")
            (d / "cover-900x383.png").write_bytes(_png())
            r = render_check.run_render_check(d, rules=declared)
            self.assertTrue(r["ok"], r["violations"])

    def test_a2_layout_stage_product_like_article002_passes(self):
        """a2) article-002 形态产物（imgs/ 本地图）按声明子集必须 PASS（本卡根因反例）。"""
        declared = ["no_placeholder", "img_assets_exist", "cover_spec", "wechat_refs"]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "imgs").mkdir()
            for n in ("01-timeline-thirty-years.png", "02-char-intro.png",
                      "03-map-song.png"):
                (d / "imgs" / n).write_bytes(_png(400, 300))
            (d / "026-athena-ww-layout-article-002-formatted.html").write_text(
                LAYOUT_STAGE_HTML, encoding="utf-8")
            (d / "cover-900x383-article-002.png").write_bytes(_png())
            r = render_check.run_render_check(d, rules=declared)
            self.assertTrue(r["ok"], r["violations"])
            # 同产物 rules=None 旧口径仍 FAIL（③ 分支保留，旧口径不放宽）
            r_full = render_check.run_render_check(d)
            self.assertFalse(r_full["ok"])

    def test_b_post_upload_no_mmbiz_still_blocked(self):
        """b) 上传后产物 img 无 mmbiz URL → wechat_img_domain 仍拦（覆盖不放松）。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "imgs").mkdir()
            (d / "imgs" / "01.png").write_bytes(_png(400, 300))
            (d / "formatted.html").write_text(UPLOADED_HTML_NO_MMBIZ, encoding="utf-8")
            (d / "cover-900x383.png").write_bytes(_png())
            r = render_check.run_render_check(d, rules=["wechat_img_domain"])
            self.assertFalse(r["ok"])
            self.assertTrue(any("微信不可达" in v or "本地路径" in v
                                for v in r["violations"]))

    def test_b2_alias_wechat_reachable_maps_to_refs(self):
        """b2) 旧名 wechat_reachable 别名归一为 wechat_refs（不判 ③）。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "imgs").mkdir()
            (d / "imgs" / "01.png").write_bytes(_png(400, 300))
            (d / "formatted.html").write_text(GOOD_LOCAL_HTML, encoding="utf-8")
            (d / "cover-900x383.png").write_bytes(_png())
            r = render_check.run_render_check(d, rules=["wechat_reachable"])
            self.assertTrue(r["ok"], r["violations"])

    def test_c_unknown_rule_failfast(self):
        """c1) 声明消费反例：未知规则名 fail-fast，禁止静默漏判。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "formatted.html").write_text(GOOD_LOCAL_HTML, encoding="utf-8")
            with self.assertRaises(ValueError):
                render_check.run_render_check(d, rules=["no_such_rule"])

    def test_c2_do_check_consumes_step_rules(self):
        """c2) 引擎 _do_check 真实读取 step["rules"]（声明不再是死配置）。"""
        import engine as eng_mod

        eng = eng_mod.WeWriteEngine("t-rules-scope", mode="test")
        captured = {}

        fake = render_check.run_render_check

        def spy(target_dir, rules=None):
            captured["rules"] = rules
            return fake(target_dir, rules=rules)

        import render_check as rc
        rc.run_render_check = spy

        # 指向临时目录产物：制造一份排版步合规产物
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "imgs").mkdir()
            (d / "imgs" / "01.png").write_bytes(_png(400, 300))
            (d / "x-ww-layout-formatted.html").write_text(GOOD_LOCAL_HTML,
                                                          encoding="utf-8")
            (d / "cover-900x383.png").write_bytes(_png())
            eng._resolve_artifact = (lambda glob, _p=d / "x-ww-layout.md": _p)
            step = {"id": "ww_check_render", "target": "ww_layout",
                    "rules": ["no_placeholder", "img_assets_exist", "cover_spec",
                              "wechat_refs"],
                    "max_retry": 2, "on_fail": "ww_layout", "next": "ww_upload"}
            state = {"instance": "t-rules-scope", "step": "ww_check_render",
                     "iteration": 0, "history": [], "status": "running"}
            res = eng._do_check(step, state)
            rc.run_render_check = fake

            self.assertEqual(captured["rules"],
                             ["no_placeholder", "img_assets_exist", "cover_spec",
                              "wechat_refs"])
            self.assertEqual(res["next"], "ww_upload")
            self.assertIn("PASS", res["log"])
            self.assertTrue(
                any(h["action"] == "check_render" and h["detail"] == "PASS"
                    for h in state["history"]))

    def test_c3_template_declares_stage_rules(self):
        """c3) 模板声明与规则词表一致（wechat_refs，不再含排版步不可满足的 ③）。"""
        import yaml
        tpl = Path(__file__).resolve().parents[1] / "config/templates/hot-topic-commentary.yaml"
        data = yaml.safe_load(tpl.read_text(encoding="utf-8"))
        steps = {s["id"]: s for s in data["steps"]}
        declared = steps["ww_check_render"]["rules"]
        self.assertNotIn("wechat_img_domain", declared)
        self.assertIn("wechat_refs", declared)
        for r in declared:
            render_check._normalize_rules([r])  # 每条都须在词表内（不抛）


if __name__ == "__main__":
    unittest.main()
