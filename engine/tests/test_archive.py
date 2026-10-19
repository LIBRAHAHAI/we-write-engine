# -*- coding: utf-8 -*-
"""test_archive.py — 066 §8 归档动作契约测试（卡 t_9ee0e7c0）。

逐条对齐 §8 四条验收：
  1. ww_done 归档产出 `Wechat-project/<当日日期>/<NN>-<slug>/`，8 标配件全在，
     formatted.html 与 exchange 源 md5 一致；
  2. 同日第二个项目 → 目录名序号 02-（NN = 当日已有项目数 + 1）；
  3. 项目库视图两层 glob `Wechat-project/*/*/meta.yaml` 能列出并点开 formatted.html；
  4. 归档 + 面板运行全程引擎目录零新增生成性文件（本测试全部落盘在 tmp_path 沙箱）。

真实数据（article-001 的 exchange 产物）只读使用；写盘一律进 tmp_path。
回归样本：真实成果区 `Wechat-project/2026-09-13/01-jiagecike/`（手工归档）→ 跳过不覆盖。

2026-09-14 交付树清理后，真实 exchange/artifacts 已空：依赖 article-001 全套产物的
用例类（needs_archive_baseline = True）改用归档基准回填的一次性 temp 只读副本
（conftest.backfill_artifacts_from_archive），归档目录全程只读。
"""
import importlib
import json
import os
import re
import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import archive  # noqa: E402
import tempfile  # noqa: E402
import shutil  # noqa: E402
import yaml  # noqa: E402

# 归档基准回填助手（conftest 已由 pytest 先行导入，此处取同一模块，无重复执行）
from tests.conftest import backfill_artifacts_from_archive  # noqa: E402

REAL_EXCHANGE = REPO_ROOT / "exchange"
ART = REAL_EXCHANGE / "artifacts"
VER = REAL_EXCHANGE / "verdicts"
REAL_WECHAT = Path("Wechat-project")
# 2026-09-26 三层布局迁移后样本新路径（<模板名>/<日期>/<NN-slug>）
SAMPLE_PROJECT = REAL_WECHAT / "hot-topic-commentary" / "2026-09-13" / "01-jiagecike"

TODAY = __import__("datetime").datetime.now().astimezone().strftime("%Y-%m-%d")
# 2026-09-26 成果区三层布局 <模板名>/<日期>/<NN-slug>；state={} 默认落默认模板层
TPL = "hot-topic-commentary"


class SandboxMixin:
    """每个用例一个一次性成果区根 + 空 instances 目录（env 驱动，无需 reload）。

    needs_archive_baseline = True 的用例类：artifacts 输入用归档基准
    （Wechat-project/2026-09-13/01-jiagecike）回填的一次性 temp 只读副本；
    其余用例类维持真实 artifacts 树（清理后即残留形态，断言不依赖其内容）。
    归档缺失时回填助手抛 SkipTest → 用例整体 skip（防归档迁移后假失败）。
    """

    needs_archive_baseline = False

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("WW_WECHAT_PROJECT_DIR", "WW_ARCHIVE_SLUG", "WW_ARCHIVE_ENABLED")}
        self.root = Path(tempfile.mkdtemp(prefix="ww_arc_")) / "wechat-project"
        self.inst = Path(tempfile.mkdtemp(prefix="ww_arc_inst_"))
        (self.root / TODAY).mkdir(parents=True, exist_ok=True)
        os.environ["WW_WECHAT_PROJECT_DIR"] = str(self.root)
        os.environ.pop("WW_ARCHIVE_SLUG", None)
        os.environ.pop("WW_ARCHIVE_ENABLED", None)
        if self.needs_archive_baseline:
            self.art = backfill_artifacts_from_archive(
                Path(tempfile.mkdtemp(prefix="ww_arc_art_")) / "artifacts")
        else:
            self.art = ART

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root.parent, ignore_errors=True)
        shutil.rmtree(self.inst, ignore_errors=True)
        if self.needs_archive_baseline:
            shutil.rmtree(self.art.parent, ignore_errors=True)

    # ---- helpers ----
    def _archive(self, instance="article-001", state=None, **kw):
        return archive.archive_instance(
            instance, state or {}, artifacts_dir=self.art, verdicts_dir=VER,
            instances_dir=self.inst, **kw)

    def _write_slug_override(self, instance, slug):
        (self.inst / f"{instance}.archive.json").write_text(
            json.dumps({"slug": slug}), encoding="utf-8")

    def _tree(self, root: Path) -> dict:
        out = {}
        for p in sorted(Path(root).rglob("*")):
            rel = str(p.relative_to(root)).replace("\\", "/")
            out[rel] = p.stat().st_size if p.is_file() else -1
        return out


