# -*- coding: utf-8 -*-
"""20260921 立项预建成果目录回归：archive._archive 复用 meta status:created 的目录。

user 需求：article-4 立项即有正式规则命名的文件夹（<日期>/<NN>-<slug>/），
收官归档必须落在同一目录（不再按收官 slug 另建新目录）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import archive


class TestArchivePrecreated(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_root = os.environ.get("WW_WECHAT_PROJECT_DIR")
        os.environ["WW_WECHAT_PROJECT_DIR"] = str(self.tmp / "wechat")
        self.addCleanup(self._restore)
        self.root = self.tmp / "wechat"
        self.pre = self.root / "2026-09-21" / "01-articlex"
        self.pre.mkdir(parents=True)
        (self.pre / "meta.yaml").write_text(
            "---\ntitle: \"articlex（立项预建）\"\ninstance: articlex\n"
            "status: created\nseq: '01'\n---\n", encoding="utf-8")
        self.art = self.tmp / "artifacts"
        self.vd = self.tmp / "verdicts"
        self.art.mkdir()
        self.vd.mkdir()

    def _restore(self):
        if self._orig_root is None:
            os.environ.pop("WW_WECHAT_PROJECT_DIR", None)
        else:
            os.environ["WW_WECHAT_PROJECT_DIR"] = self._orig_root

    def test_archive_reuses_precreated_folder(self):
        """预建目录（status: created）→ 成品落同一目录，不另建新目录。"""
        res = archive.archive_instance("articlex", {},
                                       artifacts_dir=self.art, verdicts_dir=self.vd)
        self.assertIn(res["status"], ("created", "created_incomplete"))
        self.assertEqual(Path(res["path"]), self.pre)          # 同一目录
        self.assertEqual(res["slug"], "articlex")              # 目录名立项即定
        self.assertTrue((self.pre / "reviews.md").exists())    # 成品确实落入
        self.assertTrue((self.pre / "meta.yaml").exists())
        # 收官 meta 覆盖预建 meta（status 不再是 created → 二次归档幂等跳过）
        res2 = archive.archive_instance("articlex", {},
                                        artifacts_dir=self.art, verdicts_dir=self.vd)
        self.assertEqual(res2["status"], "skipped_exists")

    def test_completed_archive_still_skipped(self):
        """已完成归档（status != created）维持原幂等跳过不覆盖。"""
        done = self.root / "2026-09-20" / "01-done"
        done.mkdir(parents=True)
        (done / "meta.yaml").write_text(
            "---\ninstance: done-x\nstatus: uploaded\n---\n", encoding="utf-8")
        res = archive.archive_instance("done-x", {},
                                       artifacts_dir=self.art, verdicts_dir=self.vd)
        self.assertEqual(res["status"], "skipped_exists")
        self.assertEqual(len(list(done.iterdir())), 1)  # 未被写入


if __name__ == "__main__":
    unittest.main()
