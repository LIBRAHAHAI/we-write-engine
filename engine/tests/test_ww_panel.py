# -*- coding: utf-8 -*-
"""ww_panel 前面板契约测试（卡 t_d37ca712，066 §1-§6 验收标准逐条落成断言）。

设计口径：
  - 面板是只读仪表 + 三类决策文件写手；本测试用真数据（article-001 / ww-m4-drill）
    验证机械算法，用 temp 沙箱验证写盘与 HTTP 契约（不碰真实 instances/ 与活跃板）。
  - 066 歧义已由 athena 裁定：进度分母 = pipeline 排除 done 步的 19 节点；
    分子 = 当前 step 在 19 序中的 1-based 位次（当前计入），终态 done 步 = 19。
"""
import importlib
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

REAL_INSTANCES = ENGINE_DIR / "instances"


# ---------------------------------------------------------------- fixtures

def _load_panel(instances: Path, exchange: Path, wechat: Path, monkeypatch):
    monkeypatch.setenv("WW_INSTANCES_DIR", str(instances))
    monkeypatch.setenv("WW_EXCHANGE_DIR", str(exchange))
    monkeypatch.setenv("WW_WECHAT_PROJECT_DIR", str(wechat))
    import ww_panel
    return importlib.reload(ww_panel)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """纯沙箱：instances/exchange/wechat 三个空 temp 根，无真实数据。"""
    inst = tmp_path / "instances"
    inst.mkdir()
    exch = tmp_path / "exchange"
    (exch / "artifacts").mkdir(parents=True)
    (exch / "verdicts").mkdir()
    (exch / "reports").mkdir()
    wechat = tmp_path / "wechat"
    wechat.mkdir()
    panel = _load_panel(inst, exch, wechat, monkeypatch)
    return panel, inst, exch, wechat