# --------------------------------------------------------------- §8 验收 1

class TestArchiveCreate(SandboxMixin, unittest.TestCase):

    needs_archive_baseline = True   # 依赖 article-001 全套产物（归档基准回填）

    def test_creates_two_layer_dir_with_8_standard_files_and_md5(self):
        """验收1：三层路径（模板/日期/NN-slug）+ 8 标配件全在 + formatted.html md5 与源一致。"""
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        self.assertEqual(res["status"], "created", res["log"])
        self.assertEqual(res["date"], TODAY)
        self.assertEqual(res["seq"], "01")
        self.assertEqual(res["slug"], "jiagecike")
        self.assertEqual(res["slug_source"], "instances/article-001.archive.json")
        dest = Path(res["path"])
        self.assertEqual(dest, self.root / TPL / TODAY / "01-jiagecike")  # 三层路径
        for f in archive.STANDARD_FILES:
            self.assertTrue((dest / f).exists(), f"缺标配件: {f}")
        self.assertEqual(res["missing"], [])
        self.assertEqual(sorted(res["standard_present"]), sorted(archive.STANDARD_FILES))
        # md5 与归档基准回填的 artifacts 源一致（066 §8 验收1 原句）
        self.assertIs(res["md5_ok"], True)
        src_html = max(self.art.glob("*formatted.html"), key=archive._sort_key)
        self.assertEqual(archive.md5(dest / "formatted.html"), archive.md5(src_html))

    def test_meta_yaml_parses_and_carries_archive_chain(self):
        """验收1 附：meta.yaml 可 yaml.safe_load，含 archived_at + source_artifacts 回指。"""
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        v = archive.verify_meta(Path(res["path"]) / "meta.yaml")
        self.assertTrue(v["ok"], v["error"])
        d = v["data"]
        self.assertTrue(d["archived_at"])
        self.assertEqual(d["instance"], "article-001")
        self.assertTrue(d["source_artifacts"])
        # 回指 = exchange 文件名，且每个都实存于归档基准回填的 artifacts / 真实 verdicts
        for name in d["source_artifacts"]:
            base = name.rstrip("/")
            hit = list(self.art.glob(base)) + list(VER.glob(base))
            self.assertTrue(hit, f"source_artifacts 回指不存在: {name}")
        self.assertIn(f"{TODAY}/01-jiagecike", d["archive_dir"])
        self.assertTrue(re.match(r"^ZIm[\w-]+$", str(d["media_id"])))  # 上传报告解析

    def test_article_final_merges_title_and_strips_frontmatter(self):
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        text = (Path(res["path"]) / "article-final.md").read_text(encoding="utf-8")
        self.assertFalse(text.startswith("---"))
        self.assertEqual(len(re.findall(r"^#\s+", text, re.MULTILINE)), 1)  # 单 H1
        self.assertIn("房价刺客", text.splitlines()[0])

    def test_reviews_md_digests_all_available_reviews(self):
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        text = (Path(res["path"]) / "reviews.md").read_text(encoding="utf-8")
        src = archive.collect_sources("article-001", self.art, VER)
        self.assertEqual(len(re.findall(r"^##\s+", text, re.MULTILINE)), len(src["reviews"]))
        for kw in ("事实核查", "合规审", "价值审"):
            self.assertIn(kw, text)
        self.assertIn("approved", text)
        for e in src["reviews"]:
            self.assertIn(e["path"].name, text)  # 每条审校都留了来源文件名

    def test_preview_html_is_standalone_with_copy_button(self):
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        html = (Path(res["path"]) / "preview.html").read_text(encoding="utf-8")
        self.assertIn("复制到公众号", html)
        src_html = (Path(res["path"]) / "formatted.html").read_text(encoding="utf-8")
        self.assertIn(src_html[:200], html)  # 内联 formatted.html 原文

    def test_imgs_and_sources_json_copied(self):
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        dest = Path(res["path"])
        self.assertTrue((dest / "imgs" / "sources.json").exists())
        self.assertTrue((dest / "sources.json").exists())
        src_files = sorted(p.name for p in (self.art / "imgs-article-001").glob("*.png"))
        self.assertEqual(sorted(p.name for p in (dest / "imgs").glob("*.png")), src_files)


