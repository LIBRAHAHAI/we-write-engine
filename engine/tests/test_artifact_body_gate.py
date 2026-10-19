"""t_a6da5fa1 回归：产物「正文非空」硬门（空壳产物视同未交付）。

缺陷实证（2026-09-21 ww_selection iter4）：087 空壳（正文仅 exwrite 骨架占位注释、
frontmatter status=done）通过 R5.2 双验证落盘，下游按 glob 取 mtime 最新产物即取到
空文件；083 同源（先建占位、25min 后才补正文，窗口期内 done 但无正文）。

修复语义（引擎消费侧；016 批次一 #2 起阈值 200 + 剥 markdown 标题行）：
  - `artifact_body_ok(path)`：剥 frontmatter + HTML 注释 + 标题行 + 空白后
    有效字符 ≥ 200；
  - `_resolve_artifact()`：候选按 mtime 降序逐个判正文，取第一个合格的；全空壳 → None
    （视同未交付，屏障继续等待 / verdict_consume 按 changes_requested 打回，fail-closed）；
  - `_shell_artifacts()` + `_do_wait` 日志：空壳被拒收时显式报出根因，不静默变慢。

⚠️ 实弹纪律（conftest）：本用例全部落一次性 temp，零活跃板 / 零真实交付树写入。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
from engine import WeWriteEngine

SHELL_BODY = "\n# 占位\n\n<!-- 正文从这里开始；交付前把 status 改为 done -->\n"
# 016 #2：阈值 200 → 真产物基线同步加长（原 88 字符形态在新阈值下属空壳）
REAL_BODY = ("\n# 真产物\n\n" + "本段正文用于验证正文非空硬门对正常产物放行。" * 20 + "\n")

FM = ("---\n"
      "nnn: \"087\"\n"
      "title: \"ww_selection 选材定主线\"\n"
      "project: we-write-engine\n"
      "artifact_type: artifact\n"
      "status: done\n"
      "version: 0.1-draft\n"
      "created_at: \"2026-09-21T09:33:32+08:00\"\n"
      "updated_at: \"2026-09-21T09:33:32+08:00\"\n"
      "author: duanmu\n"
      "assignee: user\n"
      "---\n")


class ArtifactBodyGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ww_bodygate_"))
        self.art = self.tmp / "artifacts"
        self.art.mkdir()
        ww_engine.INSTANCES_DIR = self.tmp

    def _write(self, name: str, body: str, mtime: float | None = None) -> Path:
        fp = self.art / name
        fp.write_text(FM + body, encoding="utf-8")
        if mtime is not None:
            import os
            os.utime(fp, (mtime, mtime))
        return fp

    # ---- 判据 ----

    def test_shell_body_rejected(self):
        """骨架占位正文（087 原形态）→ 判空壳。"""
        fp = self._write("087-duanmu-ww-selection-article-4-iter4.md", SHELL_BODY)
        ok, reason = ww_engine.artifact_body_ok(fp)
        self.assertFalse(ok)
        self.assertIn("空壳产物", reason)
        self.assertIn("087-", reason)

    def test_frontmatter_only_rejected(self):
        """只有 frontmatter、正文全空 → 判空壳。"""
        fp = self._write("088-empty.md", "\n\n")
        self.assertFalse(ww_engine.artifact_body_ok(fp)[0])

    def test_tiny_body_rejected(self):
        """正文有效字符 < 阈值（20）→ 判空壳。"""
        fp = self._write("089-tiny.md", "\n短\n")
        self.assertFalse(ww_engine.artifact_body_ok(fp)[0])

    def test_real_body_passes(self):
        """正常产物（有效字符远大于阈值）→ 放行。"""
        fp = self._write("086-duanmu-ww-selection-article-4-iter4.md", REAL_BODY)
        self.assertTrue(ww_engine.artifact_body_ok(fp)[0])

    def test_body_without_frontmatter_passes(self):
        """无 frontmatter 的纯正文文件（.m4drill 型不适用）→ 按正文长度判定。"""
        fp = self.art / "nofm.md"
        fp.write_text(REAL_BODY, encoding="utf-8")
        self.assertTrue(ww_engine.artifact_body_ok(fp)[0])

    def test_io_error_not_treated_as_shell(self):
        """读盘失败不伪装成产物缺陷（交给调用方既有语义）。"""
        missing = self.art / "nope.md"
        ok, reason = ww_engine.artifact_body_ok(missing)
        self.assertTrue(ok)
        self.assertIn("读取失败", reason)

    # ---- 016 批次一 #2：剥标题行 + 阈值 200（实测分布钉边界）----

    def test_long_title_shell_rejected(self):
        """146/151 形态：长标题（+占位注释）+ 空正文 → 剥标题后 0 字符 → 判空壳。"""
        long_title = "# " + "这是一条很长的骨架标题用于复现146/151形态" * 6 + "\n"
        fp = self._write("146-shell-long-title.md", "\n" + long_title + "\n<!-- 占位 -->\n")
        ok, reason = ww_engine.artifact_body_ok(fp)
        self.assertFalse(ok)
        self.assertIn("空壳产物", reason)

    def test_136_form_stripped_titles_passes(self):
        """136 形态：剥标题后 6799 有效字符 → 判 True（200 阈值无边界争议）。"""
        body = ("\n# 流程标题（剥掉）\n\n" + "正文有效内容按136形态实测分布填充。" * 400 + "\n")
        fp = self._write("136-real-136form.md", body)
        ok, _ = ww_engine.artifact_body_ok(fp)
        self.assertTrue(ok)

    def test_threshold_is_200(self):
        """阈值单点断言：MIN_BODY_CHARS == 200（防回退）。"""
        self.assertEqual(ww_engine.MIN_BODY_CHARS, 200)
        boundary = "字" * 199
        fp = self._write("199-boundary.md", f"\n{boundary}\n")
        self.assertFalse(ww_engine.artifact_body_ok(fp)[0])
        fp2 = self._write("200-boundary.md", f"\n{'字' * 200}\n")
        self.assertTrue(ww_engine.artifact_body_ok(fp2)[0])

    # ---- 解析（_resolve_artifact）----

    def test_glob_skips_newest_shell_picks_real(self):
        """glob 命中最新的空壳 + 较旧的真产物 → 取真产物（不取空壳）。"""
        self._write("083-duanmu-ww-selection-article-4.md", REAL_BODY, mtime=1_700_000_000)
        shell = self._write("087-duanmu-ww-selection-article-4-iter4.md", SHELL_BODY,
                            mtime=1_800_000_000)
        self.assertTrue(shell.exists())
        got = ww_engine._resolve_artifact(str(self.art / "*-ww-selection*.md"))
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "083-duanmu-ww-selection-article-4.md")

    def test_glob_only_shell_returns_none(self):
        """glob 下只有空壳 → None（视同未交付，fail-closed）。"""
        self._write("087-duanmu-ww-selection-article-4-iter4.md", SHELL_BODY)
        self.assertIsNone(ww_engine._resolve_artifact(str(self.art / "*-ww-selection*.md")))

    def test_exact_path_shell_returns_none(self):
        """精确路径指向空壳 → None。"""
        fp = self._write("087-x.md", SHELL_BODY)
        self.assertIsNone(ww_engine._resolve_artifact(str(fp)))

    def test_exact_path_real_returns_path(self):
        fp = self._write("086-x.md", REAL_BODY)
        self.assertEqual(ww_engine._resolve_artifact(str(fp)), fp)

    def test_shell_artifacts_lists_shells(self):
        """_shell_artifacts 列出全部空壳候选（供日志显式报出）。"""
        self._write("083-real.md", REAL_BODY)
        self._write("087-shell.md", SHELL_BODY)
        shells = ww_engine._shell_artifacts(str(self.art / "*.md"))
        self.assertEqual([p.name for p in shells], ["087-shell.md"])

    # ---- 屏障日志（不静默）----

    def test_wait_log_reports_shell_reject(self):
        """屏障因空壳拒收而等待 → log 显式报出「空壳产物被拒收」。"""
        self._write("087-duanmu-ww-selection-article-4-iter4.md", SHELL_BODY)
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = "t-bodygate"
        eng.template = {"steps": []}
        step = {"id": "ww_mat_barrier", "type": ww_engine.T_WAIT,
                 "artifacts": [str(self.art / "*-ww-selection*.md")],
                 "next": "ww_selection"}
        state = {"instance": eng.instance, "step": "ww_mat_barrier", "iteration": 1,
                 "history": [], "status": "running", "dispatched": {}}
        out = eng._do_wait(step, state)
        self.assertIsNone(out["next"])
        self.assertIn("空壳产物被拒收", out["log"])

    def test_wait_log_clean_without_shell(self):
        """无空壳时日志保持原样（不引入噪声）→ 正常产物放行。"""
        self._write("083-real.md", REAL_BODY)
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = "t-bodygate2"
        eng.template = {"steps": []}
        step = {"id": "ww_mat_barrier", "type": ww_engine.T_WAIT,
                 "artifacts": [str(self.art / "*-real*.md")], "next": "ww_selection"}
        state = {"instance": eng.instance, "step": "ww_mat_barrier", "iteration": 1,
                 "history": [], "status": "running", "dispatched": {}}
        out = eng._do_wait(step, state)
        self.assertEqual(out["next"], "ww_selection")
        self.assertNotIn("空壳产物被拒收", out["log"])


if __name__ == "__main__":
    unittest.main()