@pytest.fixture
def realdata(tmp_path, monkeypatch, archived_exchange):
    """真数据沙箱：instances 用真实 JSON 的副本（只读）。

    2026-09-14 交付树清理后真实 exchange/artifacts 已空：exchange 改指归档基准
    （Wechat-project/2026-09-13/01-jiagecike）回填的一次性 temp 副本
    （conftest.archived_exchange），断言语义与清理前逐字节等价；归档目录全程只读。
    """
    inst = tmp_path / "instances"
    inst.mkdir()
    for name in ("article-001.json", "ww-m4-drill.json"):
        src = REAL_INSTANCES / name
        if src.exists():
            (inst / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    wechat = tmp_path / "wechat"
    wechat.mkdir()
    panel = _load_panel(inst, archived_exchange, wechat, monkeypatch)
    return panel, inst


def _write_state(inst_dir: Path, name: str, **over) -> None:
    st = {"instance": name, "step": None, "iteration": 0, "history": [],
          "status": "running", "dispatched": {}, "mode": "test"}
    st.update(over)
    (inst_dir / f"{name}.json").write_text(
        json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _view(panel, name):
    state = panel.build_state()
    for v in state["instances"]:
        if v["name"] == name:
            return v, state
    raise AssertionError(f"实例 {name} 不在 /api/state 输出里")


# ---------------------------------------------------------------- §1 进度总览

def test_article001_progress_20_of_20(realdata):
    """066 §1 验收1：article-001（completed @ ww_done）→ 20/20 = 100%（E1 后 20 节点）。"""
    panel, _ = realdata
    v, _ = _view(panel, "article-001")
    assert v["overview"]["total"] == 20
    assert v["overview"]["done_count"] == 20
    assert v["overview"]["percent"] == 100
    assert v["overview"]["stage"] == "排版发布"


def test_m4drill_progress_20_of_20(realdata):
    """066 §1 验收1：ww-m4-drill 同为 100%（E1 后 20 节点）。"""
    panel, _ = realdata
    v, _ = _view(panel, "ww-m4-drill")
    assert v["overview"]["done_count"] == 20
    assert v["overview"]["percent"] == 100


def test_ww_write_progress_8_of_20(sandbox):
    """066 §1 验收2：step=ww_write → 8/20 = 40%（E1 后分母 20）。"""
    panel, inst, _exch, _wp = sandbox
    hist = [{"step": s, "action": "dispatch_done"} for s in
            ("ww_topic", "ww_outline", "ww_mat_group", "ww_mat_barrier", "ww_mat_select")]
    _write_state(inst, "t-prog", step="ww_write", history=hist)
    v, _ = _view(panel, "t-prog")
    ov = v["overview"]
    assert (ov["done_count"], ov["total"]) == (8, 20), ov
    assert ov["percent"] == 40
    html = panel.render_html(panel.build_state())
    assert '"done_count": 8' in html          # 服务端内嵌状态快照（无浏览器也能断言）
    assert '"total": 20' in html
    assert "progress-text" in html and "progress-bar" in html


def test_dual_review_owners_both(sandbox):
    """066 §1 验收3：step=ww_dual_review → 同时显示 xuanwu 与 duanmu。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-own", step="ww_dual_review")
    v, _ = _view(panel, "t-own")
    assert set(v["overview"]["owners"]) == {"xuanwu", "duanmu"}


def test_rework_badges_m4drill(realdata):
    """066 §1 验收4：ww-m4-drill 返工步出现 rework 角标且带重开次数。"""
    panel, _ = realdata
    v, _ = _view(panel, "ww-m4-drill")
    mark = {n["id"]: n["rework"] for n in v["overview"]["nodes"]}
    assert len(mark) == 20                    # 进度条 = 20 格（E1 后；done 步不入格）
    for sid in ("ww_write", "ww_polish", "ww_factcheck", "ww_layout"):
        assert mark[sid] >= 1, (sid, mark)
    assert all(mark[s] == 0 for s in ("ww_topic", "ww_gate_topic", "ww_gate_outline"))
    html = panel.render_html(panel.build_state())
    assert "data-rework-count" in html


# ---------------------------------------------------------------- §2 skill 仪表

def test_polish_skills_humanizer_and_slop(realdata):
    """066 §2 验收1：ww_polish 行 = humanizer-zh + no-ai-slop，owner duanmu。"""
    panel, _ = realdata
    v, _ = _view(panel, "article-001")
    row = [r for r in v["skills"]["done"] if r["step"] == "ww_polish"]
    assert row, v["skills"]["done"]
    names = {s["skill"] for s in row[0]["skills"]}
    owners = {s["owner"] for s in row[0]["skills"]}
    assert names == {"humanizer-zh", "no-ai-slop"}
    assert owners == {"duanmu"}


def test_layout_skills_bound(realdata):
    """066 §2 验收2：ww_layout 行 = athena-article-illustrator + gzh-design（070 改名对齐：athena-gzh-typesetting → gzh-design）。"""
    panel, _ = realdata
    v, _ = _view(panel, "article-001")
    row = [r for r in v["skills"]["done"] if r["step"] == "ww_layout"]
    assert row
    assert {s["skill"] for s in row[0]["skills"]} == \
        {"athena-article-illustrator", "gzh-design"}
    assert {s["owner"] for s in row[0]["skills"]} == {"athena"}


def test_headline_current_and_factcheck_done(sandbox):
    """066 §2 验收3：step=ww_headline → ww_factcheck 在已完成栏、标题 skill 在当前栏。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_factcheck", "action": "dispatch_done"}]
    _write_state(inst, "t-sk", step="ww_headline", history=hist)
    v, _ = _view(panel, "t-sk")
    assert "ww_factcheck" in [r["step"] for r in v["skills"]["done"]]
    cur = v["skills"]["current"]
    assert cur["step"] == "ww_headline"
    assert {s["skill"] for s in cur["skills"]} == {"baokuan-title-generator"}
    # 2026-09-27 吸引力批次：ww_headline 改绑引擎源 {skill, source: engine}
    #（旧断言 owner==hermes 基于已替换的 owner 绑定，git 582521d 前快照可查旧值）
    assert cur["skills"][0]["owner"] == "引擎源"


def test_unbound_skill_rendered(sandbox):
    """066 §2 验收4：skills_map 无绑定的步渲染 "(无绑定 skill)"，不报错。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-unb", step="ww_outline")
    v, _ = _view(panel, "t-unb")
    assert v["skills"]["current"]["unbound"] is True
    assert v["skills"]["current"]["skills"] == []
    html = panel.render_html(panel.build_state())
    assert "(无绑定 skill)" in html


# ---------------------------------------------------------------- §3 拍板仪表

def test_decision_tags_three_forms(sandbox):
    """066 §3 验收1/2：auto_default→预设默认 ⚙；无 source→user 手选；C+note→带上意见。"""
    panel, inst, _e, _w = sandbox
    hist = [
        {"step": "ww_gate_topic", "action": "decision", "decision": "1"},
        {"step": "ww_gate_outline", "action": "decision", "decision": "A"},
        {"step": "ww_gate_final", "action": "decision", "decision": "C", "note": "测试意见"},
    ]
    _write_state(inst, "t-dec", step="ww_layout", history=hist,
                 iteration=3, status="running")
    v, _ = _view(panel, "t-dec")
    tags = {r["gate"]: r["tag"] for r in v["decisions"]["records"]}
    assert tags["ww_gate_topic"] == "user 手选"
    assert tags["ww_gate_outline"] == "user 手选"
    assert tags["ww_gate_final"] == "user 手选·带意见"
    note = [r for r in v["decisions"]["records"] if r["gate"] == "ww_gate_final"][0]
    assert note["note"] == "测试意见"
    # 预设默认标签
    hist2 = [{"step": "ww_gate_outline", "action": "decision",
              "decision": "A", "source": "auto_default"}]
    _write_state(inst, "t-dec2", step="ww_mat_group", history=hist2)
    v2, _ = _view(panel, "t-dec2")
    assert v2["decisions"]["records"][0]["tag"] == "预设默认 ⚙"
    html = panel.render_html(panel.build_state())
    assert "预设默认 ⚙" in html or "预设默认" in html


def test_decision_option_label_reverse_lookup(sandbox):
    """选项 label 按 options key 反查（key 不在表中原样显示）。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_gate_topic", "action": "decision", "decision": "A"}]
    _write_state(inst, "t-lbl", step="ww_outline", history=hist)
    v, _ = _view(panel, "t-lbl")
    rec = v["decisions"]["records"][0]
    assert rec["option_label"] == "选候选 1（同意默认首选）"
    assert rec["gate_label"] == "门1"


def test_pending_banner_gate_final(sandbox):
    """066 §3 验收3：step=ww_gate_final 且无 .decision → 横幅含 question 与选项。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-pend", step="ww_gate_final")
    v, _ = _view(panel, "t-pend")
    pend = v["decisions"]["pending"]
    assert pend and pend["step"] == "ww_gate_final"
    assert "终稿拍板" in pend["question"]
    keys = {o["key"] for o in pend["options"]}
    assert {"A", "B", "C"} <= keys
    html = panel.render_html(panel.build_state())
    assert "pending-decision" in html and "pending-question" in html


def test_pending_absent_after_decision_file(sandbox):
    """已写 .decision → 横幅消失（引擎消费在途，不再重复引导拍板）。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-pend2", step="ww_gate_final")
    (inst / "t-pend2.decision").write_text("A", encoding="utf-8")
    v, _ = _view(panel, "t-pend2")
    assert v["decisions"]["pending"] is None


# ---------------------------------------------------------------- §4 产物链

def test_artifact_polish_latest_055_with_history(realdata):
    """066 §4 验收1/5：润色稿 current=055（最大号），历史层含 049/052。

    B1（20260920）后展示层按实例名后缀过滤：article-001 归档基准中旧命名契约
    的无后缀产物（041-outline/051-draft/050-factcheck 等）不再混入本实例条目，
    原「≥10 条目」的全局混排口径由「suffixed 组 ≥7」取代（polish 断言不变）。
    """
    panel, _ = realdata
    v, _ = _view(panel, "article-001")
    arts = v["artifacts"]
    assert len(arts) >= 7, [a["step"] for a in arts]
    assert all("-article-001" in a["current"]["name"]
               for a in arts if a.get("current")), "B1：条目必须归属本实例"
    pol = [a for a in arts if a["step"] == "ww_polish"][0]
    assert pol["current"]["name"].startswith("055-"), pol["current"]
    hist_names = [h["name"] for h in pol["history"]]
    assert any(n.startswith("052-") for n in hist_names), hist_names
    assert any(n.startswith("049-") for n in hist_names), hist_names


def test_artifact_note_from_c_decision(sandbox):
    """066 §4 验收5：门3 C| 意见在"修改意见"条目可读（无需打开文件）。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_gate_final", "action": "decision", "decision": "C",
             "note": "第2节数据需补时点；结尾金句偏弱"}]
    _write_state(inst, "t-note", step="ww_polish", history=hist)
    v, _ = _view(panel, "t-note")
    notes = [a for a in v["artifacts"] if a.get("note_text")]
    assert notes and "第2节数据需补时点" in notes[0]["note_text"]


def test_artifact_path_whitelist_403(sandbox):
    """066 §4 验收4：白名单外路径 → 403（目录穿越阻挡）。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-wl", step="ww_write")
    port = _serve(panel)
    try:
        code, _ = _http("GET", f"http://127.0.0.1:{port}/api/artifact?path=../../etc/passwd")
        assert code == 403, code
        code2, _ = _http("GET", f"http://127.0.0.1:{port}/api/artifact?path=/etc/hosts")
        assert code2 == 403, code2
    finally:
        _stop(panel)


def test_artifact_whitelisted_ok(sandbox):
    """白名单内 md 可读（抽屉数据源）。"""
    panel, inst, exch, _w = sandbox
    target = exch / "artifacts" / "001-t-x-ww-draft.md"
    target.write_text("---\ntitle: t\n---\n\n正文\n", encoding="utf-8")
    _write_state(inst, "t-ok", step="ww_write")
    code, body = panel.read_artifact(str(target))
    assert code == 200 and "正文" in body


# ---------------------------------------------------------------- §5 警示窗

def test_no_warning_for_completed_article001(realdata):
    """066 §5 验收4：article-001 completed 无 escalate → 警示窗不渲染。"""
    panel, _ = realdata
    v, _ = _view(panel, "article-001")
    assert v["warnings"] == []
    html = panel.render_html(panel.build_state())
    assert 'warning-window[data-instance="article-001"]' not in html


def test_warning_from_verdict_escalate(sandbox):
    """066 §5 验收1/2：verdict_escalate → 警示窗含 step/action/≥2 建议 + data-hermes-send。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_factcheck", "action": "verdict_escalate",
             "detail": "factcheck_result=changes_requested",
             "log": "⚠🚨 verdict ww_factcheck: 连续 2 轮不通过"}]
    _write_state(inst, "t-warn", step="ww_factcheck", history=hist)
    v, _ = _view(panel, "t-warn")
    w = v["warnings"][0]
    assert w["step"] == "ww_factcheck" and w["action"] == "verdict_escalate"
    assert len(w["suggestions"]) >= 2
    assert "t-warn" in w["prompt"] and "ww_factcheck" in w["prompt"]
    html = panel.render_html(panel.build_state())
    assert "data-hermes-send=" in html
    assert "warning-suggestions" in html
    assert "hermes-probe" in html and "warning-ack" in html
    assert "window.hermes" in html


def test_warning_wait_stall_ge5(sandbox):
    """066 §5 自判②：连续 dispatch_wait ≥5 → wait_stall 警示（m4-drill 形态）。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_write", "action": "dispatch_wait", "log": "在途"}] * 5
    _write_state(inst, "t-stall", step="ww_write", history=hist)
    v, _ = _view(panel, "t-stall")
    assert any(w["action"] == "wait_stall" for w in v["warnings"])


def test_unattended_alert_report_written(sandbox):
    """卡补充需求③：unattended 文件存在时，排查 prompt 自动写入 reports/unattended-alerts-<inst>.md。"""
    panel, inst, exch, _w = sandbox
    hist = [{"step": "ww_factcheck", "action": "verdict_escalate",
             "detail": "factcheck_result=changes_requested", "log": "超限"}]
    _write_state(inst, "t-unatt", step="ww_factcheck", history=hist)
    (inst / "t-unatt.unattended").write_text("on\n", encoding="utf-8")
    v, _ = _view(panel, "t-unatt")
    assert v["unattended"] is True
    rep = exch / "reports" / "unattended-alerts-t-unatt.md"
    assert rep.exists(), "无人值守排查通道文件未落盘"
    txt = rep.read_text(encoding="utf-8")
    assert "t-unatt" in txt and "ww_factcheck" in txt


def test_unattended_absent_no_report(sandbox):
    """未开无人值守 → 不写告警报告（零生成性文件）。"""
    panel, inst, exch, _w = sandbox
    hist = [{"step": "ww_factcheck", "action": "verdict_escalate", "log": "超限"}]
    _write_state(inst, "t-nounatt", step="ww_factcheck", history=hist)
    _view(panel, "t-nounatt")
    assert not (exch / "reports" / "unattended-alerts-t-nounatt.md").exists()


# ---------------------------------------------------------------- §6 并行组

def test_m4drill_mat_group_five_cols_all_done(realdata):
    """066 §6 验收1：ww-m4-drill 五路组 5 列，负责人逐一对上，100%。"""
    panel, _ = realdata
    v, _ = _view(panel, "ww-m4-drill")
    grp = [g for g in v["groups"] if g["step"] == "ww_mat_group"][0]
    assert len(grp["columns"]) == 5
    assert grp["done"] == 5 and grp["total"] == 5
    got = {c["member"]: c["assignee"] for c in grp["columns"]}
    assert got == {"ww_mat_news": "jingwei", "ww_mat_history": "default",
                   "ww_mat_classic": "athena", "ww_mat_law": "xuanwu",
                   "ww_mat_econ": "duanmu"}
    assert all(c["percent"] == 100 for c in grp["columns"])


def test_dual_review_col_skills(realdata):
    """066 §6 验收2：双审组 2 列，ww_review_audit 列 skill 行两个 skill。"""
    panel, _ = realdata
    v, _ = _view(panel, "article-001")
    grp = [g for g in v["groups"] if g["step"] == "ww_dual_review"][0]
    assert len(grp["columns"]) == 2
    col = [c for c in grp["columns"] if c["member"] == "ww_review_audit"][0]
    assert {s["skill"] for s in col["skills"]} == \
        {"xuanwu-content-audit", "yuwen-publish-precheck"}
    assert {s["owner"] for s in col["skills"]} == {"xuanwu"}


def test_group_pending_four_of_five(sandbox):
    """066 §6 验收3：pending=["ww_mat_news"] → news<100、其余 100、标题 "4/5 完成"。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_mat_group", "action": "dispatch_group_wait",
             "pending": ["ww_mat_news"], "log": "1/5 未完成"}]
    _write_state(inst, "t-grp", step="ww_mat_group", history=hist)
    v, _ = _view(panel, "t-grp")
    grp = [g for g in v["groups"] if g["step"] == "ww_mat_group"][0]
    col = {c["member"]: c for c in grp["columns"]}
    assert col["ww_mat_news"]["percent"] < 100
    assert all(col[m]["percent"] == 100 for m in col if m != "ww_mat_news")
    assert grp["done"] == 4 and grp["total"] == 5
    html = panel.render_html(panel.build_state())
    assert '"done": 4' in html and '"total": 5' in html   # 标题 "4/5 完成" 的数据源
    assert " 完成" in html


def test_group_columns_have_two_sections(sandbox):
    """066 §6 验收4：每列 ①流程/负责人/完成度 与 ②skill 区均非空。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_mat_group", "action": "dispatch_group_wait",
             "pending": [], "log": "全 done"}]
    _write_state(inst, "t-grp2", step="ww_mat_group", history=hist)
    v, _ = _view(panel, "t-grp2")
    grp = [g for g in v["groups"] if g["step"] == "ww_mat_group"][0]
    for c in grp["columns"]:
        assert c["flow"] and c["assignee"] and isinstance(c["percent"], int)
        assert "unbound" in c
    html = panel.render_html(panel.build_state())
    assert "col-flow" in html and "col-owner" in html and "col-percent" in html \
        and "col-skill" in html