# --------------------------------------------------------------- §8 验收 2

class TestNaming(SandboxMixin, unittest.TestCase):

    def test_second_project_same_day_gets_02(self):
        """验收2：同日第二个项目 → 序号 02-（三层布局：模板层下按日期计数）。"""
        tpl_root = self.root / TPL
        self.assertEqual(archive.next_seq(TODAY, tpl_root), 1)
        self._write_slug_override("article-001", "jiagecike")
        first = self._archive()
        self.assertEqual(first["seq"], "01")
        self.assertEqual(archive.next_seq(TODAY, tpl_root), 2)
        second = self._archive(instance="article-002")
        self.assertEqual(second["seq"], "02", second["log"])
        self.assertEqual(Path(second["path"]).name, f"02-{second['slug']}")
        self.assertTrue(first["path"] != second["path"])

    def test_seq_counts_pre_existing_project_dirs(self):
        (self.root / TODAY / "01-jiagecike").mkdir(parents=True)
        (self.root / TODAY / "02-m4drill").mkdir(parents=True)
        (self.root / TODAY / "not-a-project").mkdir(parents=True)  # 非 NN- 形不计
        self.assertEqual(archive.next_seq(TODAY, self.root), 3)

    def test_slug_resolution_chain(self):
        self.assertEqual(archive.resolve_slug("x", {"slug": "fromstate"}, None, "标题"),
                         ("fromstate", "state.slug"))
        self.assertEqual(archive.resolve_slug("x", {}, None, "Hello World 2026"),
                         ("helloworld2026", archive.resolve_slug("x", {}, None,
                                                                 "Hello World 2026")[1]))
        self.assertEqual(archive.resolve_slug("ww-m4-drill", {}, None, "中文标题"),
                         ("wwm4drill", "实例名兜底（ww-m4-drill）"))
        os.environ["WW_ARCHIVE_SLUG"] = "ENV-Slug"
        try:
            self.assertEqual(archive.resolve_slug("x", {}, None, "t")[0], "envslug")
        finally:
            os.environ.pop("WW_ARCHIVE_SLUG", None)


# ------------------------------------------------ 幂等 / 不覆盖（本卡 body）

class TestIdempotentSkip(SandboxMixin, unittest.TestCase):

    needs_archive_baseline = True   # test_second_run 依赖全套产物（归档基准回填）

    def test_regression_against_real_manual_archive_skips(self):
        """回归样本：真实 2026-09-13/01-jiagecike/（article-001 手工归档）→ 跳过不覆盖。

        本用例把成果区根指回**真实** Wechat-project（其余用例走 tmp 沙箱），
        以便对 sample 做真实回归；断言真实树零改动（不覆盖）。
        """
        if not SAMPLE_PROJECT.is_dir():
            self.skipTest("真实回归样本不存在")
        os.environ["WW_WECHAT_PROJECT_DIR"] = str(REAL_WECHAT)
        before = self._tree(REAL_WECHAT)
        res = archive.archive_instance("article-001", {}, artifacts_dir=self.art,
                                       verdicts_dir=VER, instances_dir=self.inst)
        self.assertEqual(res["status"], "skipped_exists", res["log"])
        self.assertEqual(Path(res["path"]), SAMPLE_PROJECT)
        self.assertEqual(res["seq"], "01")
        self.assertIn("跳过不覆盖", res["log"])
        self.assertEqual(self._tree(REAL_WECHAT), before, "真实成果区被改动（不覆盖违约）")

    def test_second_run_same_instance_skips(self):
        self._write_slug_override("article-001", "jiagecike")
        first = self._archive()
        self.assertEqual(first["status"], "created")
        tree = self._tree(Path(first["path"]))
        again = self._archive()
        self.assertEqual(again["status"], "skipped_exists")
        self.assertEqual(Path(again["path"]), Path(first["path"]))
        self.assertEqual(self._tree(Path(first["path"])), tree, "重复归档改写了成果区")

    def test_existing_dir_never_overwritten(self):
        dest = self.root / TPL / TODAY / "01-jiagecike"
        dest.mkdir(parents=True)
        (dest / "keep.txt").write_text("别动我", encoding="utf-8")
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive()
        self.assertEqual(res["status"], "skipped_exists", res["log"])
        self.assertTrue((dest / "keep.txt").exists(), "已存在目录被覆盖")
        self.assertFalse((dest / "meta.yaml").exists())


