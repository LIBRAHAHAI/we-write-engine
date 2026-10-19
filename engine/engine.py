"""engine.py — we-write 写作流水线引擎（M1/T2，2026-09-10）。

与 flow-engine 平级的独立可执行程序：一篇文章 = 一个实例 JSON。
单步推进语义（同 flow-engine run()）：每次 run() 执行当前 step 一个动作 →
落盘 next。心跳重启后从实例 JSON 的 step 续跑（断点恢复，charter S4）。

B2 修复（2026-09-11）：dispatch / dispatch_group 建卡后**留本步**，等卡 status=done
才推进 next。故 state.step 恒指向"未完成的合法断点"，进程中断重启不会空跑下游卡。

用法：
  python engine.py --instance 20260910-xxx --status        # 查看实例状态
  python engine.py --instance 20260910-xxx                 # 推进一个动作
  python engine.py --instance 20260910-xxx --step ww_write # 从指定步骤续跑
  python engine.py --list                                  # 列出全部实例

模板体系（005 需求1，2026-09-15）：全局 pipeline.yaml 已退役，改「模板目录 +
默认模板」——engine/config/templates/<id>.yaml，默认 hot-topic-commentary。
实例创建时绑定模板（state.template 首跑落盘定终身，语义对齐 mode Z5）；
005 之前落盘的旧实例无 template 字段 → 自动落默认模板，行为不变。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

import archive  # 066 §8：ww_done 成果归档动作（成果区两层目录标配）

import kanban_client

import model_router

from config import slug_registry
from config import template_loader

import cron_coupling  # 生命周期耦合（2026-10-10）：建档/终态收口 → 心跳 cron 对齐

ENGINE_DIR = Path(__file__).parent
# G-2/031：INSTANCES_DIR 支持 env 覆盖（conftest 重定向到 temp，测试残留不进真实 instances/）
INSTANCES_DIR = Path(os.environ.get(
    "WW_INSTANCES_DIR", str(ENGINE_DIR / "instances")))
# G-1/031：EXCHANGE_DIR 支持 env 覆盖（测试/快照副本可重定向落盘，不写真实交付树）
EXCHANGE_DIR = Path(os.environ.get(
    "WW_EXCHANGE_DIR", "./exchange"))
# ⚠️ 撞键教训（2026-09-10 冒烟实证）：flow-engine 立项链卡幂等键形如
# engine:we-write-engine:<step>，本引擎若用 engine:we-write-engine:<inst>:<step>
# 前缀，archive_flow_cards 前缀扫描会误归档 flow-engine 活卡（t_b94a9132 实证）。
# 本引擎键前缀改为 wweng:，与 flow-engine 的 engine: 键空间完全隔离。
ENGINE_KEY_PREFIX = "wweng:"

# 步骤类型（对齐 flow-engine 语义子集）
T_DISPATCH = "dispatch"
T_DISPATCH_GROUP = "dispatch_group"
T_DECISION = "decision"
T_WAIT = "wait"
T_DONE = "done"
T_CHECK = "check"  # M3/C4：机器校验步（013 Δ9）

ACCEPT_WORDS = ("a", "同意", "批准", "accept", "ok", "1", "是", "通过", "accepted",
                "拍板", "确认", "可以", "好", "完成", "接受")

# ---- A1①（20260920）：上传/封面权威标题机械化 ----
# 受益步：建卡 body 携带「权威标题」字段（E2：ww_cover_ai 与 ww_upload 同口径）。
AUTH_TITLE_STEPS = frozenset({"ww_upload", "ww_cover_ai"})
# E1（20260920）：test 模式强制 mock 短路集合（P3 防呆：测试实例不进真草稿箱、
# 不真调 bl 出图）——仿 ww_upload 原短路写法扩为集合判断。
# 2026-09-26 修规则不修事件：静态步号集合只覆盖 ww_* 默认模板，6 个新模板的
# fa_upload/hn_upload/bm_upload 在 test 模式会真触微信 API（实测缺陷）。改为
# 后缀规则函数：任何模板的 *_upload / *_cover_ai 步一律 test 短路，未来模板
# 自动继承，无需再登记步号。
MOCK_SHORT_CIRCUIT_SUFFIXES = ("_upload", "_cover_ai")


def _is_mock_short_circuit(sid: str) -> bool:
    return sid.endswith(MOCK_SHORT_CIRCUIT_SUFFIXES)


# ---- t_2c740cd9（20260926）test 模式实弹建卡 fail-closed 门 ----
# 实证：2026-09-26 22:01:40，pytest 用例 test_skeleton_refuses_run_but_status_ok
# 以 WeWriteEngine("t-skel", template="novel-short")（mode 缺省 test、state 落
# 一次性临时目录）实跑；当时 working tree 里 novel-short 已去 skeleton，run()
# 未被拒 → _create_card 真发 `hermes kanban create` 命中活跃板，留下孤儿幻影卡
# t_773acac9（实例文件不在 engine/instances/，永远无人推进）。
# 根因：「测试/冒烟零活跃板接触」铁律（AGENTS/conftest/铁律 5）只靠**调用方自觉
# mock**，写侧 _create_card 无任何门 → 任何未 mock 的实跑都会污染活跃板。
# 处置（fail-closed，默认拒绝）：mode != live 时拒绝向活跃板建卡，须显式 opt-in
# 才放行。两道口子（任一即可，均留痕可审计）：
#   ① 实例 state 置 "allow_test_dispatch": true —— 真实实例目录内、确需走全链
#      的演练实例（如 engine/instances/novel-pilot.json 的 template 试跑）；
#   ② 环境变量 WW_ENGINE_TEST_DISPATCH=1 —— 临时手工演练。
# 夹具/测试实例（无 opt-in）→ raise，不再静默产生幻影卡。
TEST_DISPATCH_OPTIN_ENV = "WW_ENGINE_TEST_DISPATCH"
# 拍板 note 中的候选指定（如「标题选候选2」「候选3 更好」→ 取 ww-title 呈报第 N 条）
_AUTH_CAND_NOTE_RE = re.compile(r"候选\s*(\d+)")
# 拍板 note 直接给标题文本（保守白名单句式：标题选X / 标题：X / 标题定为X）
_AUTH_TITLE_PICK_RE = re.compile(r"标题选\s*(\S[^\n；;。]{5,})")
_AUTH_TITLE_EXPLICIT_RE = re.compile(r"标题(?:定为|选定)?[：:]\s*(\S[^\n；;。]{5,})")

# ---- t_a6da5fa1（20260921）产物「正文非空」硬门：空壳产物视同未交付 ----
# 实证：ww_selection 本轮 087（正文仅 exwrite 骨架占位注释 + status=done）通过
# R5.2 双验证落盘，下游按 glob 取 mtime 最新产物即取到空文件；083 同源（先建
# 占位、25 分钟后才补正文，窗口期内 done 但无正文）。
# 根因：既有校验只验 frontmatter（yaml.safe_load + ^status 正则），不验正文。
# 处置（消费侧硬门，不改写侧工作流）：引擎解析产物时判定正文有效性，空壳
# 视同「未交付」→ 既有 fail-closed 语义生效（屏障继续等待 / verdict 缺失按
# changes_requested 打回），不静默放行。
# ⚠️ 阈值由引擎侧单点定义。2026-09-26 hermes 核查（t_a6da5fa1）：写侧
#    multi-agents/scripts/exwrite.py **没有**正文非空门、也没有 --verify 入口——
#    旧注释引用的 exwrite.py MIN_BODY_CHARS 并不存在（087 空壳能带着 status=done
#    落盘，正是写侧缺口）。本门 = 消费侧兜底；写侧根治见 016 批次一 #1（生成侧
#    去脚手架：exwrite 骨架不再产出占位注释与骨架 H1）。
# 016 批次一 #2（2026-09-29）：阈值 20 → 200，判据追加剥 markdown 标题行。
#    依据（实测分布）：正常交付件最小有效字符 7134；骨架件（146/151 形态）剥标题
#    后 0；136 形态剥标题 6799。200 无边界争议——只挡「长标题 + 空正文」骨架。
MIN_BODY_CHARS = 200
_FM_BLOCK_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MD_TITLE_RE = re.compile(r"^#+\s.*$", re.M)


def artifact_body_ok(path: Path) -> tuple[bool, str]:
    """正文非空判定：返回 (ok, reason)。

    判据：剥 frontmatter + HTML 注释 + markdown 标题行 + 全部空白后，
    剩余有效字符数 ≥ MIN_BODY_CHARS。
    - 空壳（只有骨架 H1 / 占位注释 / frontmatter）→ ok=False，reason 附实测字符数
    - 读盘失败 → ok=True（不把 IO 故障伪装成产物缺陷；读失败由调用方既有路径暴露）
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # 不可判定，交给调用方既有语义
        return True, f"读取失败不判定空壳: {e}"
    m = _FM_BLOCK_RE.match(text)
    body = text[m.end():] if m else text
    body = _HTML_COMMENT_RE.sub("", body)
    body = _MD_TITLE_RE.sub("", body)  # 016 #2：标题行不算正文（长标题空壳形态）
    effective = re.sub(r"\s+", "", body)
    if len(effective) < MIN_BODY_CHARS:
        return False, (f"空壳产物（正文字符 {len(effective)} < {MIN_BODY_CHARS}）: {path.name}")
    return True, ""


_FM_STATUS_RE = re.compile(r"^status:\s*(\S+)", re.M)
# t_d1627b5a：实体门 status 词表与 AGENTS 铁律7 对齐——工作产物 done / 验收产物 accepted/rejected
VERDICT_STATUS_VOCAB = frozenset({"done", "accepted", "rejected"})


