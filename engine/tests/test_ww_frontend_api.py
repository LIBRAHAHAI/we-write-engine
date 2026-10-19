# -*- coding: utf-8 -*-
"""B2/C1/C2（20260920）ww-frontend 后端 API 验收。

B2：/api/instances/{name} 详情 artifacts 条目全部含实例名后缀（与 ww_panel 同口径）。
C1：新实例 state 含 created_at；存量实例列表接口回退 ctime/mtime 不报错。
C2：hide/unhide 过滤、软删移入 .archive、open-folder 404 分支。

env（WW_INSTANCES_DIR / WW_EXCHANGE_DIR / WW_WECHAT_PROJECT_DIR）在 import api
前置入，保证 api.py 与懒加载 ww_panel 的模块级路径绑定都落在沙箱。
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent
BACKEND_DIR = REPO_ROOT / "ww-frontend" / "backend"

tpl_cache = {}


def _make_client(tmp_path, monkeypatch):
    """沙箱化导入 api 模块并返回 TestClient（每次全新 import，路径绑定沙箱）。"""
    monkeypatch.setenv("WW_ENGINE_DIR", str(ENGINE_DIR))
    monkeypatch.setenv("WW_INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setenv("WW_EXCHANGE_DIR", str(tmp_path / "exchange"))
    monkeypatch.setenv("WW_WECHAT_PROJECT_DIR", str(tmp_path / "wechat"))
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for mod in ("api", "ww_panel"):
        sys.modules.pop(mod, None)
    api = importlib.import_module("api")
    (tmp_path / "instances").mkdir(parents=True)
    (tmp_path / "exchange" / "artifacts").mkdir(parents=True)
    (tmp_path / "wechat").mkdir(parents=True)
    from fastapi.testclient import TestClient
    return api, TestClient(api.app)


def _write_instance(tmp_path, name, **over):
    st = {"instance": name, "step": "ww_topic", "iteration": 0, "history": [],
          "status": "running", "dispatched": {}, "mode": "test",
          "template": "hot-topic-commentary"}
    st.update(over)
    fp = tmp_path / "instances" / f"{name}.json"
    fp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return fp


def test_detail_artifacts_all_suffixed(tmp_path, monkeypatch):
    """B2：详情 artifacts 条目全部含实例名；他实例产物不混入。"""
    api, client = _make_client(tmp_path, monkeypatch)
    _write_instance(tmp_path, "article-003")
    art = tmp_path / "exchange" / "artifacts"
    (art / "064-athena-ww-polish-article-003.md").write_text("x", encoding="utf-8")
    (art / "055-athena-ww-polish-article-002.md").write_text("x", encoding="utf-8")
    r = client.get("/api/instances/article-003")
    assert r.status_code == 200
    arts = r.json()["artifacts"]
    names = [a["current"]["name"] for a in arts if a.get("current")]
    assert names, "至少命中本实例产物一组"
    assert all("article-003" in n for n in names)


def test_new_instance_summary_time_fields(tmp_path, monkeypatch):
    """C1/C2：state.created_at 直读；completed 状态回退 mtime；archived_dir/hidden 在。"""
    api, client = _make_client(tmp_path, monkeypatch)
    _write_instance(tmp_path, "inst-a", created_at="2026-09-20T10:00:00+08:00")
    _write_instance(tmp_path, "inst-b", status="completed", step="ww_done")
    rows = {r["instance"]: r for r in client.get("/api/instances").json()}
    assert rows["inst-a"]["created_at"] == "2026-09-20T10:00:00+08:00"
    assert rows["inst-b"]["created_at"]           # 存量回退 st_ctime 不为空
    assert rows["inst-b"]["completed_at"]         # completed 回退 st_mtime
    assert rows["inst-a"]["completed_at"] is None
    assert rows["inst-a"]["archived_dir"] is None
    assert rows["inst-a"]["hidden"] is False


def test_hide_unhide_and_filter(tmp_path, monkeypatch):
    api, client = _make_client(tmp_path, monkeypatch)
    _write_instance(tmp_path, "inst-h")
    r = client.post("/api/instances/inst-h/hide")
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert client.get("/api/instances").json() == []           # 默认过滤
    rows = client.get("/api/instances?include_hidden=1").json()
    assert [r["instance"] for r in rows] == ["inst-h"]
    assert rows[0]["hidden"] is True
    assert (tmp_path / "instances" / "inst-h.hidden").exists()
    r = client.post("/api/instances/inst-h/unhide")
    assert r.json()["hidden"] is False
    assert [r["instance"] for r in client.get("/api/instances").json()] == ["inst-h"]
    assert not (tmp_path / "instances" / "inst-h.hidden").exists()


def test_soft_delete_moves_into_archive(tmp_path, monkeypatch):
    api, client = _make_client(tmp_path, monkeypatch)
    inst_dir = tmp_path / "instances"
    _write_instance(tmp_path, "inst-del")
    (inst_dir / "inst-del.decision").write_text("A", encoding="utf-8")
    (inst_dir / "inst-del.hidden").write_text("", encoding="utf-8")
    (inst_dir / ".archive").mkdir()
    r = client.delete("/api/instances/inst-del")
    assert r.status_code == 200
    body = r.json()
    assert body["moved"] == ["inst-del.json", "inst-del.decision", "inst-del.hidden"]
    dest = Path(body["archived_to"])
    assert dest.parent == inst_dir / ".archive"
    assert (dest / "inst-del.json").exists()
    assert not (inst_dir / "inst-del.json").exists()
    assert client.get("/api/instances").json() == []           # 列表不再出现
    assert client.get("/api/instances/inst-del").status_code == 404


def test_open_folder_404_when_no_dir(tmp_path, monkeypatch):
    api, client = _make_client(tmp_path, monkeypatch)
    _write_instance(tmp_path, "inst-o")
    (tmp_path / "exchange" / "artifacts").rmdir()   # 无 artifacts 目录
    r = client.post("/api/instances/inst-o/open-folder")
    assert r.status_code == 404


def test_open_folder_opens_artifacts_dir(tmp_path, monkeypatch):
    import os as _os
    api, client = _make_client(tmp_path, monkeypatch)
    _write_instance(tmp_path, "inst-o2")
    opened = {}
    monkeypatch.setattr(_os, "startfile",
                        lambda p: opened.setdefault("path", p), raising=False)
    r = client.post("/api/instances/inst-o2/open-folder")
    assert r.status_code == 200
    assert Path(r.json()["opened"]) == tmp_path / "exchange" / "artifacts"
    assert opened["path"] == r.json()["opened"]
