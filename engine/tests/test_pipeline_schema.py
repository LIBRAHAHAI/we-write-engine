"""T1 pipeline.yaml schema 校验：steps 连通性 + models/skills_map 键覆盖 + Δ13 门序。

2026-09-11 D3 收敛：本文件为唯一 schema 校验入口（原仓库根 tests/test_pipeline_schema.py
的独有断言已并入，见文件末「并自用例」段）。
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
PIPELINE = Path(__file__).parent.parent / "config" / "templates" / "hot-topic-commentary.yaml"


def _load():
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))


def test_pipeline_loads():
    tpl = _load()
    assert tpl["name"] == "we-write"
    assert isinstance(tpl["steps"], list) and len(tpl["steps"]) == 21


def test_step_ids_unique():
    tpl = _load()
    ids = [s["id"] for s in tpl["steps"]]
    assert len(ids) == len(set(ids)), "step id 重复"


def test_steps_connectivity_no_dangling():
    """next/on_reject/成员选项 next 不得指向不存在的步骤。"""
    tpl = _load()
    ids = {s["id"] for s in tpl["steps"]}
    for s in tpl["steps"]:
        for ref in [s.get("next"), s.get("on_reject"), s.get("on_fail"), s.get("on_timeout")]:
            if ref:
                assert ref in ids, f"{s['id']} 悬空引用: {ref}"
        for opt in s.get("options") or []:
            if opt.get("next"):
                assert opt["next"] in ids, f"{s['id']} 选项 {opt['key']} 悬空: {opt['next']}"
        for m in s.get("members") or []:
            assert m.get("id"), f"{s['id']} member 缺 id"


def test_has_done_step_and_first_reachable():
    tpl = _load()
    steps = {s["id"]: s for s in tpl["steps"]}
    done_ids = [i for i, s in steps.items() if s["type"] == "done"]
    assert done_ids, "缺 done 终态步骤"
    first = tpl["steps"][0]["id"]
    # BFS 从首步沿 next 可达 done
    seen, queue = set(), [first]
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        seen.add(cur)
        s = steps.get(cur)
        if not s:
            continue
        if s["type"] == "done":
            return
        nxts = [s.get("next")]
        nxts += [o.get("next") for o in s.get("options") or []]
        if s.get("on_reject"):
            nxts.append(s["on_reject"])
        for n in nxts:
            if n:
                queue.append(n)
    raise AssertionError(f"从首步 {first} 不可达 done")


def test_assignees_valid():
    tpl = _load()
    valid = {"default", "athena", "xuanwu", "jingwei", "duanmu", None}
    for s in tpl["steps"]:
        assert s.get("assignee") in valid, f"{s['id']} 非法 assignee: {s.get('assignee')}"
        for m in s.get("members") or []:
            assert m.get("assignee") in valid, f"{s['id']}/{m['id']} 非法 assignee"


def test_models_and_skills_keys_valid():
    """models/skills_map 的键必须是存在的 step id 或 dispatch_group 成员 id（防漂移）。

    成员卡（如 ww_review_audit/ww_review_value）由 executor 以 member id 建卡，
    卡 body 的 skills 注入同样按 member id 查键——故 member id 属合法键。
    （2026-09-10 实测发现：只认顶层 step id 会把合法的成员绑定误判为漂移。）
    """
    tpl = _load()
    ids = {s["id"] for s in tpl["steps"]}
    for s in tpl["steps"]:
        for m in s.get("members") or []:
            if isinstance(m, dict) and m.get("id"):
                ids.add(m["id"])
    for k in (tpl.get("models") or {}):
        assert k in ids, f"models 段未知步骤: {k}"
    for k in (tpl.get("skills_map") or {}):
        assert k in ids, f"skills_map 段未知步骤: {k}"


def test_three_gates_present():
    """user 拍板门齐全（2026-09-10 修订：charter 文本"3门"计数与 user 既有拍板偏好冲突）。

    user 既有偏好：选题≥5候选拍板 + 大纲定稿拍板 + 标题5候选拍板 → 实际需要
    4 个拍板点：选题/大纲/终稿(正文+标题)/收官。charter"12步3门"文本中的"3"
    为早期计数，以 user 拍板偏好为准（4 门），偏差已在里程碑简报中报告 user。
    """
    tpl = _load()
    decisions = [s["id"] for s in tpl["steps"] if s["type"] == "decision"]
    expected = {"ww_gate_topic", "ww_gate_outline", "ww_gate_final", "ww_closing_gate"}
    assert set(decisions) == expected, f"拍板门 {decisions} ≠ 期望 {sorted(expected)}"


# ===================== Δ13 门序（017 verdict D1，P0）=====================
# charter §一：…标题 → **双审** → **门3（终稿正文+标题）** → 配图排版…
# 旧序 ww_headline → ww_gate_final → ww_dual_review 让 user 先拍板终稿、机器再审，
# 执行的正是 charter 的反面。以下 4 例把新序钉死，**对旧序必须变红**。
DELTA13_CHAIN = [
    ("ww_headline", "ww_dual_review"),
    ("ww_dual_review", "ww_review_barrier"),
    ("ww_review_barrier", "ww_logic_review"),   # 逻辑审插双审后（问题清单 #4）
    ("ww_logic_review", "ww_gate_final"),
    ("ww_gate_final", "ww_layout"),
]


def test_gate_order_gate_final_next_is_layout():
    """门3 放行直落配图排版（旧序 next=ww_dual_review → 红）。"""
    tpl = _load()
    by_id = {s["id"]: s for s in tpl["steps"]}
    gate = by_id["ww_gate_final"]
    assert gate["next"] == "ww_layout", \
        f"门3 next 应为 ww_layout（双审已前移），实际 {gate['next']}"
    for opt in gate.get("options") or []:
        assert opt.get("next") != "ww_dual_review", \
            f"门3 选项 {opt.get('key')} 仍指向双审（旧序残留）"
    assert gate.get("on_reject") == "ww_write", \
        f"门3 驳回应退回写文，实际 {gate.get('on_reject')}"


def test_gate_order_dual_review_before_gate_final():
    """steps 列表序号断言：双审及其屏障必须排在门3 之前（旧序 idx 反置 → 红）。"""
    tpl = _load()
    ids = [s["id"] for s in tpl["steps"]]
    i_dual, i_barrier, i_gate = (ids.index("ww_dual_review"),
                                 ids.index("ww_review_barrier"),
                                 ids.index("ww_gate_final"))
    assert i_dual < i_gate, \
        f"ww_dual_review(idx={i_dual}) 应在 ww_gate_final(idx={i_gate}) 之前"
    assert i_dual < i_barrier < i_gate, \
        f"双审屏障须夹在双审与门3 之间，实际 idx dual={i_dual} barrier={i_barrier} gate={i_gate}"


def test_gate_order_delta13_chain_exact():
    """Δ13 全链逐边钉死：标题→双审→屏障→门3→配图排版。"""
    tpl = _load()
    by_id = {s["id"]: s for s in tpl["steps"]}
    for src, dst in DELTA13_CHAIN:
        assert by_id[src].get("next") == dst, \
            f"{src}.next 应为 {dst}，实际 {by_id[src].get('next')}"


def test_gate_final_context_includes_dual_review_verdicts():
    """门3 拍板必须能看到双审 verdict——前移的意义就是 user 审「机器已查完」的稿。"""
    tpl = _load()
    by_id = {s["id"]: s for s in tpl["steps"]}
    ctx = by_id["ww_gate_final"].get("context_files") or []
    joined = " ".join(ctx)
    assert "ww-audit-verdict" in joined, f"门3 context_files 缺玄武审 verdict: {ctx}"
    assert "ww-value-verdict" in joined, f"门3 context_files 缺端木价值审 verdict: {ctx}"
    for a in by_id["ww_review_barrier"]["artifacts"]:
        assert a in ctx, f"屏障产物 {a} 未进入门3 context_files"


# ============ 并自用例（原仓库根 tests/test_pipeline_schema.py，D3 收敛）============
# 与上方既有断言重复的 4 例（id 唯一 / 悬空引用 / hermes 保留名 / models+skills 键）
# 未重复搬运——上方版本覆盖范围更宽（另查 on_fail/on_timeout，assignee 用白名单）。

def test_step_type_in_vocabulary():
    tpl = _load()
    vocab = {"dispatch", "dispatch_group", "decision", "wait", "done", "check"}
    for s in tpl["steps"]:
        assert s.get("type") in vocab, f"{s['id']} 类型非法: {s.get('type')}"


def test_exactly_one_done_step():
    """done 终态步骤恰 1 个（上方 test_has_done_step_and_first_reachable 只查存在性）。"""
    tpl = _load()
    done_ids = [s["id"] for s in tpl["steps"] if s["type"] == "done"]
    assert len(done_ids) == 1, f"done 步骤应恰 1 个，实际 {done_ids}"


def test_decision_gates_have_question():
    tpl = _load()
    for s in tpl["steps"]:
        if s["type"] == "decision":
            assert s.get("question"), f"门 {s['id']} 缺 question"


def test_group_barrier_artifacts_present():
    """每个 dispatch_group 的 next 必须是带 artifacts 的 wait 屏障，且成员数=产物数。"""
    tpl = _load()
    by_id = {s["id"]: s for s in tpl["steps"]}
    for s in tpl["steps"]:
        if s["type"] == "dispatch_group":
            nxt = by_id.get(s.get("next"))
            assert nxt and nxt["type"] == "wait", f"{s['id']} 后未接 wait 屏障"
            assert nxt.get("artifacts"), f"屏障 {nxt['id']} 缺 artifacts 列表"
            members = [m["id"] for m in s["members"]]
            assert len(members) == len(nxt["artifacts"]), \
                f"{s['id']} 成员数 {len(members)} 与屏障产物数 {len(nxt['artifacts'])} 不匹配"


def test_node_count_exact_21_and_distribution():
    """口径单源钉死——21 节点 + 分布 11/2/4/2/1/1。

    M2：17→18（Δ3 factcheck）；M3：18→19（Δ9 ww_check_render）；
    M4 后：19→20（ww_logic_review 逻辑审，user 问题清单拍板）；
    E1（20260920）：20→21（ww_cover_ai 封面 AI 出图，插在排版后、渲染校验前）。
    分布 = 13 工作步 + 4 门 + 2 屏障 + 1 收官 + 1 机器校验（C4）。
    """
    tpl = _load()
    steps = tpl["steps"]
    assert len(steps) == 21, f"节点数 {len(steps)} != 21（口径漂移）"
    dist = {}
    for s in steps:
        dist[s["type"]] = dist.get(s["type"], 0) + 1
    assert dist.get("dispatch") == 11, f"dispatch {dist.get('dispatch')} != 11"
    assert dist.get("dispatch_group") == 2, f"dispatch_group {dist.get('dispatch_group')} != 2"
    assert dist.get("decision") == 4, f"decision {dist.get('decision')} != 4"
    assert dist.get("wait") == 2, f"wait {dist.get('wait')} != 2"
    assert dist.get("done") == 1, f"done {dist.get('done')} != 1"
    assert dist.get("check") == 1, f"check {dist.get('check')} != 1"


# ===================== M2 新增 schema 校验（013 §3.3 规则 8-12）=====================

def test_m2_factcheck_step_position_and_contract():
    """Δ3：factcheck 位于 polish 之后 headline 之前，verdict_consume 打回 ww_write。"""
    tpl = _load()
    by_id = {s["id"]: s for s in tpl["steps"]}
    ids = [s["id"] for s in tpl["steps"]]
    assert ids.index("ww_polish") < ids.index("ww_factcheck") < ids.index("ww_headline")
    fc = by_id["ww_factcheck"]
    assert fc["assignee"] == "xuanwu"
    assert fc["load_class"] == "audit"
    vc = fc["verdict_consume"]
    assert vc["field"] == "factcheck_result"
    assert vc["pass_value"] == "approved"
    assert vc["on_reject"] == "ww_write"


def test_m2_verdict_gate_contract():
    """Δ5：双审组屏障带 verdict_gate，打回 ww_polish。"""
    tpl = _load()
    by_id = {s["id"]: s for s in tpl["steps"]}
    vg = by_id["ww_review_barrier"]["verdict_gate"]
    assert vg["field"] == "audit_result"
    assert vg["pass_value"] == "approved"
    assert vg["on_reject"] == "ww_polish"


def test_m2_audit_load_class_deny_minimax():
    """Δ7：audit 类步骤存在且 load_classes.audit.deny_providers 含 minimax。"""
    tpl = _load()
    audit_steps = [s["id"] for s in tpl["steps"] if s.get("load_class") == "audit"]
    audit_steps += [m["id"] for s in tpl["steps"] for m in s.get("members") or []
                    if isinstance(m, dict) and m.get("load_class") == "audit"]
    assert "ww_factcheck" in audit_steps and "ww_review_audit" in audit_steps
    assert "minimax" in tpl["load_classes"]["audit"]["deny_providers"]


def test_m2_skills_map_owner_structure():
    """Δ6：skills_map 条目必须 {skill, owner} 结构，owner 非空。

    2026-09-27 吸引力批次放宽：引擎源条目 {skill, source: engine} 与 owner 互斥
    二选一也合法（validate 规则9 走 engine/config/skills/ 实存校验新分支）。
    """
    tpl = _load()
    for k, entries in (tpl.get("skills_map") or {}).items():
        assert entries, f"skills_map {k} 空绑定"
        for e in entries:
            assert isinstance(e, dict) and e.get("skill"), \
                f"skills_map {k} 条目缺 skill: {e}"
            has_owner = e.get("owner")
            has_source = e.get("source") == "engine"
            assert has_owner or has_source, \
                f"skills_map {k} 条目缺 owner（或 source: engine）: {e}"
            if has_owner and has_source:
                raise AssertionError(f"skills_map {k} 条目 owner 与 source 同给: {e}")