def verdict_deliverable_ok(path: Path | None) -> tuple[bool, str]:
    """verdict 消费实体门（016 批次一 #3；t_d1627b5a 词表对齐）：frontmatter
    契约字段之外，正文须过 artifact_body_ok 且 status 在词表内才可放行。

    词表（AGENTS 铁律7）：工作产物 done / 验收产物 accepted/rejected。
    verdict 属验收产物 → status ∈ {done, accepted, rejected} 均放行实体门。
    实证：article-006 ww_logic_review 复消费时 verdict status=approved 按
    铁律7 写 accepted，被旧门 status=done 硬判打回 ww_polish（假打回）。

    本门把「字段齐」与「件在手」绑定：空壳/未定稿（status 不在词表）→
    视同 changes_requested 走既有打回语义。
    读盘失败 → ok=True（与 artifact_body_ok 同容错，IO 故障不伪装成产物缺陷）。
    """
    if path is None:
        return False, "产物缺失"
    ok, reason = artifact_body_ok(path)
    if not ok:
        return False, reason
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return True, f"读取失败不判定: {e}"
    m = _FM_STATUS_RE.search(text)
    st = m.group(1) if m else None
    if st not in VERDICT_STATUS_VOCAB:
        return False, f"status={st or '缺失'} ∉ 词表{sorted(VERDICT_STATUS_VOCAB)}"
    return True, ""


def _shell_artifacts(artifact: str) -> list[Path]:
    """列出匹配该路径/glob 的空壳候选文件（屏障日志显式报出用，不静默）。"""
    ap = Path(artifact)
    if ap.exists():
        return [] if artifact_body_ok(ap)[0] else [ap]
    if "*" not in artifact and "?" not in artifact:
        return []
    try:
        matches = [m for m in ap.parent.glob(ap.name) if m.is_file()]
    except OSError:
        return []
    return [m for m in matches if not artifact_body_ok(m)[0]]


def _parse_title_candidates(text: str) -> list[str]:
    """ww-title 呈报产物 → 候选标题有序列表（A1①，059 实测格式）。

    兼容两种呈现：markdown 表格行「| N | 标题 | …」与编号列表行
    「候选N：… / N. … / N、…」。仅识别小节标题含「候选」的区段——评分分项表
    等数字单元格被「非纯数字 + 最短长度」过滤挡掉。
    """
    m = re.match(r"^---\s*\n.*?\n---\s*\n?", text or "", re.DOTALL)
    body = text[m.end():] if m else (text or "")
    found: dict[int, str] = {}
    in_section = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            in_section = "候选" in line
            continue
        if not line or not in_section:
            continue
        cand = None
        m_tab = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        m_lst = None if m_tab else (
            re.match(r"^候选\s*(\d+)\s*[：:、.．]\s*(.+)$", line)
            or re.match(r"^(\d+)\s*[.、）)]\s*(.+)$", line))
        if m_tab or m_lst:
            n = int((m_tab or m_lst).group(1))
            cand = ((m_tab or m_lst).group(2).strip().strip("*").strip()
                    .strip("「」\"'“”"))
            if cand and len(cand) >= 6 and not cand.isdigit():
                found.setdefault(n, cand)
    return [found[k] for k in sorted(found)]


def _resolve_artifact(artifact: str) -> Path | None:
    """精确路径存在即返回；通配符取 mtime 最新的匹配文件（exwrite 取号不固定）。

    t_a6da5fa1 空壳硬门：候选按 mtime 降序逐个判正文有效性（artifact_body_ok），
    取第一个正文合格的；全部为空壳 → None（视同未交付，fail-closed）。
    精确路径传空壳同样返回 None。
    """
    ap = Path(artifact)
    if ap.exists():
        return ap if artifact_body_ok(ap)[0] else None
    if "*" not in artifact and "?" not in artifact:
        return None
    matches = [m for m in ap.parent.glob(ap.name) if m.is_file()]
    if not matches:
        return None
    for m in sorted(matches, key=lambda x: x.stat().st_mtime, reverse=True):
        if artifact_body_ok(m)[0]:
            return m
    return None


# verdict frontmatter 契约字段白名单（013 §3.3 规则 12：机器消费字段文档化）
VERDICT_FIELD_WHITELIST = {"audit_result", "factcheck_result", "logic_result"}


def read_frontmatter_field(path: Path, field: str) -> str | None:
    """读产物 frontmatter 的一个字段值（013 §七 agentlib.exchange 语义）。

    简易 YAML frontmatter 解析（--- 界定块内 key: value 单行）——不引入完整
    yaml 依赖层级，字段缺失/格式异常返回 None（调用方视同 changes_requested）。
    """
    if field not in VERDICT_FIELD_WHITELIST:
        raise ValueError(f"非白名单 verdict 字段: {field}")
    try:
        head = path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return None
    m = re.match(r"^---\s*\n(.*?)\n---", head, re.DOTALL)
    if not m:
        return None
    fm = m.group(1)
    fm_m = re.search(rf"^\s*{re.escape(field)}\s*:\s*(\S[^\n]*)$", fm, re.MULTILINE)
    return fm_m.group(1).strip().strip("\"'") if fm_m else None


def build_reject_body(step_id: str, reason_items: list[str]) -> str:
    """打回注单（013 §4.4：check 拦截注单与 verdict_gate 注单共用）。"""
    lines = [f"[引擎打回单 {step_id}] 以下问题须逐条处理后重新交付："]
    lines += [f"  {i+1}. {it}" for i, it in enumerate(reason_items)]
    return "\n".join(lines)


def diagnose_slug_mismatch(expected: str, registry: str) -> str:
    """Z2b/023：屏障失配近似候选诊断（013 Δ8③/P8）。

    期望 slug 不在注册表时，用 difflib 找最接近的注册表条目 → 返回提示行；
    无近似候选（阈值 0.6 以下）返回空串。供 wait 屏障产物缺失时附诊断。
    """
    if not expected:
        return ""
    import difflib
    known = [s for s in registry.split(";") if s.strip()]
    close = difflib.get_close_matches(expected, known, n=1, cutoff=0.6)
    if not close:
        return ""
    return (f"期望 slug={expected}，发现近似候选 {close[0]}"
            f"（注册表可能是你要的产物名，请核对产物命名漂移）")


