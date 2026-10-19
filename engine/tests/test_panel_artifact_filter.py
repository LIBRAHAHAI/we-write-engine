# -*- coding: utf-8 -*-
"""B1（20260920）ww_panel.build_artifacts 按实例过滤验收。

任务书验收：WW_EXCHANGE_DIR 放两个实例的产物各一，
  - build_artifacts('article-003') 只含 article-003 文件；
  - build_artifacts('article-nonexistent') 返回空列表；
  - test_ww_panel.py 既有用例全过（另行全量验证）。
"""
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import yaml


def _tpl():
    return yaml.safe_load(
        (ENGINE_DIR / "config" / "templates" / "hot-topic-commentary.yaml")
        .read_text(encoding="utf-8"))


@pytest.fixture
def panel_two_instances(tmp_path, monkeypatch):
    """两个实例的产物混放同一 artifacts 目录（全局 glob 混排现场还原）。"""
    monkeypatch.setenv("WW_INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("WW_EXCHANGE_DIR", str(tmp_path / "exchange"))
    monkeypatch.setenv("WW_WECHAT_PROJECT_DIR", str(tmp_path / "wechat"))
    import ww_panel
    panel = __import__("importlib").reload(ww_panel)
    art = tmp_path / "exchange" / "artifacts"
    art.mkdir(parents=True)
    (tmp_path / "wechat").mkdir()
    # article-003 产物（md / html / 封面）
    (art / "064-athena-ww-polish-article-003.md").write_text("x", encoding="utf-8")
    (art / "athena-ww-layout-article-003-formatted.html").write_text("x", encoding="utf-8")
    (art / "cover-900x383-article-003.png").write_bytes(b"\x89PNG")
    # article-002 产物（md / html / 封面）
    (art / "055-athena-ww-polish-article-002.md").write_text("x", encoding="utf-8")
    (art / "athena-ww-layout-article-002-formatted.html").write_text("x", encoding="utf-8")
    (art / "cover-900x383-article-002.png").write_bytes(b"\x89PNG")
    return panel, tmp_path


def _state(tmp_path, name, monkeypatch=None):
    return {"instance": name, "step": "ww_polish", "iteration": 1,
            "history": [], "status": "running", "dispatched": {}, "mode": "test"}


def test_build_artifacts_filters_by_instance(panel_two_instances):
    panel, tmp_path = panel_two_instances
    out = panel.build_artifacts("article-003", _state(tmp_path, "article-003"), _tpl())
    names = [entry["current"]["name"] for entry in out]
    assert names, "article-003 应至少命中 润色/排版HTML/封面 三组"
    assert all("article-003" in n for n in names)
    assert not any("article-002" in n for n in names)
    kinds = {entry["kind"] for entry in out}
    assert {"md", "html", "img"} <= kinds  # 附加①HTML ②封面 同口径过滤生效


def test_build_artifacts_unknown_instance_empty(panel_two_instances):
    panel, tmp_path = panel_two_instances
    out = panel.build_artifacts("article-nonexistent",
                                _state(tmp_path, "article-nonexistent"), _tpl())
    assert out == []


def test_similar_instance_name_no_cross_capture(panel_two_instances):
    """边界匹配：-article-003 不得误吸 article-0033 的产物。"""
    panel, tmp_path = panel_two_instances
    art = tmp_path / "exchange" / "artifacts"
    (art / "090-athena-ww-polish-article-0033.md").write_text("x", encoding="utf-8")
    out = panel.build_artifacts("article-003", _state(tmp_path, "article-003"), _tpl())
    names = [entry["current"]["name"] for entry in out]
    assert not any("article-0033" in n for n in names)


# ---- 20260921 补验：详情抽屉/拍板参考/组列/项目库 的按实例口径 ----

def _panel_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("WW_INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("WW_EXCHANGE_DIR", str(tmp_path / "exchange"))
    monkeypatch.setenv("WW_WECHAT_PROJECT_DIR", str(tmp_path / "wechat"))
    import importlib
    import ww_panel
    return importlib.reload(ww_panel)


def test_step_artifact_files_scoped_by_instance(tmp_path, monkeypatch):
    """详情抽屉「成果」栏：只列本实例文件（user 报告的 ww_topic 串项目问题）。"""
    panel = _panel_reload(tmp_path, monkeypatch)
    art = tmp_path / "exchange" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "035-athena-ww-topic-candidates-article-003.md").write_text("x", encoding="utf-8")
    (art / "036-athena-ww-topic-candidates-article-004.md").write_text("x", encoding="utf-8")
    step = {"id": "ww_topic",
            "artifact": str(art / "*-ww-topic-candidates*.md")}
    names = [f["name"] for f in panel._step_artifact_files(step, "article-003")]
    assert names == ["035-athena-ww-topic-candidates-article-003.md"]
    assert panel._step_artifact_files(step, "article-999") == []


def test_glob_latest_for_scoped(tmp_path, monkeypatch):
    """拍板参考/组列：全局最新可能是他实例产物 → 必须取本实例最新。"""
    panel = _panel_reload(tmp_path, monkeypatch)
    art = tmp_path / "exchange" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    old = art / "055-athena-ww-polish-article-003.md"
    new_foreign = art / "099-athena-ww-polish-article-002.md"
    old.write_text("old", encoding="utf-8")
    new_foreign.write_text("newer", encoding="utf-8")
    os_utime = __import__("os").utime
    os_utime(old, (1_000_000_000, 1_000_000_000))
    os_utime(new_foreign, (2_000_000_000, 2_000_000_000))
    pat = str(art / "*-ww-polish*.md")
    hit = panel._glob_latest_for("article-003", pat)
    assert hit is not None and "article-003" in hit.name
    assert panel._glob_latest_for("article-999", pat) is None


def test_build_projects_carries_instance(tmp_path, monkeypatch):
    """项目库数据源带 instance 绑定（前端按当前实例过滤的依据）。"""
    panel = _panel_reload(tmp_path, monkeypatch)
    day = tmp_path / "wechat" / "2026-09-21"
    proj = day / "01-article4"
    proj.mkdir(parents=True)
    (proj / "meta.yaml").write_text(
        "---\ntitle: t\ninstance: article-4\nstatus: created\n---\n", encoding="utf-8")
    (proj / "article-final.md").write_text("x", encoding="utf-8")
    projects = panel.build_projects()
    assert len(projects) == 1
    assert projects[0]["instance"] == "article-4"