# ---------------------------------------------------------------- HTTP 契约

def _serve(panel):
    srv = panel.make_server("127.0.0.1", 0)  # 0 = 随机空闲端口
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    panel._test_server = srv
    return srv.server_address[1]


def _http(method, url, body=None, headers=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _stop(panel):
    srv = getattr(panel, "_test_server", None)
    if srv:
        srv.shutdown()


def test_etag_304_on_unchanged(sandbox):
    """token 纪律：轮询带 mtime 对比，未变返回 304。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-304", step="ww_write")
    port = _serve(panel)
    try:
        code, body = _http("GET", f"http://127.0.0.1:{port}/api/state")
        assert code == 200
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state")
        with urllib.request.urlopen(req, timeout=10) as r:
            etag = r.headers.get("ETag")
        assert etag
        code2, body2 = _http("GET", f"http://127.0.0.1:{port}/api/state",
                             headers={"If-None-Match": etag})
        assert code2 == 304, code2
        assert body2 in (b"", None, b" ")
    finally:
        _stop(panel)


def test_state_contains_instances_and_no_llm(sandbox):
    """/api/state 契约：顶层字段齐全，纯本地读取（不调 LLM/不 subprocess）。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-st", step="ww_topic")
    st = panel.build_state()
    for k in ("generated_at", "pipeline_total", "stages", "instances", "projects"):
        assert k in st, k
    v, _ = _view(panel, "t-st")
    for k in ("name", "status", "step", "overview", "skills", "decisions",
              "artifacts", "warnings", "groups", "unattended", "defaults_enabled"):
        assert k in v, k


def test_decide_guard_400_on_non_decision(sandbox):
    """066 §3 验收5：非 decision 步 POST /decide → 400 且不写文件。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-400", step="ww_write")
    port = _serve(panel)
    try:
        code, _ = _http("POST", f"http://127.0.0.1:{port}/decide",
                        {"instance": "t-400", "decision": "A"})
        assert code == 400, code
        assert not (inst / "t-400.decision").exists()
    finally:
        _stop(panel)


def test_decide_writes_key_and_c_note(sandbox):
    """066 §3 验收3/4：拍 A 落文件 "A"；C|意见 落文件匹配 ^C\\|.+。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-w", step="ww_gate_final")
    port = _serve(panel)
    try:
        code, _ = _http("POST", f"http://127.0.0.1:{port}/decide",
                        {"instance": "t-w", "decision": "A"})
        assert code == 200
        assert (inst / "t-w.decision").read_text(encoding="utf-8").strip() == "A"
        (inst / "t-w.decision").unlink()
        code, _ = _http("POST", f"http://127.0.0.1:{port}/decide",
                        {"instance": "t-w", "decision": "C|第2节数据需补时点"})
        assert code == 200
        import re
        txt = (inst / "t-w.decision").read_text(encoding="utf-8").strip()
        assert re.match(r"^C\|.+", txt), txt
    finally:
        _stop(panel)


def test_defaults_and_unattended_switches(sandbox):
    """卡补充需求①②：预设默认开关写 defaults.json，无人值守开关写 .unattended。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-sw", step="ww_gate_final")
    port = _serve(panel)
    try:
        code, _ = _http("POST", f"http://127.0.0.1:{port}/defaults",
                        {"instance": "t-sw", "enabled": True})
        assert code == 200
        dj = json.loads((inst / "t-sw.defaults.json").read_text(encoding="utf-8"))
        assert dj.get("ww_gate_final") == "A", dj
        code, _ = _http("POST", f"http://127.0.0.1:{port}/defaults",
                        {"instance": "t-sw", "enabled": False})
        assert code == 200 and not (inst / "t-sw.defaults.json").exists()

        code, _ = _http("POST", f"http://127.0.0.1:{port}/unattended",
                        {"instance": "t-sw", "enabled": True})
        assert code == 200 and (inst / "t-sw.unattended").exists()
        code, _ = _http("POST", f"http://127.0.0.1:{port}/unattended",
                        {"instance": "t-sw", "enabled": False})
        assert code == 200 and not (inst / "t-sw.unattended").exists()
    finally:
        _stop(panel)


def test_no_generated_files_in_engine_dir(sandbox):
    """铁律：面板运行只写 instances/ 三件 + exchange/reports 告警件，engine/ 零新增。"""
    panel, inst, exch, _w = sandbox
    before = {p.name for p in ENGINE_DIR.iterdir()}
    _write_state(inst, "t-clean", step="ww_factcheck",
                 history=[{"step": "ww_factcheck", "action": "wait_escalate", "log": "卡死"}])
    (inst / "t-clean.unattended").write_text("on\n", encoding="utf-8")
    _view(panel, "t-clean")
    after = {p.name for p in ENGINE_DIR.iterdir()}
    assert before == after, after - before
    assert (exch / "reports" / "unattended-alerts-t-clean.md").exists()


# ---------------------------------------------------------------- 自研 YAML 解析器正确性

def test_custom_yaml_parser_matches_pyyaml(sandbox):
    """ww_panel 为满足「纯标准库」自研了 pipeline.yaml 子集解析器——
    必须与 PyYAML 结果在面板消费的字段上逐字一致，否则所有仪表都会读到坏数据。"""
    import yaml
    panel, _inst, _e, _w = sandbox
    a = panel.load_pipeline()
    b = yaml.safe_load((ENGINE_DIR / "config" / "templates" / "hot-topic-commentary.yaml").read_text(encoding="utf-8"))
    assert [s["id"] for s in a["steps"]] == [s["id"] for s in b["steps"]]
    assert [s.get("type") for s in a["steps"]] == [s.get("type") for s in b["steps"]]
    assert [s.get("assignee") for s in a["steps"]] == [s.get("assignee") for s in b["steps"]]
    assert a.get("decision_defaults") == b.get("decision_defaults")
    # skills_map 全等（Δ6 v2 条目 {skill, owner}）
    assert a.get("skills_map") == b.get("skills_map"), (a.get("skills_map"), b.get("skills_map"))
    # 门选项 key/label 全等（§3 反查 label 依赖）
    for sa, sb in zip(a["steps"], b["steps"]):
        if sa.get("type") == "decision":
            assert [(o.get("key"), o.get("label")) for o in sa.get("options", [])] == \
                   [(o.get("key"), o.get("label")) for o in sb.get("options", [])], sa["id"]
            assert sa.get("question") == sb.get("question")
    # 组员与产物 glob 全等（§4/§6 依赖）
    for sa, sb in zip(a["steps"], b["steps"]):
        assert sa.get("artifact") == sb.get("artifact")
        assert sa.get("artifacts") == sb.get("artifacts")
        assert [(m.get("id"), m.get("assignee")) for m in sa.get("members", [])] == \
               [(m.get("id"), m.get("assignee")) for m in sb.get("members", [])], sa["id"]


# ---------------------------------------------------------------- §7 引擎侧第②顺位

def test_engine_defaults_json_second_priority(tmp_path, monkeypatch):
    """卡需求②/066 §7：engine._do_decision 消费顺位
    ①.decision → ②<instance>.defaults.json → ③pipeline → ④等待（逐级实测）。"""
    from engine import WeWriteEngine
    import engine as engine_mod

    monkeypatch.setattr(engine_mod, "INSTANCES_DIR", tmp_path)
    gate = {"id": "g1", "type": "decision", "question": "q?",
            "options": [{"key": "A", "label": "通过", "next": "s_next"},
                        {"key": "B", "label": "驳回", "next": "s_back"}],
            "on_reject": "s_back", "next": "s_next"}

    def mk(tpl=None):
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = "t-def"
        eng.mode = "test"
        eng.template = tpl or {"steps": [dict(gate)]}
        return eng, {"instance": "t-def", "step": "g1", "iteration": 0,
                     "history": [], "status": "running"}

    # ② defaults.json 命中 → 自动落拍 A
    (tmp_path / "t-def.defaults.json").write_text('{"g1": "A"}', encoding="utf-8")
    eng, st = mk()
    res = eng._do_decision(dict(gate), st)
    assert res["next"] == "s_next"
    assert st["history"][-1]["source"] == "auto_default"
    assert st["history"][-1]["decision"] == "A"

    # ① decision 文件优先于 ②（写 B → 走驳回，且不留 auto_default 痕）
    (tmp_path / "t-def.decision").write_text("B", encoding="utf-8")
    eng, st = mk()
    res = eng._do_decision(dict(gate), st)
    assert res["next"] == "s_back"
    assert st["history"][-1].get("source") is None
    assert not (tmp_path / "t-def.decision").exists()   # 消费即删（引擎原语义）

    # ③ defaults.json 缺失 → 回落 pipeline decision_defaults
    # （20260921：③ 需 opt-in——勾「无人值守」写 .unattended 模拟授权）
    (tmp_path / "t-def.defaults.json").unlink()
    (tmp_path / "t-def.unattended").write_text("", encoding="utf-8")
    eng, st = mk({"steps": [dict(gate)], "decision_defaults": {"g1": "B"}})
    res = eng._do_decision(dict(gate), st)
    assert res["next"] == "s_back"
    assert st["history"][-1]["source"] == "auto_default"

    # ④ 三级全无（且撤回 opt-in 授权）→ 等待拍板（不越权自动落拍）
    (tmp_path / "t-def.unattended").unlink()
    eng, st = mk()
    res = eng._do_decision(dict(gate), st)
    assert res["next"] is None
    assert "等待拍板" in res["log"]
    assert st["history"] == []

    # 坏 defaults.json（非法 JSON）→ 降级不崩，回落 ④
    (tmp_path / "t-def.defaults.json").write_text("{不是 json", encoding="utf-8")
    eng, st = mk()
    res = eng._do_decision(dict(gate), st)
    assert res["next"] is None and "等待拍板" in res["log"]


# ---------------------------------------------------------------- §5 wait_stall 抑制（卡 t_9261a467，2026-09-18）

def _patch_cards(panel, monkeypatch, cards: dict):
    """mock _query_card：cards = {step_id: status or None}；不碰真实 kanban 库。"""
    monkeypatch.setattr(
        panel, "_query_card",
        lambda inst, sid: ({"id": f"card-{sid}", "status": cards.get(sid)}
                           if sid in cards else None))


def test_waitstall_suppressed_when_card_blocked(sandbox, monkeypatch):
    """单测 A：dispatch_wait ×N + 被等卡 status=blocked → 不产 wait_stall，
    card_blocked 仍产且附注 wait_stall_note（双告警降为单告警）。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_layout", "action": "dispatch_wait",
             "log": "卡 card-1 在途（幂等跳过 status=blocked）"}] * 6
    _write_state(inst, "t-sup", step="ww_layout", history=hist)
    _patch_cards(panel, monkeypatch, {"ww_layout": "blocked"})
    v, _ = _view(panel, "t-sup")
    acts = [w["action"] for w in v["warnings"]]
    assert "wait_stall" not in acts, acts
    blocked = [w for w in v["warnings"] if w["action"] == "card_blocked"]
    assert len(blocked) == 1 and blocked[0]["wait_stall_note"] == "连续 dispatch_wait 6 次"