class WeWriteEngine:
    def __init__(self, instance: str, mode: str | None = "test", validate: bool = True,
                 template: str | None = None, materials: list | None = None):
        self.instance = instance
        # Z5/025：mode=None 表示「跟随实例 state」（wrapper 无参续跑场景）；
        # 显式传 test/live 时与 state 冲突 → _load_state 拒跑（fail-closed）。
        self.mode = mode
        # 005 需求2：程序化传入的写作材料（正常链路经 API 写 state.materials）。
        self.materials = [m for m in (materials or [])
                          if isinstance(m, dict) and m.get("content")]
        # 005 需求1：模板三层解析——显式指定 > 实例 state 绑定 > 默认模板。
        # resolve_template_id 内部 fail-closed：显式与 state 绑定冲突即 raise；
        # 旧实例 state 无 template 字段 → 默认 hot-topic-commentary（兼容硬要求）。
        state_tpl = None
        st_fp = INSTANCES_DIR / f"{instance}.json"
        if st_fp.exists():
            try:
                state_tpl = (json.loads(st_fp.read_text(encoding="utf-8"))
                             or {}).get("template")
            except (OSError, ValueError):
                state_tpl = None  # 损坏 state 不拦模板解析，留给 _load_state 报真错
        self.template_id = template_loader.resolve_template_id(template, state_tpl)
        self.template_path = template_loader.template_path(self.template_id)
        self.template = yaml.safe_load(self.template_path.read_text(encoding="utf-8"))
        # Z3/023：拒启门禁——构造时跑 P8 静态校验，坏配置 raise（不再带病启动）。
        # validate=False 仅供内部单测构造残缺模板（显式豁免，非引擎链路）。
        if validate:
            errs = slug_registry.validate_slug_homogeneity(self.template)
            if errs:
                raise ValueError(
                    f"模板 {self.template_id} slug 同构校验失败，拒启（013 Δ8①/Z3）：\n  "
                    + "\n  ".join(errs))
        self.instance_path = INSTANCES_DIR / f"{instance}.json"

    # ---- 状态读写 ----
    def _load_state(self) -> dict:
        # Z5 测试隔离：仅未绑定 instance_path 时解析（__new__ 构造的测试用例已自带 temp 路径）
        if not getattr(self, "instance_path", None):
            self.instance_path = INSTANCES_DIR / f"{self.instance}.json"
        if not self.instance_path.exists():
            st = {"instance": self.instance, "step": None, "iteration": 0,
                  "history": [], "status": "idle", "dispatched": {},
                  "mode": self.mode or "test",  # Z5/025：mode 首跑定终身，缺省 test 防呆
                  "template": getattr(self, "template_id",
                                      template_loader.DEFAULT_TEMPLATE_ID),
                  # 005 需求2：新建对话提交的材料（type: text|url|file|folder），
                  # 引擎首次派选题卡时以卡 comment 注入（_maybe_inject_materials）。
                  "materials": getattr(self, "materials", []),
                  # C1（20260920）：立项时间戳（面板「立项日期」数据源；
                  # 存量实例无此字段 → 后端回退 st_ctime，不回填）
                  "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            self._save_state(st)  # Z5：立即持久化（后续无参续跑可读到 state.mode）
            # 生命周期耦合（2026-10-10）：新实例建档（idle 即活动实例）→ 保证心跳在跑
            cron_coupling.reconcile_silent()
            return st
        st = json.loads(self.instance_path.read_text(encoding="utf-8"))
        st.setdefault("dispatched", {})  # 兼容 B2 修复前落盘的实例 JSON
        st.setdefault("history", [])
        st.setdefault("mode", "test")  # 兼容旧实例 JSON（P3 防呆方向：未知归 test）
        # 005 需求1：旧实例无 template 字段 → 绑定本次解析结果（默认模板），
        # 随本拍落盘定终身（与 mode 同语义；显式冲突在 __init__ 已 fail-closed）。
        # 回填一次性落盘（幂等）：--status / API 只读路径也能让绑定持久化，
        # 全部实例的 template 字段随首次读取统一收敛。
        legacy_unbound = "template" not in st
        st.setdefault("template", getattr(self, "template_id",
                                          template_loader.DEFAULT_TEMPLATE_ID))
        st.setdefault("materials", [])
        if legacy_unbound:
            self._save_state(st)
        # Z5/025 fail-closed：显式 --mode 与实例 state.mode 冲突 → 拒跑（非静默降级）；
        # mode=None（wrapper 无参续跑）跟随 state，不冲突。
        if self.mode is not None and self.mode != st["mode"]:
            raise ValueError(
                f"mode 冲突拒跑（Z5 fail-closed）：实例 state.mode={st['mode']}，"
                f"本次显式 --mode {self.mode}。请用与实例一致的模式续跑。")
        self.mode = st["mode"]  # 后续 _do_dispatch 读 self.mode（跟随 state）
        return st

    def _save_state(self, state: dict) -> None:
        INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        self.instance_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        # 生命周期耦合（2026-10-10）：终态落盘后对齐心跳开关（全引擎无活动实例
        # → 心跳自停）。只在终态触发——常规推进每拍都落盘，逐拍 reconcile 会
        # 平白多 1-2 次 hermes 子进程调用。
        if state.get("status") in cron_coupling.TERMINAL_STATUSES:
            cron_coupling.reconcile_silent()

    def _step_by_id(self, sid: str) -> dict:
        for s in self.template["steps"]:
            if s["id"] == sid:
                return s
        raise KeyError(f"步骤不存在: {sid}")

    def _step_goal(self, step: dict) -> str:
        """组装卡 body：description + skill 注入 + 交付路径 + 职责边界。"""
        goal = step.get("description") or f"{self.instance}/{step['id']}"
        skills = (self.template.get("skills_map") or {}).get(step["id"]) or []
        if skills:
            # M2/Δ6：条目 {skill, owner} → 显示名带 owner（路径注入见后段）
            # 2026-09-27 吸引力批次：引擎源条目 {skill, source: engine} 与 owner 互斥二选一；
            # 其加载指令是 read_file 读绝对路径 SKILL.md（引擎自带实体，不在任何 profile 池里，
            # skill_view 不可达）——指令按条目 source 区分，不混用。
            has_engine = any(isinstance(e, dict) and e.get("source") == "engine"
                            for e in skills)
            names = []
            for e in skills:
                if isinstance(e, dict):
                    nm = e["skill"]
                    if e.get("source") == "engine":
                        nm += "（引擎源）"
                    elif e.get("owner"):
                        nm += f"（{e['owner']}）"
                else:
                    nm = str(e)
                names.append(nm)
            how = ("引擎源条目 read_file 读下方注入的绝对路径 SKILL.md，owner 条目先 skill_view"
                   if has_engine else "先 skill_view 后执行")
            goal = f"[加载 skill: {', '.join(names)}（{how}）] " + goal
        hint = ("[执行前第一步：读项目 AGENTS.md 的「本项目 Skill 池」，点名列出将加载的 "
                "skill + 一句话理由，skill_view 后再动手；池中无适用则明说，禁止硬凑] "
                "[完成前必须验证：实际运行测试/构建并贴真实输出] ")
        goal = hint + goal
        # 职责边界（charter 约束：验收产物须验收人本人落盘）
        if "禁止代写验收产物" not in goal:
            goal += ("（职责边界：禁止代写验收产物——验收产物必须由验收人经 exwrite 落盘，"
                     "author 字段须为验收人本人。交付通道铁律（016 批次一 #4）：产物/"
                     "verdict 一律经 exwrite 取号落盘，禁止直写 exchange/artifacts/、"
                     "exchange/verdicts/——撞号与漏记账实证 198/038/074）")
        # 模型路由注入（T6/S5）：body 单点组装（原在 _create_card 二次拼接，已收敛）
        # M2/Δ7：走 model_router.resolve_routing——load_class 禁配时 fallback 绕行，
        # DENIED 日志落在卡 body（路由声明）与返回日志双处留痕。
        final_route, route_log = model_router.resolve_routing(step["id"])
        goal += f"[model_router] {route_log} "
        if final_route:
            # 020 模型路由补链路：路由真正注入卡 model_override/provider_override
            # 字段（_create_card 落字），此处文案只作台账，不再自称「实际调用」。
            goal += (f"（模型路由：路由声明已注入卡字段 model_override="
                     f"{final_route['model']} provider_override="
                     f"{final_route['provider']}，执行日志须记录实际路由）")
        # M2/Δ6：skills_map owner 归属——注入解析后完整路径，执行者不再按名联想。
        owner_paths = self._resolve_skill_paths(step["id"])
        if owner_paths:
            goal += f"（skill 路径：{'; '.join(owner_paths)}）"
        # Z1/023（Δ8②）：产物 slug 注册表注入卡 body 环境段——exwrite.py L2 写侧
        # 门禁消费此串（gate_check 校验文件名 slug 段 ∈ 注册表），空注册表=门禁空转。
        registry = slug_registry.build_registry(self.template)
        if registry:
            goal += f"（EXWRITE_SLUG_REGISTRY={registry}）"
        # M2/Δ3：factcheck verdict 契约前置告知（执行者按契约写 frontmatter 字段）
        vc = step.get("verdict_consume")
        if vc:
            goal += (f"（verdict 契约：产物 frontmatter 必含 {vc['field']}: "
                     f"{vc['pass_value']}|changes_requested，引擎机器消费）")
        return goal

    def _resolve_skill_paths(self, step_id: str) -> list[str]:
        """skills_map 条目（{skill, owner}）→ 解析后路径列表（013 §5.2）。

        搜索序：<owner>/skills/<name> → skills-shared/<name>；找不到记
        「未解析:<skill>」占位（validate 层拒启是静态关卡，此处运行时兜底提示）。
        """
        entries = (self.template.get("skills_map") or {}).get(step_id) or []
        paths = []
        for e in entries:
            if isinstance(e, dict) and e.get("skill"):
                owner, name = e.get("owner"), e["skill"]
                if e.get("source") == "engine":
                    # 2026-09-27 吸引力批次：引擎源 skill——实存根在 engine/config/skills/，
                    # 与 owner 互斥二选一（validate 规则9 已把关实存，此处运行时兜底）
                    hit = Path(__file__).resolve().parent / "config" / "skills" / name / "SKILL.md"
                    paths.append(str(hit) if hit.exists() else f"未解析:engine/{name}")
                elif owner:
                    hit = Path(f"./agents-home/{owner}/skills/{name}")
                    paths.append(str(hit) if hit.exists() else f"未解析:{owner}/{name}")
                else:
                    shared = Path(f"./agents-home/skills-shared/{name}")
                    paths.append(str(shared) if shared.exists() else f"未解析:{name}")
            else:  # v1 兼容：裸字符串条目
                paths.append(str(e))
        return paths

    # ---- 派发 ----
    def _test_dispatch_allowed(self) -> bool:
        """test 模式建卡 opt-in 判定（t_2c740cd9 fail-closed 门的唯一放行口）。

        缺省 False = 拒绝。读取顺序：环境变量（临时演练）> 实例 state
        `allow_test_dispatch`（持久化演练实例）。state 读不到/坏 JSON → False
        （防呆方向：不可判定即拒绝，不倒向放行）。
        """
        if os.environ.get(TEST_DISPATCH_OPTIN_ENV) == "1":
            return True
        ip = getattr(self, "instance_path", None)
        if not ip:
            return False
        try:
            st = json.loads(Path(ip).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return st.get("allow_test_dispatch") is True

    def _create_card(self, step_id: str, step: dict, iteration: int,
                     body_suffix: str = "") -> str:
        """kanban 建卡（幂等键 wweng:<instance>:<step>，与 flow-engine 键空间隔离）。返回卡 id。

        A1①：body_suffix 供 upload/cover 步追加「权威标题」字段（建卡即落卡）。
        t_2c740cd9：mode != live 且无显式 opt-in → 拒建卡（raise），绝不发
        `hermes kanban create`（活跃板零接触）。
        """
        if (self.mode or "test") != "live" and not self._test_dispatch_allowed():
            raise RuntimeError(
                f"test 模式拒建卡（fail-closed，t_2c740cd9）：实例 {self.instance} "
                f"mode={self.mode or 'test'} 非 live，拒绝向活跃板 dispatch 步骤 "
                f"{step_id}（原会建出无人推进的幻影卡）。放行方式三选一：\n"
                f"  ① 演练实例：在实例 state 置 \"allow_test_dispatch\": true\n"
                f"  ② 临时演练：环境变量 {TEST_DISPATCH_OPTIN_ENV}=1\n"
                f"  ③ 测试用例：按 conftest 铁律 mock _resolve_step_card/_create_card")
        assignee = step.get("assignee") or "default"
        if assignee == "hermes":
            assignee = "default"
        title = f"[ww:{self.instance}] {step_id} (iter {iteration})"
        body = self._step_goal(step) + body_suffix
        key = f"{ENGINE_KEY_PREFIX}{self.instance}:{step_id}"
        # 020 模型路由补链路：建卡走 CLI 公共接口（hermes kanban create 原生
        # 支持 --model/--provider），把 resolve_routing 的最终路由真正写进卡
        # 字段；不自造 DB INSERT（建卡字段多、CLI 校验全，最稳一条）。
        final_route, _route_log = model_router.resolve_routing(step_id)
        cmd = ["hermes", "kanban", "create", title,
               "--assignee", assignee,
               "--idempotency-key", key,
               "--body", body,
               "--project", "multi-agents",
               "--created-by", "we-write-engine"]
        if final_route:
            cmd += ["--model", final_route["model"],
                    "--provider", final_route["provider"]]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        err = (r.stderr or "") + (r.stdout or "")
        if r.returncode != 0 and not any(w in err.lower() for w in
                                         ("already exists", "duplicate", "idempotency key")):
            raise RuntimeError(f"建卡失败 rc={r.returncode}: {err[-300:]}")
        m = re.search(r"t_[0-9a-f]+", err)
        return m.group(0) if m else "(卡已存在/幂等)"

    def _resolve_step_card(self, step_id: str) -> dict | None:
        """查本实例本步的卡（委托 kanban_client.resolve_step_card，键空间 wweng:）。

        ⚠️ 必须显式传 key_prefix=ENGINE_KEY_PREFIX（wweng:）：kanban_client 的缺省
        前缀是 flow-engine 的 engine:，用缺省值会读到 flow-engine 同名 step 的卡
        （撞键教训 t_b94a9132，见本文件头部）。
        """
        try:
            return kanban_client.resolve_step_card(
                self.instance, step_id, key_prefix=ENGINE_KEY_PREFIX)
        except Exception:  # noqa: BLE001 — 查询异常降级（不阻断）
            return None

    # ---- 卡评论通道（005 需求2/4：材料注入选题卡 comment / 拍板意见注入返工卡 comment）----

    def _post_card_comment(self, card_id: str, text: str) -> bool:
        """hermes kanban comment 写卡评论（引擎侧唯一评论写通道）。

        失败不阻断（返回 False，调用方记 history 下拍重试），绝不影响卡与流水线状态。
        """
        if not card_id or not card_id.startswith("t_"):
            return False  # "(卡已存在/幂等)" 等占位返回无真实卡 id，无可附评论
        try:
            r = subprocess.run(
                ["hermes", "kanban", "comment", card_id, text,
                 "--author", "we-write-engine"],
                capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return r.returncode == 0

    def _first_dispatch_step_id(self) -> str | None:
        """模板首个 dispatch 步（= 选题卡，材料注入点）。"""
        for s in self.template.get("steps") or []:
            if s.get("type") == T_DISPATCH:
                return s.get("id")
        return None

    def _maybe_inject_materials(self, step_id: str, card_id: str, state: dict) -> None:
        """需求2：选题卡派卡拍，把 state.materials 注入卡 comment（手动补 comment 的机制化）。

        幂等：state.materials_injected 置位后不再注；写失败保持未置位，下拍
        在途分支重试。材料正文单条截 500 字（防超长评论）。
        """
        mats = state.get("materials") or []
        if not mats or state.get("materials_injected"):
            return
        if step_id != self._first_dispatch_step_id():
            return
        lines = [f"[we-write-engine 材料注入 · 实例 {self.instance}] "
                 f"user 提交的写作材料，选题时一并参考（{len(mats)} 条）："]
        for m in mats:
            lines.append(f"- [{m.get('type', 'text')}] {str(m.get('content', ''))[:500]}")
        if self._post_card_comment(card_id, "\n".join(lines)):
            state["materials_injected"] = True
            state["history"].append({"step": step_id, "action": "materials_inject",
                                     "log": f"材料 {len(mats)} 条注入选题卡 comment（{card_id}）"})
        else:
            state["history"].append({"step": step_id, "action": "materials_inject_retry",
                                     "log": f"⚠ 材料评论写入失败（{card_id}），下拍重试"})

    def _maybe_flush_rework_note(self, card_id: str, state: dict) -> None:
        """需求4：门拍板 `C|意见` 打回后，意见注入返工卡 comment。

        在返工靶点卡重开/首建/在途拍调用；写成功消费 pending_rework_note 防重复，
        失败保留 pending 下拍重试（意见另在 history.decision 永久留痕，无丢失风险）。
        """
        pend = state.get("pending_rework_note")
        if not pend:
            return
        text = (f"[返工单 · 门 {pend.get('gate')} · 实例 {self.instance}] "
                f"user 修改意见（含补充材料时一并照办）：\n{pend.get('note', '')}")
        if self._post_card_comment(card_id, text):
            state["pending_rework_note"] = None
            state["history"].append({"step": state.get("step"), "action": "rework_note_inject",
                                     "log": f"拍板意见注入返工卡 comment（{card_id}）"})

    # ---- 派发标记（B2 修复配套；A2/20260920 改标记撤销方案）----
    # state["dispatched"][step_id] = 派卡时的 iteration。作用是区分两种"卡已 done"：
    #   ① 本步已派过卡（标记在）→ 本轮 done 是交付完成 → 推进；
    #   ② 回流/首进本步（标记不在）→ done 是上一轮残留 → 必须重开重做。
    # 没有这个标记，done 卡既要"推进"又要"打回重开"，无法判定 → 会无限重开。
    # A2：标记不再随 run() 推进整体清空（整体清空会让正常推进后的 done 卡被误判
    # 成"上一轮残留"而误重开，article-003 iter15 实证）；打回/返工语义改由
    # 打回入口对目标步显式 _pop_dispatched 表达（见各打回分支）。

    def _dispatched(self, state: dict, step_id: str) -> bool:
        return step_id in (state.get("dispatched") or {})

    def _mark_dispatched(self, state: dict, step_id: str) -> None:
        state.setdefault("dispatched", {})[step_id] = state["iteration"]

    def _pop_dispatched(self, state: dict, step_id: str | None) -> None:
        """A2：打回/返工入口显式撤销目标步派发标记（标记不存在时静默）。

        多 pop 只会多一次重开判定检查，少 pop 会漏重开——不确定处宁多 pop。
        """
        if step_id:
            state.get("dispatched", {}).pop(step_id, None)

    def _pop_dispatched_chain(self, state: dict, target: str | None) -> None:
        """016 #5（D1 修复）：打回范围扩到上游整链——从返工靶点起（模板序）
        到链尾的所有派发标记一并撤销，含 dispatch_group 成员 id。

        为什么：reject 打回靶点后，返工流会重走靶点→本步之间的每个 dispatch
        步；这些步的旧标记不清，回流时 done 卡会被判「本轮已交付」而跳过重开
        （polish 旧稿直进终稿取件，D1 实证）。标记只在该步被重新进入时才消费，
        故多 pop 无副作用（靶点上游步骤的标记保留，不触发无谓重做）。
        """
        steps = self.template.get("steps") or []
        ids = [s.get("id") for s in steps if s.get("id")]
        try:
            start = ids.index(target) if target else 0
        except ValueError:
            start = 0  # 靶点不在模板（防御）：全撤，宁多不少
        for sid in ids[start:]:
            self._pop_dispatched(state, sid)
        for s in steps:  # 组成员是嵌套 id（不在顶层序内），统一撤——成员本无标记
            for m in s.get("members") or []:
                if isinstance(m, dict) and m.get("id"):
                    self._pop_dispatched(state, m["id"])

    # ---- A1①：权威标题解析与注入（ww_upload / ww_cover_ai 建卡路径）----

    def _latest_artifact(self, pattern: str) -> Path | None:
        """exchange/artifacts 下 glob 取 mtime 最新（测试经 WW_EXCHANGE_DIR 重定向）。"""
        matches = [m for m in (EXCHANGE_DIR / "artifacts").glob(pattern) if m.is_file()]
        return max(matches, key=lambda m: m.stat().st_mtime) if matches else None

    def _last_gate_final_note(self, state: dict) -> str:
        """gate_final 最近一次带意见拍板的 note（C 路径落 history）。"""
        note = ""
        for h in state.get("history") or []:
            if (h.get("action") == "decision" and h.get("step") == "ww_gate_final"
                    and h.get("note")):
                note = str(h["note"])
        return note

    def _resolve_authoritative_title(self, state: dict) -> tuple[str, str]:
        """权威标题解析（A1①优先级，user 2026-09-20 拍板）：
        ① gate_final 拍板 note 指定（候选N / 直接给标题文本）
        ② ww-title 候选呈报文件存在 → 默认候选1（禁止回落端木原稿标题）
        ③ 无候选文件 → 退回 ww_polish 最新产物 archive.split_article() 的 H1
        返回 (title, 来源描述)；title 为空串 = 解析失败（调用方 fail-closed）。"""
        cand_file = self._latest_artifact("*-ww-title*.md")
        candidates: list[str] = []
        if cand_file:
            try:
                candidates = _parse_title_candidates(
                    cand_file.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                candidates = []
        note = self._last_gate_final_note(state)
        if note:
            m_cand = _AUTH_CAND_NOTE_RE.search(note)
            if m_cand and candidates:
                n = int(m_cand.group(1))
                if 1 <= n <= len(candidates):
                    return candidates[n - 1], f"拍板候选{n}，{cand_file.name}"
                # 指定越界：保守回落候选1，来源注明原因（不静默）
                return candidates[0], (f"候选1，{cand_file.name}"
                                       f"（拍板候选{n} 超出候选数 {len(candidates)}，回退默认）")
            m_txt = _AUTH_TITLE_PICK_RE.search(note) or _AUTH_TITLE_EXPLICIT_RE.search(note)
            if m_txt:
                t = m_txt.group(1).strip().strip("「」\"'“”*。，；")
                if len(t) >= 6:
                    return t, "拍板指定标题文本"
        if candidates:
            src = f"候选1，{cand_file.name}" if cand_file else "候选1"
            return candidates[0], src + "（拍板未指定标题，默认候选1）"
        polish = self._latest_artifact("*-ww-polish*.md")
        if polish:
            try:
                title, _ = archive.split_article(
                    polish.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                title = ""
            if title:
                return title, f"polish H1，{polish.name}（无候选呈报文件，回退解析）"
        return "", ("候选呈报文件缺失/无候选且 polish H1 为空"
                    + (f"（{polish.name}）" if polish else ""))

    def _authoritative_title_block(self, state: dict) -> str:
        """卡 body 注入块：成功 = 权威标题字段；失败 = 显式禁上传（U3 fail-closed）。"""
        title, src = self._resolve_authoritative_title(state)
        if title:
            return (f"【权威标题：{title}（来源：{src}，引擎解析，禁止更改）】"
                    f"title 一律取本字段，禁止 worker 自行取题、禁止 free-form 标题。")
        return (f"【权威标题解析失败，禁止上传】（原因：{src}；"
                f"解析不出权威标题就不得上传，禁止自行取题放行。）")

    def _maybe_inject_authoritative_title(self, sid: str, card_id: str,
                                          state: dict) -> None:
        """A1①：upload/cover 卡在途/存量场景的权威标题补投递（comment 通道，
        沿用 _maybe_inject_materials 机制）。首派已随 body 落卡，此处置位防重；
        写失败保持未置位下拍重试。"""
        if sid not in AUTH_TITLE_STEPS or state.get("auth_title_injected"):
            return
        text = f"[权威标题注入 · 实例 {self.instance}] {self._authoritative_title_block(state)}"
        if self._post_card_comment(card_id, text):
            state["auth_title_injected"] = True
            state["history"].append({"step": sid, "action": "auth_title_inject",
                                     "log": f"权威标题注入卡 comment（{card_id}）"})

    # ---- verdict 机器消费（M2/Δ3 verdict_consume / Δ5 verdict_gate）----

    def _consume_verdict(self, step_id: str, artifact_glob: str,
                         consume: dict, state: dict, step: dict) -> dict | None:
        """读产物 frontmatter 契约字段，决定放行或打回。返回 None=放行。

        - pass → None（调用方照常推进）
        - changes_requested / 字段缺失 → 打回 on_reject：iter+1 + 注单
        - FAIL 超过 max_retry → escalate 警示放行（带警示上门3，user 终审，
          charter 已拍板）——escalate 不阻断，但 history 落警示条。
        """
        field = consume["field"]
        pass_value = consume.get("pass_value", "approved")
        on_reject = consume.get("on_reject")
        art = _resolve_artifact(artifact_glob)
        val = read_frontmatter_field(art, field) if art else None
        deliver_note = ""
        if val == pass_value:
            # 016 #3：字段放行前须过实体门（正文非空 + status=done）——空壳
            # verdict 带 approved 字段被消费的实证 = 141-duanmu。
            dok, dreason = verdict_deliverable_ok(art)
            if dok:
                # 016 #6：verdict_pass 留痕——_escalation_warnings 据此判定
                # 同 step 更早的 escalate 警示已被后续通过覆盖，不再上门3。
                state["history"].append({
                    "step": step_id, "action": "verdict_pass",
                    "detail": f"{field}={val}",
                    "log": f"✅ verdict {step_id}: {field}={val} 通过"
                           + (f"（{art.name}）" if art else "")})
                return None  # 放行
            deliver_note = (f"（字段={val} 但产物未过实体门：{dreason}——"
                            f"补全正文并置 status: done 后重新落盘）")
        # 不通过：FAIL/STALE/缺失/值不在词表/实体门未过 → 视同 changes_requested
        retry_used = sum(1 for h in state["history"]
                         if h.get("step") == step_id
                         and h.get("action") == "verdict_reject")
        max_retry = int(step.get("max_retry", 2))
        if retry_used >= max_retry:
            warn = (f"⚠🚨 verdict {step_id}: {field}={val or '缺失'} 连续 "
                    f"{retry_used} 轮不通过（超 max_retry={max_retry}）——"
                    f"带警示放行上门3，user 终审（charter 拍板条款）"
                    + (f"｜{deliver_note}" if deliver_note else ""))
            state["history"].append({"step": step_id, "action": "verdict_escalate",
                                     "detail": f"{field}={val}", "log": warn})
            return None  # 带警示放行，不阻断（user 保留终审权）
        items = [f"{field}={val or '（字段缺失，视同 changes_requested）'}",
                 f"产物: {art.name if art else '(未找到)'}",
                 "按问题清单节逐条修复后重新落盘产物并更新 verdict 字段"]
        if deliver_note:
            items.append(deliver_note)
        state["iteration"] += 1
        # A2：打回语义显式撤销标记——016 #5（D1 修复）：打回范围扩到上游整链，
        # 从返工靶点到本步之间（模板序）所有已派发步骤一并撤销（含 polish 类），
        # 该段回流重跑时 done 卡才会重开（ww_factcheck 打回 ww_write 时
        # ww_polish 旧标记残留曾导致返工稿取不到终稿）。
        self._pop_dispatched_chain(state, on_reject)
        self._pop_dispatched(state, step_id)
        state["history"].append({"step": step_id, "action": "verdict_reject",
                                 "detail": f"{field}={val}",
                                 "log": f"❌ verdict {step_id}: 不通过 → 打回 {on_reject}（{retry_used+1}/{max_retry}）"})
        return {"next": on_reject,
                "log": (f"❌ verdict {step_id}: {field}={val or '缺失'} → "
                        f"打回 {on_reject}（{retry_used+1}/{max_retry}）｜注单: "
                        f"{build_reject_body(step_id, items)[:120]}…")}

    def _do_dispatch(self, step: dict, state: dict) -> dict:
        """派卡 + 等卡 done（B2 修复：两拍语义，不再建卡即推进）。

        拍1：建卡 / 幂等命中在途卡 / 重开 done 卡 → next=None 留本步；
        拍2：卡 status=done → 推进 step["next"]。
        为什么必须留步：建卡即推进时 state.step 会指向尚未开工的下游步，进程中断
        后无法判定断点合法性，dispatcher 会空跑下游卡（违反 charter S4）。
        iteration 只在实际派工（建卡/重开）时自增——留步轮询是心跳空转，若每拍
        自增会把 iteration 刷爆（pipeline max_iterations=60 会被无意义耗尽），
        且卡标题的 iter 号会失真。
        """
        sid = step["id"]
        # A1①：upload/cover 步建卡即解析权威标题落卡 body（解析失败=禁上传字样，
        # 同样落卡，worker 按卡 fail-closed）；重开/在途场景走 fix_hint/comment 补投递。
        body_suffix = ("\n" + self._authoritative_title_block(state)
                       if sid in AUTH_TITLE_STEPS else "")
        # M3/Δ10 + E1：upload/cover 步 + mode=test → 强制 mock 短路
        # （P3 防呆：测试稿不进真草稿箱、测试封面不真调 bl）
        if _is_mock_short_circuit(sid) and getattr(self, "mode", "test") == "test":
            return self._mock_upload(step, state)
        card = self._resolve_step_card(sid)
        already = self._dispatched(state, sid)

        if card and card["status"] == "done" and not already:
            # 打回重做（verify/on_reject 回流时）：重开为 ready（原守卫语义保留）
            state["iteration"] += 1
            log = (f"dispatch {sid}: 卡 {card['id']} 已 done，重开重做")
            # Z6/025：C4 打回时 fix_hint 带具体违规清单（worker 拿得到要修什么）
            fix_hint = "本轮为打回重做：只做增量修复，勿全量重跑"
            if sid == "ww_layout" and getattr(self, "_last_check_reject", None):
                fix_hint += "\n" + self._last_check_reject
            if sid in AUTH_TITLE_STEPS:
                fix_hint += "\n" + self._authoritative_title_block(state)
            ok = kanban_client.reopen_done_card(
                card["id"], new_title=None,
                reason=f"回流重做 iter={state['iteration']}",
                mode="incremental",
                fix_hint=fix_hint)
            if not ok:
                # 重开失败不打派发标记 → 下一拍重试重开（不留死角）
                log += "（重开失败，保持等待）"
                state["history"].append({"step": sid, "action": "dispatch", "log": log})
                return {"next": None, "log": f"⏳ {log}"}
            # 020 模型路由补链路：重开同一事务域回填路由（双 NULL 才回填，
            # 不覆盖人工 set-model pin）——修存量 override 全空的卡。
            # 回填失败不阻断 dispatch（降级记日志，下拍在途分支重试）。
            fr, _ = model_router.resolve_routing(sid)
            if fr:
                try:
                    kanban_client.ensure_card_model(card["id"], fr["provider"], fr["model"])
                except Exception as e:  # noqa: BLE001 — 回填降级不阻断派卡
                    log += f"（路由回填失败降级: {e}）"
            self._mark_dispatched(state, sid)
            state["history"].append({"step": sid, "iteration": state["iteration"],
                                     "action": "dispatch_reopen", "log": log})
            # 005 需求2/4：重开拍即返工现场——材料（若未注）与拍板意见经卡 comment 送达
            self._maybe_inject_materials(sid, card["id"], state)
            self._maybe_inject_authoritative_title(sid, card["id"], state)
            self._maybe_flush_rework_note(card["id"], state)
            return {"next": None,
                    "log": f"⏳ dispatch {sid}: 卡在途，留本步等待（{log}）"}

        if card and card["status"] == "done":
            # 本步已派过卡且卡已 done → 交付完成；M2/Δ3：verdict_consume 机器消费
            vc = step.get("verdict_consume")
            if vc and step.get("artifact"):
                rej = self._consume_verdict(sid, step["artifact"], vc, state, step)
                if rej:
                    return rej
            log = f"dispatch {sid}: 卡 {card['id']} done → 推进"
            # A2：正常推进补标记——标记不再整体清空，done 分支保持标记在，
            # 下游步与断点恢复都靠它区分"本轮已交付"与"上一轮残留"。
            self._mark_dispatched(state, sid)
            state["history"].append({"step": sid, "iteration": state["iteration"],
                                     "action": "dispatch_done", "log": log})
            return {"next": step.get("next"), "log": f"✅ {log}"}

        if card:
            # 在途（ready/running/todo/blocked）：幂等跳过建卡，留本步等 done
            # 020 模型路由补链路：在途卡双 NULL 时回填路由（修存量 427 张空卡；
            # 运行中卡字段更新对当前 run 无效，下次 dispatch 生效）。
            # 回填失败不阻断等待（降级，下拍重试）。
            fr, _ = model_router.resolve_routing(sid)
            if fr:
                try:
                    kanban_client.ensure_card_model(card["id"], fr["provider"], fr["model"])
                except Exception:  # noqa: BLE001 — 回填降级不阻断等待
                    pass
            if not already:
                self._mark_dispatched(state, sid)
            # 005 需求2/4：评论写失败的重试拍（材料/返工意见未消费完在此补投）
            self._maybe_inject_materials(sid, card["id"], state)
            self._maybe_inject_authoritative_title(sid, card["id"], state)
            self._maybe_flush_rework_note(card["id"], state)
            log = (f"dispatch {sid}: 卡 {card['id']} 在途"
                   f"（幂等跳过 status={card['status']}）")
            state["history"].append({"step": sid, "action": "dispatch_wait", "log": log})
            return {"next": None,
                    "log": (f"⏳ dispatch {sid}: 卡在途，留本步等待"
                            f"（卡 {card['id']} status={card['status']}）")}

        # 无卡：首派（或卡被归档/丢失 → 幂等键保证重建不产生重复卡）
        state["iteration"] += 1
        card_id = self._create_card(sid, step, state["iteration"],
                                    body_suffix=body_suffix)
        self._mark_dispatched(state, sid)
        # 005 需求2：选题卡首派拍注入材料 comment；需求4：返工靶点卡首建拍投递意见
        self._maybe_inject_materials(sid, card_id, state)
        self._maybe_flush_rework_note(card_id, state)
        log = f"dispatch {sid}: 建卡 {card_id} (assignee={step.get('assignee', 'default')})"
        state["history"].append({"step": sid, "iteration": state["iteration"],
                                 "action": "dispatch", "log": log})
        return {"next": None, "log": f"⏳ {log}，卡在途，留本步等待"}

    def _do_dispatch_group(self, step: dict, state: dict) -> dict:
        """并行组：按 members 逐个建卡（幂等键含 member id），任一失败 fail-fast。

        B2 修复：成员卡全 done 才推进 next，否则留本步（下游 wait 屏障只判产物，
        卡状态由本步把关）。组内不重开 done 卡——沿用原幂等跳过语义：回流再进本步
        时成员卡若全 done 直接放行，不重做（组成员无 on_reject 回流路径）。
        """
        members = step.get("members") or []
        if not members:
            raise RuntimeError(f"dispatch_group {step['id']}: 缺 members 列表")
        results = []
        pending = []
        for m in members:
            mid = m.get("id")
            if not mid:
                raise RuntimeError(f"dispatch_group {step['id']}: member 缺 id")
            mstep = dict(m)
            mstep.setdefault("description",
                             f"{step.get('description', '')} [{mid}]")
            card = self._resolve_step_card(mid)
            if card and card["status"] == "done":
                results.append(f"{mid}:done")
                continue
            if card and card["status"] in ("ready", "running", "todo", "blocked"):
                results.append(f"{mid}:{card['status']}")
                pending.append(mid)
                continue
            card_id = self._create_card(mid, mstep, state["iteration"])
            results.append(f"{mid}:{card_id}")
            pending.append(mid)
        if pending:
            log = (f"dispatch_group {step['id']}: {len(members)} 卡就绪，"
                   f"{len(pending)}/{len(members)} 张未完成 ({'; '.join(results)})")
            state["history"].append({"step": step["id"], "action": "dispatch_group_wait",
                                     "log": log, "pending": pending})
            return {"next": None, "log": f"⏳ {log}，留本步等待"}
        log = (f"✅ dispatch_group {step['id']}: {len(members)} 卡就绪 "
               f"(全 done: {'; '.join(results)}) → 推进")
        state["history"].append({"step": step["id"], "action": "dispatch_group", "log": log})
        return {"next": step.get("next"), "log": log}

    def _do_wait(self, step: dict, state: dict) -> dict:
        """组屏障：artifacts 全部存在才放行；超轮次 escalate（不计轮，需人工处置）。"""
        artifacts = step.get("artifacts") or ([step["artifact"]] if step.get("artifact") else [])
        missing = [a for a in artifacts if _resolve_artifact(str(a)) is None]
        # t_a6da5fa1：空壳产物显式报出——被硬门拒收后屏障表现为「缺产物」，
        # 不报出根因就变成静默变慢（守 §16 不静默铁律）。
        shells = sorted({p.name for a in artifacts for p in _shell_artifacts(str(a))})
        shell_note = (f"｜⚠ 空壳产物被拒收（正文非空硬门 t_a6da5fa1）: {', '.join(shells[:5])}"
                      if shells else "")
        if not missing:
            # M2/Δ5：verdict_gate——屏障全齐后引擎机器消费各 verdict 字段
            vg = step.get("verdict_gate")
            if vg:
                items = []
                for a in artifacts:
                    art = _resolve_artifact(str(a))
                    val = read_frontmatter_field(art, vg["field"]) if art else None
                    # 016 #3：字段之外绑实体门（正文非空 + status=done）
                    dok, dreason = verdict_deliverable_ok(art)
                    if val != vg.get("pass_value", "approved") or not dok:
                        note = "" if val == vg.get("pass_value", "approved") \
                            else f"{vg['field']}={val or '缺失'}"
                        if not dok:
                            note += ("；" if note else "") + f"实体门未过（{dreason}）"
                        items.append(f"{art.name if art else a}: {note}")
                if items:
                    on_reject = vg.get("on_reject")
                    state["iteration"] += 1
                    # A2 + 016 #5：verdict_gate 打回 → 撤销返工靶点起整链标记
                    # （双审打回时 polish/write 旧标记残留曾致返工稿取不到终稿）
                    self._pop_dispatched_chain(state, on_reject)
                    state["history"].append({
                        "step": step["id"], "action": "verdict_gate_reject",
                        "detail": "; ".join(items),
                        "log": f"❌ verdict_gate {step['id']}: 任一 changes_requested → 打回 {on_reject}"})
                    return {"next": on_reject,
                            "log": (f"❌ verdict_gate {step['id']}: 不通过 → 打回 "
                                    f"{on_reject}｜问题清单: {'; '.join(items)[:150]}")}
            state["history"].append({"step": step["id"], "action": "wait",
                                     "detail": f"产物齐: {len(artifacts)}"})
            return {"next": step.get("next"), "log": f"✅ wait {step['id']}: 产物已齐"}
        # 先落历史再计轮（含本轮）
        state["history"].append({"step": step["id"], "action": "wait_missing",
                                 "detail": f"缺 {len(missing)} 个"})
        esc_max = int(step.get("wait_escalate_max", 3))
        waited = sum(1 for h in state["history"]
                     if h.get("step") == step["id"] and h.get("action") == "wait_missing")
        if waited >= esc_max:
            # Z2b/023（Δ8③）：屏障失配近似候选诊断——对缺失产物逐个做近似匹配，
            # 命中注册表近似条目时在 escalate 行给出「期望 slug=X，发现近似候选 Y」
            registry = slug_registry.build_registry(self.template)
            hints = []
            for a in missing:
                m = re.search(r"\*-([\w-]+)\*\.md$", str(a).replace("\\", "/"))
                if m:
                    hint = diagnose_slug_mismatch(m.group(1), registry)
                    if hint:
                        hints.append(hint)
            state["history"].append({
                "step": step["id"], "action": "wait_escalate",
                "missing": [str(m) for m in missing],
                "log": f"⚠🚨 wait {step['id']}: 产物缺失 {waited}/{esc_max} 轮，需人工处置"
                       + (f"｜{shell_note}" if shell_note else "")
                       + ("｜近似诊断: " + "; ".join(hints) if hints else "")})
            return {"next": None,
                    "log": (f"⚠🚨 wait {step['id']}: 缺 {missing[0]} 等 "
                            f"{len(missing)} 个产物 {waited}/{esc_max} 轮（拍板点，需人工处置）"
                            + shell_note
                            + ("｜" + "; ".join(hints) if hints else ""))}
        return {"next": None,
                "log": f"⏳ wait {step['id']}: 等待 {len(missing)} 个产物（{waited}/{esc_max}）"
                       + shell_note}

    def _group_members(self, step: dict) -> list[str]:
        """找模板中 next 指向本屏障的 dispatch_group 的成员 id 列表。"""
        for s in self.template.get("steps", []):
            if s.get("next") == step["id"] and s.get("type") == T_DISPATCH_GROUP:
                return [m.get("id") for m in s.get("members", []) if m.get("id")]
        return []

    def _escalation_warnings(self) -> list[str]:
        """Z4/023（Δ3）：收集本实例 history 中所有 escalate 警示（门3 拍板现场呈现，
        charter「user 保留终审权」的通道实现——警示数据不再只躺在 history）。

        016 #6：verdict_escalate 警示若在其来源轮次之后已有同 step 的
        verdict_pass（016 #3 留痕），说明问题已被后续轮次修复——不再显示，
        消除 article-005 门3 假警示类问题（旧警示永久滞留误导终审）。
        """
        hist = self._load_state().get("history") or []
        warns = []
        for i, h in enumerate(hist):
            if h.get("action") in ("verdict_escalate", "wait_escalate", "check_escalate"):
                # verdict_escalate = 事实核查/审计超限 → charter 固定话术
                if h["action"] == "verdict_escalate":
                    covered = any(later.get("step") == h.get("step")
                                  and later.get("action") == "verdict_pass"
                                  for later in hist[i + 1:])
                    if covered:
                        continue  # 已被后续轮次通过覆盖，不再上门3
                    warns.append(f"⚠ 警示（{h.get('step')}）：事实核查未全清，"
                                 f"user 可拍板豁免或终止（{h.get('detail', '')}）")
                else:
                    warns.append(f"⚠ 警示（{h.get('step')}）：{h.get('log', '')}")
        return warns

    def _do_decision(self, step: dict, state: dict) -> dict:
        decision_file = INSTANCES_DIR / f"{self.instance}.decision"
        if not decision_file.exists():
            # 门6/拍板门默认预设：user 可在 pipeline.yaml 顶层 decision_defaults
            # （按门 id → 默认选项 key）或步骤级 default_decision 预设默认拍板。
            # 命中即自动落拍（留痕 history，source=auto_default），不再等待 user。
            # 066 §7：第②顺位 <name>.defaults.json（ww_panel「预设默认」开关写的
            # 实例级默认映射）——消费顺位 ①.decision → ②defaults.json →
            # ③pipeline decision_defaults/default_decision → ④等待。
            # 066 §7：第②顺位 <name>.defaults.json（ww_panel「预设默认」开关写的
            # 实例级默认映射）——消费顺位 ①.decision → ②defaults.json →
            # ③pipeline decision_defaults/default_decision → ④等待。
            # 20260921（user 裁定）：②③ 必须 opt-in——「预设默认」（defaults.json
            # 在）或「无人值守」（.unattended 在）任一开启才允许自动落拍；
            # 两者皆无时一律停在拍板点等 user（article-4 第二次选题被模板默认
            # A 无条件放行，实证）。选项勾选状态即授权状态。
            defaults_file = INSTANCES_DIR / f"{self.instance}.defaults.json"
            opt_in = (defaults_file.exists()
                      or (INSTANCES_DIR / f"{self.instance}.unattended").exists())
            default_key = None
            if opt_in and defaults_file.exists():
                try:
                    dmap = json.loads(defaults_file.read_text(encoding="utf-8"))
                    if isinstance(dmap, dict):
                        default_key = dmap.get(step["id"])
                except (OSError, ValueError):
                    default_key = None
            if opt_in:
                default_key = default_key or step.get("default_decision") \
                    or (self.template.get("decision_defaults") or {}).get(step["id"])
            if default_key:
                state["history"].append({"step": step["id"], "action": "decision",
                                         "decision": str(default_key),
                                         "source": "auto_default"})
                res = self._apply_decision(step, str(default_key), state)
                res["log"] = res["log"].replace("拍板[", "拍板(预设默认)[", 1)
                return res
            ctx = ""
            for cf in step.get("context_files", []):
                ap = _resolve_artifact(cf)
                if ap:
                    ctx += f" {ap.name}"
            warns = self._escalation_warnings()
            warn_txt = ""
            if warns:
                warn_txt = "\n" + "\n".join(warns) + "\n（警示项请在本拍板中一并裁定：豁免或终止）"
            return {"next": None,
                    "log": (f"⏸ 拍板点[{step['id']}]: {step.get('question', '待拍板')} "
                            f"参考产物: {ctx or '(无)'} — 等待拍板{warn_txt}")}
        decision = decision_file.read_text(encoding="utf-8").strip()
        decision_file.unlink(missing_ok=True)
        # 门7/带意见修改（问题清单 #7④）：决策文件支持 `C|意见内容` 格式——
        # C = 带修改意见修改：意见写进打回注单，自动返工后回到本门再确认。
        # 返工靶点 = 步骤级 rework_target（缺省 on_reject）；意见随 state 留痕。
        # 016 批次三 #11：B（驳回重做）同样支持 `B|说明`——复用 C 的机制
        # （意见写 pending_rework_note + history 留痕，返工卡重开时注入卡
        # comment）；无意见纯 B 维持原选项表语义（走 _apply_decision）。
        m_note = re.match(r"^([A-Za-z\d])\s*[|｜]\s*(.+)$", decision, re.S)
        if m_note and m_note.group(1).upper() in ("B", "C"):
            key = m_note.group(1).upper()
            note = m_note.group(2).strip()
            state["history"].append({"step": step["id"], "action": "decision",
                                     "decision": key, "note": note})
            # 005 需求4：意见挂 pending，返工靶点卡下一拍 dispatch 时注入卡 comment
            state["pending_rework_note"] = {"gate": step["id"], "note": note}
            target = step.get("rework_target") or step.get("on_reject") or step.get("next")
            # A2：B/C 路径返工 → 撤销返工靶点步标记（下一拍对该步 done 卡走重开）
            self._pop_dispatched(state, target)
            verb = "带修改意见修改" if key == "C" else "驳回重做（带说明）"
            return {"next": target,
                    "log": (f"🔁 拍板[{step['id']}]: {verb} → 打回 {target} 返工，"
                            f"完成后回本门再确认。意见：{note[:200]}")}
        state["history"].append({"step": step["id"], "action": "decision",
                                 "decision": decision})
        return self._apply_decision(step, decision, state)

    def _apply_decision(self, step: dict, decision: str, state: dict) -> dict:
        """按选项表消费一个拍板字符串（手动 decision 文件与预设默认共用）。

        A2：非顺延导航（选项 next ≠ 本门顺延 next = 重选/驳回类返工语义、
        未匹配选项落 on_reject）一律 pop 目标步标记——该步 done 卡下一拍重开。
        """
        options = step.get("options")
        if options:
            d = decision.strip().upper()
            for opt in options:
                key = str(opt.get("key", "")).strip().upper()
                if key and d == key:
                    nxt = opt.get("next")
                    if nxt:
                        if nxt != step.get("next"):
                            self._pop_dispatched(state, nxt)
                        return {"next": nxt,
                                "log": f"✅ 拍板[{step['id']}]: {decision}（{opt.get('label', '')}）→ 继续"}
                    return {"next": step.get("next"),
                            "log": f"✅ 拍板[{step['id']}]: {decision} → 继续"}
            back = step.get("on_reject")
            if back:
                self._pop_dispatched(state, back)
                return {"next": back,
                        "log": f"❌ 拍板[{step['id']}]: {decision} 未匹配选项 → 打回 {back}"}
            return {"next": step.get("next"), "log": f"拍板[{step['id']}]: {decision}"}
        if any(w in decision.lower() for w in ACCEPT_WORDS):
            return {"next": step.get("next"),
                    "log": f"✅ 拍板[{step['id']}]: {decision} → 继续"}
        back = step.get("on_reject")
        if back:
            self._pop_dispatched(state, back)
            return {"next": back, "log": f"❌ 拍板[{step['id']}]: {decision} → 打回 {back}"}
        return {"next": step.get("next"), "log": f"拍板[{step['id']}]: {decision}"}

    # ---- C4 check 执行器（M3/Δ9）----

    def _do_check(self, step: dict, state: dict) -> dict:
        """机器校验步：调 render_check.run_render_check 对 target 产物目录跑规则。

        PASS → 推进；FAIL → on_fail 打回注单（超 max_retry escalate 留在本步，
        需人工处置——同 wait escalate 语义）。
        """
        import render_check
        sid = step["id"]
        target = step.get("target")  # target 步的产物 = ww-layout 产物目录
        layout_glob = "./exchange/artifacts/*-ww-layout*"
        art = _resolve_artifact(layout_glob)
        # 校验目录：产物 MD 同目录 + glob 根目录兜底
        target_dir = art.parent if art else Path(layout_glob).parent
        # t_caa2bc28 根治：消费模板 step["rules"] 声明（按阶段取规则子集）。
        # ww_check_render（排版步产物）不判 ③img 素材域——排版产物按设计就是本地路径，
        # 由 ww_upload 上传后 verify_draft_exists U5④ 兜底。未声明 rules → 全量旧口径。
        rules = step.get("rules")
        result = render_check.run_render_check(target_dir, rules=rules)
        state["history"].append({"step": sid, "action": "check_render",
                                 "detail": result["violations"] or "PASS"})
        if result["ok"]:
            return {"next": step.get("next"),
                    "log": f"✅ check {sid}: 渲染完整性 PASS（rules={rules or '全量'}，"
                           f"含 warns {len(result['warns'])}）"}
        retry_used = sum(1 for h in state["history"]
                         if h.get("step") == sid and h.get("action") == "check_render")
        max_retry = int(step.get("max_retry", 2))
        if retry_used > max_retry:
            state["history"].append({"step": sid, "action": "check_escalate",
                                     "log": f"⚠🚨 check {sid}: 连续 {retry_used} 轮 FAIL，需人工处置"})
            return {"next": None,
                    "log": f"⚠🚨 check {sid}: FAIL {retry_used}/{max_retry} 超限（拍板点）: "
                           f"{result['violations'][:2]}"}
        state["iteration"] += 1
        # Z6/025：C4 FAIL 的 violations 必须进返工卡（worker 拿得到要修什么）——
        # 打回 log 带注单全文，_do_dispatch reopen 时把本步最新 check_render
        # 违规明细写进 fix_hint（见 _do_dispatch 内 check_reopen 分支）。
        self._last_check_reject = build_reject_body(sid, result["violations"])
        # A2：check 打回 → 撤销 on_fail 目标步标记（排版卡重开重做）
        self._pop_dispatched(state, step.get("on_fail"))
        return {"next": step.get("on_fail"),
                "log": (f"❌ check {sid}: {'; '.join(result['violations'])[:120]} → "
                        f"打回 {step.get('on_fail')}｜注单: {self._last_check_reject[:150]}…")}

    # ---- 上传 test 隔离（M3/Δ10/P3）----

    def _mock_upload(self, step: dict, state: dict) -> dict:
        """test 实例上传短路：不派真卡，进程内产 mock report（P3 根治）。

        Z7/025：mock 产物写 exchange/reports/（引擎写权内），**禁入
        exchange/artifacts/**（NNN 编号即审计空间，占号纪律铁律 3）。
        """
        rep_dir = EXCHANGE_DIR / "reports"
        rep_dir.mkdir(parents=True, exist_ok=True)
        rep = rep_dir / f"{self.instance}-upload-mock.md"
        rep.write_text(
            "---\nauthor: jingwei\nmock: true\n---\n\n# upload report (mock)\n"
            f"instance: {self.instance}\nmode: test（强制 mock，不触微信 API）\n",
            encoding="utf-8")
        state.setdefault("uploads", {}).update({"mock": True, "draft_media_id": None})
        state["history"].append({"step": step["id"], "action": "upload_mock",
                                 "log": "upload test 模式：mock report，不触 API"})
        return {"next": step.get("next"),
                "log": f"✅ upload {step['id']}: mock 上传（mode=test，草稿箱零触碰）"}

    def _do_done(self, step: dict, state: dict) -> dict:
        # M3/Δ11 原子收官：归档残留卡 → status=completed → 收官简报，
        # 收官顺序守卫校验终态与简报一致；归档异常降级不阻断终态
        # （G3/025：docstring 与实现统一为「降级」口径，重跑幂等）
        brief = self._closing_brief(state)
        cleanup_log = ""
        try:
            # 实例级收尾：只归档本实例的 wweng: 键卡（禁止前缀泛扫——
            # engine:we-write-engine: 前缀撞键误归档 flow-engine 活卡，t_b94a9132 实证）
            conn = kanban_client._connect()
            prefix = f"{ENGINE_KEY_PREFIX}{self.instance}:"
            rows = conn.execute(
                "SELECT id FROM tasks WHERE idempotency_key LIKE ? "
                "AND status IN ('ready','running','todo')",
                (prefix + "%",)).fetchall()
            with conn:
                now = int(__import__("time").time())
                for r in rows:
                    conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (r["id"],))
                    conn.execute(
                        "INSERT INTO task_events (task_id, kind, payload, created_at) "
                        "VALUES (?, 'status', ?, ?)",
                        (r["id"], json.dumps({"from": "ready/running/todo", "to": "archived",
                                              "reason": "实例收官收尾归档"}), now))
            conn.close()
            cleanup_log = f" | 收尾归档本实例残留卡 {len(rows)} 张"
        except Exception as e:  # noqa: BLE001 — 收尾兜底失败不阻断完成
            cleanup_log = f" | ⚠ 收尾清理异常: {e}"
            brief += f"\n⚠ 收尾清理异常: {e}"
        # 066 §8：成果归档钩子（成果区目录 + 8 标配件 + meta.yaml）。
        # 2026-09-26：模板内自带 *_archive 步（6 新模板）→ 归档由流程步负责
        # （精卫落 Wechat-project/<模板名>/…，user 规则），引擎钩子让位不双写；
        # 默认模板（无 archive 步）仍由本钩子收官归档（三层布局 archive.py 内）。
        # 归档失败/异常一律降级不阻断收官。t- 测试实例自动关闭。
        has_archive_step = any(str(s.get("id", "")).endswith("_archive")
                               for s in (self.template or {}).get("steps") or [])
        if has_archive_step:
            arc = {"status": "delegated",
                   "log": "归档由模板内 *_archive 步负责（引擎钩子让位，防双写）"}
        else:
            arc = archive.archive_instance(
                self.instance, state,
                artifacts_dir=EXCHANGE_DIR / "artifacts",
                verdicts_dir=EXCHANGE_DIR / "verdicts",
                instances_dir=INSTANCES_DIR)
        cleanup_log += f" | 归档[{arc['status']}] {arc['log']}"
        brief += f"\n- 归档: [{arc['status']}] {arc['log']}"
        self._last_archive = arc
        state["status"] = "completed"
        # C1（20260920）：完成时间戳（面板「完成日期」数据源；只写一次幂等，
        # 重跑收官不覆盖首拍时间）
        state.setdefault("completed_at",
                         datetime.now().astimezone().isoformat(timespec="seconds"))
        self._save_state(state)  # 原子收官：简报组装完成后才落终态
        self._write_closing_brief(brief)
        # G1/031：收官顺序守卫在 _do_done 内实调（031 指认守卫为死代码）——
        # 终态落盘后简报必须已存在；不满足即抛错（暴露顺序倒置，不留静默坏终态）。
        if not self.closing_order_guard(state):
            raise RuntimeError(
                "收官顺序守卫失败（G1/025）：state=completed 但收官简报未落盘，"
                "检查 _do_done 内 status 落盘与 _write_closing_brief 的先后顺序。")
        return {"next": None, "log": "🎉 本期文章流程完成" + cleanup_log}

    def closing_order_guard(self, state: dict) -> bool:
        """G1/025：收官顺序机器守卫——state 已 completed 时简报必须已落盘。

        变异 M4（顺序倒置：先写简报后落终态）在此断言下变红：
        倒置实现会在 state=completed 前就把简报写出去，而本守卫要求
        「completed ⇒ 简报存在」且简报内容含实例名（防占位空文件）。
        """
        if state.get("status") != "completed":
            return True  # 未到终态，不适用
        brief = (EXCHANGE_DIR / "reports" / f"{self.instance}-closing-brief.md")
        return brief.exists()

    def _closing_brief(self, state: dict) -> str:
        """收官简报（013 §6.4 字段固定）：media_id/耗时/4门/拦截统计/路由摘要。"""
        gates = {h["step"]: h.get("decision") for h in state["history"]
                 if h.get("action") == "decision"}
        intercepts = sum(1 for h in state["history"]
                         if h.get("action") in ("verdict_reject", "verdict_gate_reject"))
        uploads = state.get("uploads") or {}
        lines = [
            f"# 收官简报 {self.instance}",
            f"- draft_media_id: {uploads.get('draft_media_id') or '(test/mock 或未上传)'}",
            f"- mock: {uploads.get('mock', '(未上传)')}",
            f"- 全链迭代: {state['iteration']}",
            f"- 4门拍板: {json.dumps(gates, ensure_ascii=False)}",
            f"- verdict 打回拦截: {intercepts} 次",
        ]
        return "\n".join(lines)

    def _write_closing_brief(self, brief: str) -> None:
        try:
            out = EXCHANGE_DIR / "reports"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{self.instance}-closing-brief.md").write_text(
                brief + "\n", encoding="utf-8")
        except OSError as e:  # noqa: BLE001 — 简报落盘失败不阻断终态
            pass  # noqa: BLE001

    # ---- 主入口 ----
    def run(self, target_step: str | None = None) -> str:
        # 005 需求1：骨架模板拒跑（fail-closed）——status: skeleton = 未开放，
        # 推进即抛错（API 建实例已 400 前置拦截，此处兜底 CLI/直调路径）。
        # --status 不走 run()，不受影响（只读可查）。
        if (self.template or {}).get("status") == "skeleton":
            raise ValueError(
                f"模板 {getattr(self, 'template_id', '?')} 为骨架占位"
                f"（status: skeleton），未开放运行——请改用完整模板"
                f"（默认 {template_loader.DEFAULT_TEMPLATE_ID}）")
        state = self._load_state()
        if state["status"] in ("completed", "failed"):
            return f"实例已终态: {state['status']}"
        if target_step:
            if state["step"] != target_step:
                # 跳步 = 步骤转移，清派发标记（残留标记会把 done 卡误判成"本轮已交付"
                # 直接放行，跳过重做）；原地续跑（target 就是当前步）保留标记，
                # 断点恢复才不会重做已完成步骤（charter S4）。
                state["dispatched"] = {}
            state["step"] = target_step
            state["status"] = "running"
        if state["step"] is None:
            state["step"] = self.template["steps"][0]["id"]
            state["status"] = "running"
        step = self._step_by_id(state["step"])
        t = step["type"]
        if t == T_DISPATCH:
            res = self._do_dispatch(step, state)
        elif t == T_DISPATCH_GROUP:
            res = self._do_dispatch_group(step, state)
        elif t == T_WAIT:
            res = self._do_wait(step, state)
        elif t == T_DECISION:
            res = self._do_decision(step, state)
        elif t == T_DONE:
            res = self._do_done(step, state)
        elif t == T_CHECK:
            res = self._do_check(step, state)
        else:
            raise ValueError(f"未知步骤类型: {t}")
        if res.get("next"):
            state["step"] = res["next"]
            # A2（20260920）：标记不再整体清空——整体清空让"正常推进首次到达下游步"
            # 与"打回重做"无法区分，下游 done 卡被误重开（article-003 iter15 实证：
            # factcheck 通过推 headline 时 headline 已 done 卡被重开）。
            # dispatched[sid] 存在 = 该步已派卡且未被本轮任何打回撤销；
            # 打回由各入口显式 pop 表达（verdict_reject / verdict_gate_reject /
            # check on_fail / 拍板 C 返工 / 非顺延选项与 on_reject 导航）。
        self._save_state(state)
        return f"[{state['step']}] {res['log']}"

    def status(self) -> str:
        state = self._load_state()
        lines = [f"实例: {self.instance}", f"状态: {state['status']}",
                 f"模板: {state.get('template') or self.template_id}",
                 f"当前步骤: {state['step']}", f"迭代: {state['iteration']}",
                 f"模式: {state.get('mode', 'test')}"]
        if state["history"]:
            lines.append("历史:")
            for h in state["history"][-8:]:
                lines.append(f"  {h}")
        return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="we-write 写作流水线引擎")
    ap.add_argument("--instance", help="文章实例名（instances/<名>.json）")
    ap.add_argument("--step", help="从指定步骤续跑")
    ap.add_argument("--status", action="store_true", help="查看实例状态")
    ap.add_argument("--reset", action="store_true", help="重置实例")
    ap.add_argument("--list", action="store_true", help="列出全部实例")
    ap.add_argument("--decision", help="写入拍板决定（配合 --instance）")
    ap.add_argument("--mode", choices=["test", "live", "auto"], default="auto",
                    help="实例模式（M3/Δ10 防呆：缺省 auto=跟随实例 state；"
                         "首跑落缺省 test 不触真箱；显式与 state 冲突拒跑，Z5/031）")
    ap.add_argument("--template",
                    help="文章模板 id（005 需求1：缺省=实例 state 绑定/默认 "
                         "hot-topic-commentary；首跑绑定定终身，显式与 state 冲突拒跑）")
    args = ap.parse_args()

    if args.list:
        if INSTANCES_DIR.is_dir():
            for fp in sorted(INSTANCES_DIR.glob("*.json")):
                try:
                    st = json.loads(fp.read_text(encoding="utf-8"))
                    print(f"{st.get('instance', fp.stem)}: {st.get('status')} @ {st.get('step')}")
                except Exception:  # noqa: BLE001
                    print(f"{fp.stem}: (损坏)")
        return 0
    if not args.instance:
        ap.error("--instance 必填（或用 --list）")
    # Z5/031：auto = 跟随实例 state（mode=None 语义，wrapper 无参/缺省场景不降级）
    cli_mode = None if args.mode == "auto" else args.mode
    eng = WeWriteEngine(args.instance, mode=cli_mode, template=args.template)
    if args.reset:
        eng.instance_path.unlink(missing_ok=True)
        # 生命周期耦合（2026-10-10）：实例删除可能使引擎全停 → 心跳自停
        cron_coupling.reconcile_silent()
        print("已重置")
        return 0
    if args.decision:
        INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        (INSTANCES_DIR / f"{args.instance}.decision").write_text(args.decision, encoding="utf-8")
        print(f"拍板已写入: {args.decision}")
        return 0
    if args.status:
        print(eng.status())
        return 0
    print(eng.run(args.step))
    return 0


if __name__ == "__main__":
    sys.exit(main())