class TestGuards(SandboxMixin, unittest.TestCase):

    def test_t_prefix_instances_disabled(self):
        self.assertFalse(archive.archive_enabled("t-m3-done"))
        res = self._archive(instance="t-m3-done")
        self.assertEqual(res["status"], "disabled")
        self.assertEqual(list(self.root.rglob("*")), [d for d in self.root.rglob("*")
                                                      if d == self.root / TODAY])

    def test_env_kill_switch(self):
        os.environ["WW_ARCHIVE_ENABLED"] = "0"
        self.assertFalse(archive.archive_enabled("article-001"))
        self.assertEqual(self._archive()["status"], "disabled")

    def test_dry_run_writes_nothing(self):
        self._write_slug_override("article-001", "jiagecike")
        res = self._archive(dry_run=True)
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(Path(res["path"]), self.root / TPL / TODAY / "01-jiagecike")
        self.assertFalse(Path(res["path"]).exists())

    def test_failure_degrades_without_raising(self):
        os.environ["WW_ARCHIVE_ENABLED"] = "0"   # 先确认关断路径不抛
        self.assertEqual(self._archive()["status"], "disabled")
        os.environ.pop("WW_ARCHIVE_ENABLED", None)
        # 正常路径：不存在的源目录也不抛（缺项记 missing）
        res = archive.archive_instance("ghost-999", {}, artifacts_dir=self.root / "nope",
                                      verdicts_dir=self.root / "nope",
                                      instances_dir=self.inst)
        self.assertIn(res["status"], ("created_incomplete", "created"))
        self.assertTrue(res["missing"])


# ----------------------------------------------------- §8 验收 4（引擎钩子）