def test_waitstall_kept_when_card_running(sandbox, monkeypatch):
    """单测 B：同形态 history + 卡 status=running → 仍产 wait_stall（防误伤真 stall）。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_layout", "action": "dispatch_wait", "log": "在途"}] * 5
    _write_state(inst, "t-run", step="ww_layout", history=hist)
    _patch_cards(panel, monkeypatch, {"ww_layout": "running"})
    v, _ = _view(panel, "t-run")
    assert any(w["action"] == "wait_stall" for w in v["warnings"])


def test_waitstall_kept_when_card_query_degrades(sandbox, monkeypatch):
    """查询降级 None（卡库缺席）→ 照常报 wait_stall（宁报勿漏）。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_layout", "action": "dispatch_wait", "log": "在途"}] * 5
    _write_state(inst, "t-deg", step="ww_layout", history=hist)
    _patch_cards(panel, monkeypatch, {})
    v, _ = _view(panel, "t-deg")
    assert any(w["action"] == "wait_stall" for w in v["warnings"])


def test_waitstall_fallback_when_no_card_blocked_anchor(sandbox, monkeypatch):
    """单测 D（卡 t_968935f1，2026-09-18）：state["step"] 为非 dispatch 型 + history 尾部
    dispatch_wait≥5 + 被等卡全 blocked → 抑制分支找不到 card_blocked 落点，
    必须回落产 wait_stall（宁报勿漏），不得静默 warnings=[]。"""
    panel, inst, _e, _w = sandbox
    hist = [{"step": "ww_layout", "action": "dispatch_wait", "log": "在途"}] * 5
    # 当前步 = ww_check_render（check 型，判定2 不产 card_blocked）
    _write_state(inst, "t-fb", step="ww_check_render", history=hist)
    _patch_cards(panel, monkeypatch, {"ww_layout": "blocked"})
    v, _ = _view(panel, "t-fb")
    acts = [w["action"] for w in v["warnings"]]
    assert acts, "反例场景不得静默"
    assert any(w["action"] == "wait_stall" for w in v["warnings"]), acts