class TestEngineHook(SandboxMixin, unittest.TestCase):

    def test_do_done_calls_archive_and_logs_in_brief(self):
        """ww_done 后钩子：归档结论入收官简报与 log；t- 实例自动关断。"""
        import engine as eng_mod
        inst_name = "arc-e2e-001"
        eng = eng_mod.WeWriteEngine.__new__(eng_mod.WeWriteEngine)
        eng.instance = inst_name
        eng.instance_path = self.inst / f"{inst_name}.json"
        eng.mode = "test"
        eng.template = yaml.safe_load((ENGINE_DIR / "config" / "templates" / "hot-topic-commentary.yaml").read_text(encoding="utf-8"))
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_done")
        state = {"instance": inst_name, "step": "ww_done", "iteration": 3, "history": [],
                 "status": "running", "dispatched": {}, "mode": "test"}
        res = eng._do_done(step, state)
        self.assertEqual(state["status"], "completed")
        self.assertIn("归档", res["log"])
        arc = eng._last_archive
        # 钩子测试的 exchange 是 conftest 空沙箱（无真实产物）→ 允许 created_incomplete；
        # 8/8 全齐的证据由 §8 验收1 专项（真实 article-001 产物）承担。
        self.assertTrue(arc["status"].startswith("created"), arc["log"])
        self.assertTrue((Path(arc["path"]) / "meta.yaml").exists())
        self.assertTrue((Path(arc["path"]) / "reviews.md").exists())
        brief = (eng_mod.EXCHANGE_DIR / "reports" / f"{inst_name}-closing-brief.md")
        self.assertTrue(brief.exists())
        self.assertIn("归档", brief.read_text(encoding="utf-8"))
        brief.unlink(missing_ok=True)
        (self.inst / f"{inst_name}.json").unlink(missing_ok=True)

    def test_do_done_t_instance_disabled_does_not_touch_root(self):
        import engine as eng_mod
        eng = eng_mod.WeWriteEngine.__new__(eng_mod.WeWriteEngine)
        eng.instance = "t-arc-guard"
        eng.instance_path = self.inst / "t-arc-guard.json"
        eng.mode = "test"
        eng.template = yaml.safe_load((ENGINE_DIR / "config" / "templates" / "hot-topic-commentary.yaml").read_text(encoding="utf-8"))
        step = next(s for s in eng.template["steps"] if s["id"] == "ww_done")
        state = {"instance": "t-arc-guard", "step": "ww_done", "iteration": 1, "history": [],
                 "status": "running", "dispatched": {}, "mode": "test"}
        res = eng._do_done(step, state)
        self.assertEqual(eng._last_archive["status"], "disabled")
        self.assertIn("归档[disabled]", res["log"])
        self.assertFalse(any((self.root / TODAY).glob("*")), "t- 实例写了成果区")
        (self.inst / "t-arc-guard.json").unlink(missing_ok=True)


# -------------------------------------------------- §8 验收 3（项目库视图）

class TestPanelTwoLayerGlob(SandboxMixin, unittest.TestCase):

    def _load_panel(self):
        os.environ["WW_WECHAT_PROJECT_DIR"] = str(self.root)
        os.environ.setdefault("WW_EXCHANGE_DIR", str(REAL_EXCHANGE))
        os.environ.setdefault("WW_INSTANCES_DIR", str(self.inst))
        import ww_panel
        return importlib.reload(ww_panel)

    def _seed_project(self):
        d = self.root / TODAY / "01-jiagecike"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.yaml").write_text(
            '---\ninstance: "article-001"\nfinal_title: "51亿罚单，治不好\\"房价刺客\\""\n'
            'archived_at: "2026-09-13T17:30:00+08:00"\n---\n', encoding="utf-8")
        (d / "formatted.html").write_text(
            '<section style="font-size:24px">房价刺客</section>', encoding="utf-8")
        (d / "article-final.md").write_text("# 51亿罚单\n", encoding="utf-8")
        return d

    def test_two_layer_glob_lists_project(self):
        """验收3：两层 glob `Wechat-project/*/*/meta.yaml` 列出项目。"""
        self._seed_project()
        panel = self._load_panel()
        projects = panel.build_projects()
        self.assertEqual(len(projects), 1, projects)
        p = projects[0]
        self.assertEqual((p["date"], p["seq"], p["slug"]), (TODAY, "01", "jiagecike"))
        self.assertIn("51亿罚单", p["title"])
        names = [f["name"] for f in p["files"]]
        self.assertIn("formatted.html", names)
        self.assertIn("meta.yaml", names)

    def test_instance_view_links_archived_project_and_opens_html(self):
        """验收3 附：实例 → 成果区回指，formatted.html 经 /api/artifact 可读（抽屉点开）。"""
        self._seed_project()
        panel = self._load_panel()
        hit = panel.find_archived_project("article-001")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["has_html"])
        self.assertEqual(hit["open_name"], "formatted.html")
        status, text = panel.read_artifact(hit["open"])
        self.assertEqual(status, 200)
        self.assertIn("房价刺客", text)
        self.assertIsNone(panel.find_archived_project("not-archived"))

    def test_project_without_meta_excluded(self):
        d = self.root / TODAY / "02-ghost"
        d.mkdir(parents=True, exist_ok=True)
        (d / "formatted.html").write_text("x", encoding="utf-8")
        panel = self._load_panel()
        self.assertEqual(panel.build_projects(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