# ---------------------------------------------------------------- 016 #10 重派

def test_reassign_blocked_card_runs_unblock_and_dispatch(sandbox, monkeypatch):
    """016 批次三 #10：blocked 卡 /reassign → unblock + dispatch 各一次（CLI mock）。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-ra", step="ww_layout")
    calls = []

    def fake_cli(args, timeout=120):
        calls.append(list(args))
        return 0, "ok"

    monkeypatch.setattr(panel, "_kanban_cli", fake_cli)
    monkeypatch.setattr(
        panel, "_query_card",
        lambda i, s: {"id": "t_abc123", "status": "blocked"} if s == "ww_layout" else None)
    port = _serve(panel)
    try:
        code, body = _http("POST", f"http://127.0.0.1:{port}/reassign",
                           {"instance": "t-ra", "step": "ww_layout"})
        assert code == 200, (code, body)
        assert calls == [["unblock", "t_abc123", "--reason", "user 手动重派"],
                         ["dispatch"]], calls
        assert json.loads(body)["ok"] is True
    finally:
        _stop(panel)


def test_reassign_guards_reject_non_blocked_or_missing(sandbox, monkeypatch):
    """门禁：非 blocked 卡 409 / 查无卡 404 / 实例不存在 400——且零 CLI 调用。"""
    panel, inst, _e, _w = sandbox
    _write_state(inst, "t-rg", step="ww_layout")
    calls = []
    monkeypatch.setattr(panel, "_kanban_cli",
                        lambda args, timeout=120: calls.append(list(args)) or (0, "ok"))
    monkeypatch.setattr(
        panel, "_query_card",
        lambda i, s: ({"id": "t_xyz", "status": "ready"}
                      if (i, s) == ("t-rg", "ww_layout") else None))
    port = _serve(panel)
    try:
        # ① 卡在但 status=ready → 409
        code, _ = _http("POST", f"http://127.0.0.1:{port}/reassign",
                        {"instance": "t-rg", "step": "ww_layout"})
        assert code == 409, code
        # ② 键空间查无卡（他实例/他步）→ 404
        code, _ = _http("POST", f"http://127.0.0.1:{port}/reassign",
                        {"instance": "t-rg", "step": "ww_write"})
        assert code == 404, code
        # ③ 实例不存在 → 400
        code, _ = _http("POST", f"http://127.0.0.1:{port}/reassign",
                        {"instance": "t-ghost", "step": "ww_layout"})
        assert code == 400, code
        assert calls == [], calls  # 门禁全拒 → 零 CLI 调用（安全边界）
    finally:
        _stop(panel)
