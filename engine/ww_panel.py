"""ww_panel.py — we-write 写作流水线前面板仪表盘（066 方案 §0-§8，2026-09-13）。

单文件、纯标准库（http.server），端口 8660（避开 flow-engine 8650）。为什么独立
成面板：引擎是无心跳推进的批处理件，user 需要一个只读窗口随时看「每实例跑到哪、
谁在负责、卡在哪、拍板拍了什么」，并把拍板/预设/无人值守三类决定写成
instances/ 下的决策文件由引擎心跳消费（机制已验证，引擎零状态侵入）。

安全边界（charter 成果分离铁律）：
  - 面板只读 pipeline.yaml / instances/*.json / exchange 产物 + kanban 只读查询；
    禁 LLM、禁 subprocess，/api/state 全链无副作用（除 §5 无人值守通道写
    exchange/reports/unattended-alerts-*.md，内容变化才写）。
  - 唯一写盘位置：INSTANCES_DIR 下 .decision/.defaults.json/.unattended 三类
    决策文件 + 上述 reports 通道文件；engine/ 目录（含本文件所在处）零新增文件。
  - /api/artifact 路径白名单：仅 artifacts/verdicts/Wechat-project/reports 四根，
    resolve() 后 is_relative_to 校验，挡 ../ 穿越，越权一律 403。
  - subprocess 唯一豁免（016 批次三 #10，2026-09-29 user 拍板）：POST /reassign
    的 blocked 卡手动重派——`hermes kanban unblock` + `hermes kanban dispatch`，
    且仅限经 wweng:<instance>: 键空间解析出的卡 id、卡须 status=blocked；
    命令与参数由服务端构造，客户端 input 不进命令行（路径白名单/门禁同上）。
    通道实现见 _kanban_cli，测试经 monkeypatch 注入，不触真板。

为什么自带一个极小 YAML 子集解析器而不 import yaml：面板被要求只依赖标准库，
而它消费的 pipeline.yaml 结构固定（块映射/块序列/流式集合/块标量四形态），
子集解析比引入第三方依赖更可控（解析失败即拒答，不会半懂不懂地误读）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---- 路径与常量（全部 env 可覆盖，便于测试隔离；调用时现读，不在 import 时缓存）----
ENGINE_DIR = Path(__file__).resolve().parent
INSTANCES_DIR = Path(os.environ.get("WW_INSTANCES_DIR", str(ENGINE_DIR / "instances")))
EXCHANGE_DIR = Path(os.environ.get("WW_EXCHANGE_DIR", "./exchange"))
ARTIFACTS_DIR = EXCHANGE_DIR / "artifacts"
VERDICTS_DIR = EXCHANGE_DIR / "verdicts"
REPORTS_DIR = EXCHANGE_DIR / "reports"
WECHAT_PROJECT_DIR = Path(os.environ.get("WW_WECHAT_PROJECT_DIR", "Wechat-project"))
# kanban 幂等键前缀（engine.py 同源，禁止用缺省 engine:——那是 flow-engine 键空间，
# 撞键教训 t_b94a9132，见 engine.py 头部）
KEY_PREFIX = "wweng:"
DEFAULT_PORT = 8660

sys.path.insert(0, str(ENGINE_DIR))
import kanban_client  # noqa: E402 — 只读查询用（resolve_step_card/get_block_info）；写操作一概不调

# 005 需求1（2026-09-15）：pipeline.yaml 退役，面板消费模板目录。
# 2026-09-26 大修（user 报告 novel-pilot 面板显示 ww_topic 未运行）：面板此前写死
# 默认模板渲染所有实例 → 非默认模板实例（fa/hn/bm/ns/nl/sc-pilot）步骤链全错。
# 修复：按实例 state.template 加载各自模板（load_pipeline 带缓存），阶段/产物分组
# 对非 ww_ 步骤提供关键词兜底分类。template_loader 纯标准库，不破坏零 yaml 依赖纪律。
from config import template_loader  # noqa: E402

PIPELINE_PATH = template_loader.default_template_path()
TEMPLATES_DIR = template_loader.TEMPLATES_DIR

# ---- §2 阶段映射（方向/泳道，源自 pipeline.yaml 注释分组）----
STAGE_NAMES = ["选题大纲", "素材写作", "审查拍板", "排版发布"]
STEP_STAGE = {
    "ww_topic": 0, "ww_gate_topic": 0, "ww_outline": 0, "ww_gate_outline": 0,
    "ww_mat_group": 1, "ww_mat_barrier": 1, "ww_mat_select": 1, "ww_write": 1,
    "ww_polish": 1, "ww_factcheck": 1, "ww_headline": 1,
    "ww_dual_review": 2, "ww_review_barrier": 2, "ww_logic_review": 2, "ww_gate_final": 2,
    "ww_layout": 3, "ww_check_render": 3, "ww_upload": 3, "ww_closing_gate": 3,
    "ww_done": 3,
}

# 2026-09-26 大修：非 ww_ 模板步骤的阶段关键词分类（6 新模板步骤 id 后缀语义化，
# 按关键词兜底；命中序=从具体到宽泛）。未命中 → -1（面板显示「未知」不崩）。
_STAGE_KEYWORDS = [
    (3, ("_upload", "_layout", "_archive", "_closing_gate", "_check_render", "_cover", "_done")),
    (2, ("_factcheck", "_logic", "_audit", "_review", "_gate_final")),
    (1, ("_data", "_research", "_notes", "_analysis", "_write", "_draft", "_polish",
         "_chapter", "_dialogue", "_scenebreak", "_premise")),
    (0, ("_topic", "_outline", "_structure", "_logline", "_bible", "_gate_topic",
         "_gate_outline", "_gate_structure", "_gate_scene")),
]


def _stage_of(step_id: str) -> int:
    """步骤 → 阶段索引（ww_ 查表；其他模板按后缀关键词；未命中 -1）。"""
    if step_id in STEP_STAGE:
        return STEP_STAGE[step_id]
    for stage, suffixes in _STAGE_KEYWORDS:
        if step_id.endswith(suffixes):
            return stage
    return -1

# §4 产物分组中文名固定映射（值 None = 不产组，如屏障/check 步的 glob 与成员重复）
_ARTIFACT_GROUP_FIXED = {
    "ww_topic": "选题", "ww_outline": "大纲", "ww_mat_select": "选材",
    "ww_write": "草稿", "ww_polish": "润色稿", "ww_factcheck": "事实核查",
    "ww_headline": "标题", "ww_layout": "排版", "ww_check_render": "渲染校验",
    "ww_upload": "上传回执", "ww_review_audit": "双审", "ww_review_value": "双审",
    "ww_logic_review": "逻辑审",
}

# §6 并行列流程名固定映射（成员 id → 中文流程名）
FLOW_NAMES = {
    "ww_mat_news": "新闻", "ww_mat_history": "历史", "ww_mat_classic": "经典",
    "ww_mat_law": "法律", "ww_mat_econ": "经济",
    "ww_review_audit": "合规审", "ww_review_value": "价值审",
}

# §5 排查建议模板（≥2 条，按警示类型机械填充）
SUGGESTIONS_TPL = {
    "verdict_escalate": [
        "核查产物 frontmatter 契约字段是否落盘（factcheck_result/audit_result）",
        "[src:N] 引用源可达性复测",
        "max_retry 是否耗尽",
    ],
    "wait_escalate": [
        "屏障 artifacts glob 逐个实存检查",
        "对应 kanban 卡状态与 comments 复核",
    ],
    "check_escalate": [
        "render_check 四规则（no_placeholder/img_assets_exist/cover_spec/wechat_reachable）逐条复跑",
        "排版产物目录与 formatted.html 是否为本实例最新版本",
    ],
    "card_blocked": [
        "读卡 block reason（kanban_client.get_block_info）",
        "按 block kind 恢复（capacity/依赖/输入缺失）",
    ],
    "wait_stall": [
        "读卡 comments 看 worker 是否卡死",
        "考虑 reopen 或改派",
    ],
}

# §5 排查 prompt 模板（机械填参，逐字对齐 066 §5）
PROMPT_TPL = (
    "写作引擎实例 {instance} 在 {step} 阻塞（{action} 于 {time}）。详情: {detail}。"
    "请排查：读 engine/instances/{instance}.json 最近 history、"
    "kanban 键 wweng:{instance}:{step} 卡状态与 comments、"
    "exchange 产物 {artifact_glob} 是否落盘且 frontmatter status=done，"
    "给出根因与修复建议。"
)


# ---------------------------------------------------------------------------
# 极小 YAML 子集解析器（只服务 pipeline.yaml 的四形态，见模块 docstring）
# ---------------------------------------------------------------------------

def _yaml_strip_comment(line: str) -> str:
    """去掉行内注释：# 前有空白且不在引号内才算注释起点。"""
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and i > 0 and line[i - 1] in " \t":
            return line[:i].rstrip()
    return line


def _yaml_scalar(s: str):
    """标量：成对引号剥壳，其余原样保留为字符串（面板不需要数字类型）。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _split_flow(s: str) -> list[str]:
    """流式集合按逗号切分（引号与嵌套 [] {} 内的逗号不切）。"""
    parts, buf, depth, in_s, in_d = [], [], 0, False, False
    for ch in s:
        if ch == "'" and not in_d:
            in_s = not in_s
            buf.append(ch)
        elif ch == '"' and not in_s:
            in_d = not in_d
            buf.append(ch)
        elif not in_s and not in_d and ch in "[{":
            depth += 1
            buf.append(ch)
        elif not in_s and not in_d and ch in "]}":
            depth -= 1
            buf.append(ch)
        elif not in_s and not in_d and ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _parse_flow(s: str):
    """流式值：[...] 序列 / {...} 映射 / 标量。"""
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_flow(t) for t in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out = {}
        if not inner:
            return out
        for tok in _split_flow(inner):
            k, _, v = tok.partition(":")
            out[str(_yaml_scalar(k))] = _parse_flow(v)
        return out
    return _yaml_scalar(s)


def _parse_block(lines: list[tuple[int, str]], i: int, indent: int):
    """缩进块递归解析：返回 (值, 下一行下标)。lines 已是 (缩进, 去注释文本)。"""
    if i >= len(lines) or lines[i][0] < indent:
        return None, i
    # 单行流式集合 / 单行标量（序列项值整体，如 `- {key: A, ...}`、
    # `- "D:/.../*.md"`——值含冒号，必须先于 key: value 判定，否则会被拆成映射）
    first = lines[i][1]
    if first[:1] in "{[" and (i + 1 >= len(lines) or lines[i][0] != indent):
        return _parse_flow(first), i + 1
    if first[:1] in "\"'" and len(first) >= 2 and first.endswith(first[0]):
        return _yaml_scalar(first), i + 1
    if ":" not in first:
        return _yaml_scalar(first), i + 1
    # 块序列：`- ` 项
    if lines[i][1].startswith("- ") or lines[i][1] == "-":
        seq = []
        while i < len(lines) and lines[i][0] == indent and (
                lines[i][1].startswith("- ") or lines[i][1] == "-"):
            text = lines[i][1]
            if text == "-":
                # 裸 `- `：值是更深层缩进的嵌套块
                val, i = _parse_block(lines, i + 1, _next_indent(lines, i + 1, indent))
                seq.append(val)
            else:
                # `- key: v`：把项首行归一化为缩进 +2 的虚拟块，连同后续更深行一起解析
                item_indent = indent + 2
                virtual = [(item_indent, text[2:])]
                i += 1
                while (i < len(lines) and lines[i][0] >= item_indent
                       and not (lines[i][0] == indent
                                and (lines[i][1].startswith("- ") or lines[i][1] == "-"))):
                    virtual.append(lines[i])
                    i += 1
                val, _ = _parse_block(virtual, 0, item_indent)
                seq.append(val)
        return seq, i
    # 块映射
    mapping = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        text = lines[i][1]
        if ":" not in text:
            i += 1
            continue
        key, _, rest = text.partition(":")
        key = str(_yaml_scalar(key))
        rest = rest.strip()
        if rest in (">-", "|-", ">", "|"):
            # 块标量：吃后续更深缩进的连续行（> 折叠成空格拼接，| 保换行）
            i += 1
            buf = []
            while i < len(lines) and lines[i][0] > indent:
                buf.append(lines[i][1])
                i += 1
            joined = "\n".join(buf)
            mapping[key] = joined.replace("\n", " ").strip() if rest.startswith(">") else joined
            continue
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                # 嵌套块（更深缩进）
                val, i = _parse_block(lines, i + 1, lines[i + 1][0])
                mapping[key] = val
            elif i + 1 < len(lines) and lines[i + 1][0] == indent and lines[i + 1][1].startswith("- "):
                # 同级序列（YAML 允许 key: 与 - 同缩进）
                val, i = _parse_block(lines, i + 1, indent)
                mapping[key] = val
            else:
                mapping[key] = None
                i += 1
            continue
        mapping[key] = _parse_flow(rest)
        i += 1
    return mapping, i


def _next_indent(lines: list[tuple[int, str]], i: int, parent: int) -> int:
    """找 i 起下一个非空行的缩进（兜底返回 parent+2，防裸悬空）。"""
    if i < len(lines):
        return lines[i][0]
    return parent + 2


def load_pipeline(template_id: str | None = None) -> dict:
    """读模板 YAML（每次现读文件，mtime+size 缓存——测试 reload 模块/env 变更自动失效）。

    template_id=None → 默认模板（兼容既有调用与测试）；否则按 id 解析模板路径
    （template_loader fail-closed：非法/缺失 id 抛 ValueError，调用方兜底默认模板）。
    """
    if template_id:
        try:
            path = template_loader.template_path(template_id)
        except ValueError:
            path = PIPELINE_PATH   # 绑定模板文件被删等异常 → 兜底默认模板不 500
    else:
        path = PIPELINE_PATH
    try:
        st = path.stat()
        cache_key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        cache_key = (str(path), 0, 0)
    cached = _TPL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    text = path.read_text(encoding="utf-8")
    lines = []
    for raw in text.splitlines():
        stripped = _yaml_strip_comment(raw.rstrip())
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue  # 空行与整行注释（# 在行首时 _yaml_strip_comment 不剥，须在此跳过）
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    doc, _ = _parse_block(lines, 0, 0)
    if not isinstance(doc, dict):
        raise ValueError(f"pipeline.yaml 解析失败（非映射根）: {path}")
    doc.setdefault("steps", [])
    doc.setdefault("skills_map", {})
    # 缓存防爆：只留最近 16 个 (path, mtime) 组合
    if len(_TPL_CACHE) > 16:
        _TPL_CACHE.clear()
    _TPL_CACHE[cache_key] = doc
    return doc


_TPL_CACHE: dict[tuple, dict] = {}


def _template_for_state(state: dict | None) -> dict:
    """实例 state → 其绑定模板（2026-09-26 大修：面板按实例模板渲染，不再全用默认）。"""
    tid = (state or {}).get("template")
    if not tid:
        return load_pipeline()
    try:
        return load_pipeline(str(tid))
    except (OSError, ValueError):
        return load_pipeline()


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _step_map(tpl: dict) -> dict[str, dict]:
    """id → step 定义。"""
    return {s["id"]: s for s in tpl.get("steps", []) if isinstance(s, dict) and s.get("id")}


def _seq19(tpl: dict) -> list[dict]:
    """19 节点序列 = pipeline steps 顺序中排除 type==done 的步骤。"""
    return [s for s in tpl.get("steps", []) if s.get("type") != "done"]


# pipeline.yaml 里的产物 glob 是绝对路径（写死在交付树）。WW_EXCHANGE_DIR 环境覆盖
# （engine.py G-1/031 的测试隔离机制）对绝对 glob 本来无效 —— 这会让面板在任何沙箱
# 环境下都读到真实交付树的产物（§6 组列完成度因此恒为 100%，§6 验收3 无法成立）。
# 因此：glob 若落在「默认 exchange 根」之下，就改写为当前 EXCHANGE_DIR 下的同构相对
# 路径。生产环境 EXCHANGE_DIR == 默认根 → 改写前后完全等价，零行为变化。
_DEFAULT_EXCHANGE = Path("./exchange")


def _effective_glob(pattern: str) -> str:
    """把写死的默认 exchange 绝对 glob 映射到当前 EXCHANGE_DIR（env 覆盖生效）。"""
    p = str(pattern or "").replace("\\", "/")
    d = str(_DEFAULT_EXCHANGE).replace("\\", "/")
    if p == d or p.startswith(d + "/"):
        rest = p[len(d):].lstrip("/")
        return str(EXCHANGE_DIR / rest)
    return str(pattern)


def _glob_latest(pattern: str) -> Path | None:
    """glob 解析：精确路径存在即返回；通配取 mtime 最新（对齐 engine._resolve_artifact）。"""
    try:
        p = Path(_effective_glob(pattern))
        if p.exists():
            return p
        matches = [m for m in p.parent.glob(p.name) if m.is_file()]
        return max(matches, key=lambda m: m.stat().st_mtime) if matches else None
    except (OSError, ValueError):
        return None


def _glob_all(pattern: str) -> list[Path]:
    """glob 全量命中（§4 产物链：同 glob 多版本全部列出）。"""
    try:
        pattern = _effective_glob(pattern)
        p = Path(pattern)
        if not any(c in pattern for c in "*?["):
            return [p] if p.is_file() else []
        return sorted((m for m in p.parent.glob(p.name) if m.is_file()),
                      key=lambda m: m.name)
    except (OSError, ValueError):
        return []


def _query_card(instance: str, step_id: str) -> dict | None:
    """kanban 只读查卡（wweng: 键空间）；异常降级 None（不阻断仪表渲染）。"""
    try:
        return kanban_client.resolve_step_card(instance, step_id, key_prefix=KEY_PREFIX)
    except Exception:  # noqa: BLE001 — 查询异常降级（面板只读，不因 DB 缺席红屏）
        return None


def _query_block_info(card_id: str) -> dict | None:
    """kanban 只读查卡阻塞原因（§5 card_blocked 详情增补）。"""
    try:
        return kanban_client.get_block_info(card_id)
    except Exception:  # noqa: BLE001
        return None


def _kanban_cli(args: list[str], timeout: int = 120) -> tuple[int, str]:
    """hermes kanban CLI 通道（016 批次三 #10 的 subprocess 唯一豁免点）。

    仅 /reassign 调用；命令白名单由调用方构造（服务端拼装，客户端 input
    不进命令行）。返回 (returncode, 合并输出)。测试经 monkeypatch 替换本函数，
    不触真板。
    """
    try:
        r = subprocess.run(["hermes", "kanban", *args],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return 1, f"CLI 执行失败: {e}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def read_md_head(path: Path, limit: int = 400) -> tuple[str | None, str]:
    """读 md 头部：frontmatter title + 正文前 limit 字（token 纪律：禁止全文）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    title, body = None, text
    if m:
        tm = re.search(r"^\s*title\s*:\s*(.+)$", m.group(1), re.MULTILINE)
        if tm:
            title = tm.group(1).strip().strip("\"'")
        body = text[m.end():]
    return title, body.strip()[:limit]


def _list_instances() -> list[str]:
    """实例名列表（instances/*.json，按名排序，确定性渲染序）。

    20260921：排除 <name>.defaults.json（预设默认映射文件，glob *.json 会误收
    → 面板出现 instance=null 的幽灵条目，article-002.defaults 实证）。
    """
    if not INSTANCES_DIR.is_dir():
        return []
    return sorted(fp.stem for fp in INSTANCES_DIR.glob("*.json")
                  if not fp.name.endswith(".defaults.json"))


def _load_instance(name: str) -> dict | None:
    fp = INSTANCES_DIR / f"{name}.json"
    try:
        st = json.loads(fp.read_text(encoding="utf-8"))
        st.setdefault("history", [])
        return st
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# §1/§3 进度总览
# ---------------------------------------------------------------------------

def _progress(state: dict, tpl: dict) -> dict:
    """进度算法（spec §3 裁定口径）：
    当前 step ∈ seq19 → index+1（1-based，当前节点计入已过）；
    当前 step 是 done 步或 status=completed → 满格；不在 pipeline → 0。
    2026-09-26 大修：done 判定去 ww_done 硬编码（按模板内 type==done 步）。"""
    seq = _seq19(tpl)
    ids = [s["id"] for s in seq]
    step = state.get("step")
    is_done_step = any(s.get("type") == "done" and s.get("id") == step
                       for s in tpl.get("steps", []))
    if state.get("status") == "completed" or is_done_step:
        n = len(seq)
    elif step in ids:
        n = ids.index(step) + 1
    else:
        n = 0
    total = len(seq)
    return {"done_count": n, "total": total, "percent": round(n / total * 100) if total else 0}


def _history_reopen_counts(state: dict) -> dict[str, int]:
    """history 中每 step 的 dispatch_reopen 次数（§1 返工角标）。"""
    counts: dict[str, int] = {}
    for h in state.get("history", []):
        if h.get("action") == "dispatch_reopen" and h.get("step"):
            counts[h["step"]] = counts.get(h["step"], 0) + 1
    return counts


def _glob_latest_for(name: str, pattern: str) -> Path | None:
    """_glob_latest 的实例过滤版（20260921：拍板参考/组列/详情抽屉不得串项目）。

    取 _glob_all 全量后按 -<name> 边界过滤再取 mtime 最新；全局「最新」可能是
    别的实例的产物（article-003/004 并行期实证）。
    """
    files = [p for p in _glob_all(pattern) if _belongs_to_instance(p, name)]
    try:
        return max(files, key=lambda m: m.stat().st_mtime) if files else None
    except OSError:
        return None


def _step_artifact_files(step: dict, name: str = "") -> list[dict]:
    """该 step（group 步取成员）artifact 实存文件清单（015-P1 详情卡「成果」栏）。
    只取 name/size/mtime/path 元数据，不读文件内容（token 纪律）。
    20260921：按实例名过滤——详情抽屉曾把其他实例同步骤成果一并列出
    （article-003/004 并行期实证，user 报告）。"""
    globs: list[str] = []
    if step.get("artifact"):
        globs.append(str(step["artifact"]))
    for m in _group_member_steps(step):
        if m.get("artifact"):
            globs.append(str(m["artifact"]))
    out: list[dict] = []
    for g in globs:
        for fp in _glob_all(g):
            if name and not _belongs_to_instance(fp, name):
                continue
            try:
                st = fp.stat()
                out.append({"name": fp.name, "path": str(fp.resolve()),
                            "size": st.st_size,
                            "mtime": datetime.datetime.fromtimestamp(
                                st.st_mtime).strftime("%m-%d %H:%M")})
            except OSError:
                continue
    return out


def _step_detail(name: str, state: dict, tpl: dict, step_id: str) -> dict:
    """015-P1：单步详情卡数据（全部由既有 state JSON / 模板 / kanban 只读派生，
    不新增 LLM 调用、不新增重查询——kanban 沿用 build_overview 同一 resolve_step_card）。"""
    step_def = _step_map(tpl).get(step_id, {})
    skills, unbound = _skills_entries(tpl, step_id)
    # group 步的 skills_map 绑在成员上：成员条目平铺（标注成员步名）
    if unbound and step_def.get("type") == "dispatch_group":
        skills = []
        for m in _group_member_steps(step_def):
            entries, _u = _skills_entries(tpl, m["id"])
            skills.extend({**e, "step": m["id"]} for e in entries)
        unbound = not skills
    # 负责人：history 该步最新 dispatch/dispatch_reopen 的 log "assignee=x"，
    # 无则回退模板 skills_map owner（多 owner 用 / 连接）
    assignee = None
    for h in reversed(state.get("history", [])):
        if h.get("step") == step_id and h.get("action") in ("dispatch", "dispatch_reopen"):
            m = re.search(r"assignee=([\w.-]+)", str(h.get("log", "")))
            if m:
                assignee = m.group(1)
                break
    if not assignee:
        owners = [e["owner"] for e in skills if e.get("owner")]
        assignee = " / ".join(owners) if owners else None
    # 活跃板卡（id/status/title 作 summary）
    card = _query_card(name, step_id)
    card_brief = ({"id": card.get("id"), "status": card.get("status"),
                   "summary": card.get("title")} if card else None)
    # 打回/重试痕迹：verdict_reject / verdict_gate_reject / dispatch_reopen /
    # check_render 未 PASS（detail 非空且非 PASS 即违规清单）
    traces: list[dict] = []
    for h in state.get("history", []):
        if h.get("step") != step_id:
            continue
        action = h.get("action", "")
        detail = str(h.get("detail") or h.get("log") or "")
        if action in ("verdict_reject", "verdict_gate_reject", "dispatch_reopen"):
            traces.append({"action": action, "detail": detail})
        elif action == "check_render" and detail and detail.strip().upper() != "PASS":
            traces.append({"action": action, "detail": detail})
    return {
        "assignee": assignee,
        "skills": skills,
        "unbound": unbound,
        "card": card_brief,
        "artifacts": _step_artifact_files(step_def, name),
        "traces": traces,
    }


def _group_member_steps(step: dict) -> list[dict]:
    return [m for m in (step.get("members") or []) if isinstance(m, dict) and m.get("id")]


def _owners(step: dict) -> list[str]:
    """负责人：dispatch_group 列全体成员 assignee（去重保序），否则单 assignee。"""
    seen: list[str] = []

    def add(v):
        if v and v not in seen:
            seen.append(v)

    if step.get("type") == "dispatch_group":
        for m in _group_member_steps(step):
            add(m.get("assignee"))
    else:
        add(step.get("assignee"))
    return seen


def build_overview(name: str, state: dict, tpl: dict) -> dict:
    """§1 overview：阶段/负责人/进度条 19 格/活跃卡。"""
    smap = _step_map(tpl)
    seq = _seq19(tpl)
    step_id = state.get("step")
    step = smap.get(step_id, {})
    prog = _progress(state, tpl)
    reopens = _history_reopen_counts(state)
    stage_idx = _stage_of(step_id or "")
    nodes = []
    for i, s in enumerate(seq):
        nodes.append({
            "id": s["id"],
            "done": i < prog["done_count"],
            "current": i == prog["done_count"] - 1 and prog["done_count"] < prog["total"],
            "rework": reopens.get(s["id"], 0),
            # 015-P1：条块点击展开的详情数据（按需派生，零 LLM）
            **_step_detail(name, state, tpl, s["id"]),
        })
    # 活跃卡：单步查本步；group 步逐成员查（active_cards 仅 group 步非空）
    active_card = None
    active_cards: list[dict] = []
    if step.get("type") == "dispatch_group":
        for m in _group_member_steps(step):
            card = _query_card(name, m["id"])
            active_cards.append({"step": m["id"], "card": card})
            if card and active_card is None:
                active_card = card
    else:
        active_card = _query_card(name, step_id) if step_id else None
    return {
        "stage": STAGE_NAMES[stage_idx] if stage_idx >= 0 else "未知",
        "stage_idx": stage_idx,
        "stages": list(STAGE_NAMES),
        "step_assignee": step.get("assignee"),
        "owners": _owners(step) if step else [],
        "active_card": active_card,
        "active_cards": active_cards,
        "done_count": prog["done_count"],
        "total": prog["total"],
        "percent": prog["percent"],
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# §2 skills 仪表
# ---------------------------------------------------------------------------

def _skills_entries(tpl: dict, step_id: str) -> tuple[list[dict], bool]:
    """skills_map 直读：返回 (条目列表, unbound)。无绑定 → ([], True)。"""
    entries = (tpl.get("skills_map") or {}).get(step_id) or []
    out = []
    for e in entries:
        if isinstance(e, dict) and e.get("skill"):
            # 2026-09-27 吸引力批次：引擎源条目 owner 显示「引擎源」（与 skill 池渲染同口径）
            owner = "引擎源" if e.get("source") == "engine" else str(e.get("owner", ""))
            out.append({"skill": str(e["skill"]), "owner": owner})
    return out, not out


def _group_finished_members(name: str, state: dict, tpl: dict, group_step: dict) -> list[str]:
    """组内已完成成员（§2 done 栏补充）：history 有该组 dispatch_group 全 done 记录时，
    按 §6 列判定（产物实存或卡 done）逐成员回填——成员 step 名在 history 中不出现。"""
    gid = group_step["id"]
    finished = any(h.get("step") == gid and h.get("action") == "dispatch_group"
                   for h in state.get("history", []))
    if not finished:
        return []
    done = []
    for m in _group_member_steps(group_step):
        if _glob_latest_for(name, str(m.get("artifact", ""))) or (
                _query_card(name, m["id"]) or {}).get("status") == "done":
            done.append(m["id"])
    return done


def build_skills(name: str, state: dict, tpl: dict) -> dict:
    """§2 skills：done 栏（history dispatch_done 去重保序 + 完成组成员补列）+
    current 栏（group 步平铺所有成员 skill 条目，每条带成员 step 名）。"""
    smap = _step_map(tpl)
    done_steps: list[str] = []
    for h in state.get("history", []):
        sid = h.get("step")
        if h.get("action") == "dispatch_done" and sid and sid not in done_steps:
            done_steps.append(sid)
    # dispatch_group 的成员步：history 不记成员名 → 组全 done 时按 §6 判定补列
    for s in tpl.get("steps", []):
        if s.get("type") == "dispatch_group":
            for mid in _group_finished_members(name, state, tpl, s):
                if mid not in done_steps:
                    done_steps.append(mid)
    done = []
    for sid in done_steps:
        skills, unbound = _skills_entries(tpl, sid)
        done.append({"step": sid, "skills": skills, "unbound": unbound})

    cur_id = state.get("step")
    cur_step = smap.get(cur_id, {})
    cur_skills: list[dict] = []
    if cur_step.get("type") == "dispatch_group":
        for m in _group_member_steps(cur_step):
            entries, _unb = _skills_entries(tpl, m["id"])
            for e in entries:
                cur_skills.append({**e, "step": m["id"]})
        unbound = not cur_skills
    else:
        entries, unbound = _skills_entries(tpl, cur_id or "")
        for e in entries:
            cur_skills.append({**e, "step": cur_id})
    active_card = _query_card(name, cur_id) if cur_id and not cur_step.get("members") else None
    if cur_step.get("type") == "dispatch_group":
        for seg in build_overview(name, state, tpl)["active_cards"]:
            if seg["card"] and active_card is None:
                active_card = seg["card"]
    current = {"step": cur_id, "skills": cur_skills, "unbound": unbound,
               "active_card": active_card}
    return {"done": done, "current": current}


# ---------------------------------------------------------------------------
# §3 decisions 拍板时间线
# ---------------------------------------------------------------------------

def _decision_gate_labels(tpl: dict) -> dict[str, str]:
    """门序：pipeline 中 decision 步出现顺序 → 门1..门4。"""
    labels: dict[str, str] = {}
    n = 0
    for s in tpl.get("steps", []):
        if s.get("type") == "decision":
            n += 1
            labels[s["id"]] = f"门{n}"
    return labels


def _option_label(step: dict, decision: str) -> str:
    """按 decision 在 options[].key 上 strip().upper() 反查；查不到原样显示。"""
    d = str(decision).strip().upper()
    for opt in step.get("options") or []:
        key = str(opt.get("key", "")).strip().upper()
        if key and d == key:
            return opt.get("label", str(decision))
    return str(decision)


def build_decisions(name: str, state: dict, tpl: dict) -> dict:
    """§3：records（history 逐条）+ pending（当前 decision 步且无 .decision 文件）。"""
    smap = _step_map(tpl)
    gate_labels = _decision_gate_labels(tpl)
    hist = state.get("history", [])
    records = []
    for idx, h in enumerate(hist):
        if h.get("action") != "decision":
            continue
        sid = h.get("step", "")
        step = smap.get(sid, {})
        dec = h.get("decision")
        if h.get("source") == "auto_default":
            tag = "预设默认 ⚙"
        elif dec == "C" and h.get("note"):
            tag = "user 手选·带意见"
        else:
            tag = "user 手选"
        # 紧随其后的 dispatch_reopen 且该步 == 该门 rework_target/on_reject → 已返工
        reworked = False
        if idx + 1 < len(hist):
            nxt = hist[idx + 1]
            target = step.get("rework_target") or step.get("on_reject")
            if (nxt.get("action") == "dispatch_reopen" and target
                    and nxt.get("step") == target):
                reworked = True
        records.append({
            "gate": sid,
            "gate_label": gate_labels.get(sid, sid),
            "decision": dec,
            "option_label": _option_label(step, dec) if dec is not None else None,
            "tag": tag,
            "source": h.get("source"),
            "note": h.get("note"),
            "reworked": reworked,
        })

    pending = None
    cur = smap.get(state.get("step") or "", {})
    decision_file = INSTANCES_DIR / f"{name}.decision"
    if (cur.get("type") == "decision" and not decision_file.exists()
            and state.get("status") != "completed"):
        ctx = []
        for cf in cur.get("context_files") or []:
            fp = _glob_latest_for(name, str(cf))  # 20260921：参考产物限定本实例
            if fp:
                title, head = read_md_head(fp)
                ctx.append({"path": str(fp.resolve()), "name": fp.name,
                            "title": title, "head": head})
        pending = {
            "step": cur["id"],
            "gate_label": gate_labels.get(cur["id"], cur["id"]),
            "question": cur.get("question", ""),
            "options": [{"key": str(o.get("key", "")), "label": str(o.get("label", ""))}
                        for o in (cur.get("options") or [])],
            "context_files": ctx,
        }
    return {"records": records, "pending": pending}


# ---------------------------------------------------------------------------
# §4 artifacts 产物链（手风琴 + 抽屉）
# ---------------------------------------------------------------------------

def _artifact_group_name(step_id: str) -> str | None:
    """分组中文名固定映射；None = 该步不产组（屏障/check 的 glob 与成员重复）。
    2026-09-26 大修：非 ww_ 模板步骤按后缀关键词兜底分组。"""
    if step_id in _ARTIFACT_GROUP_FIXED:
        return _ARTIFACT_GROUP_FIXED[step_id]
    if step_id.startswith("ww_mat_"):
        return "素材"
    if "verdict" in step_id:
        return "双审/逻辑审"
    # 非 ww_ 模板兜底（6 新模板后缀语义化）
    for group, suffixes in (
            ("审查", ("_factcheck", "_logic", "_audit", "_review")),
            ("排版发布", ("_layout", "_upload", "_archive", "_cover")),
            ("正文", ("_write", "_draft", "_polish", "_chapter", "_dialogue")),
            ("选题大纲", ("_topic", "_outline", "_premise", "_structure",
                          "_logline", "_scenebreak", "_bible", "_data",
                          "_analysis", "_research", "_notes"))):
        if step_id.endswith(suffixes):
            return group
    return None


def _nnn(filename: str) -> int:
    r"""文件名开头 ^(\d{3})- 的 nnn 号；无则 -1（mtime 兜底排序）。"""
    m = re.match(r"^(\d{3})-", filename)
    return int(m.group(1)) if m else -1


def _sort_versions(files: list[Path]) -> list[dict]:
    """同 glob 多版本排序：nnn 降序；无 nnn 的按 mtime 兜底（nnn 记 -1）。"""
    items = []
    for fp in files:
        n = _nnn(fp.name)
        try:
            mt = fp.stat().st_mtime
        except OSError:
            mt = 0.0
        items.append({"name": fp.name, "path": str(fp.resolve()), "nnn": n, "_mt": mt})
    items.sort(key=lambda it: (it["nnn"], it["_mt"]), reverse=True)
    return items


def _belongs_to_instance(fp: Path, name: str) -> bool:
    """B1（20260920）：产物文件按实例名过滤的边界匹配谓词。

    产物命名契约 NNN-owner-slug-<instance>.md（exwrite 层强制稳定）→
    文件名含边界形式 -<name>. 或 -<name>- 即归属该实例；
    -article-003 不会误吸 -article-0033（后边界必须 . 或 -）。
    """
    n = fp.name
    return f"-{name}." in n or f"-{name}-" in n


def build_artifacts(name: str, state: dict, tpl: dict) -> list[dict]:
    """§4：遍历带 artifact 的 work 步（含 group 成员）解析实存文件；
    附加 排版HTML/封面/修改意见 三类特殊条目。条目只带 name/path（token 纪律）。

    B1（20260920）：全局 glob 混排多实例产物 → 全部结果先按文件名 -<name>
    边界过滤再入列（展示层过滤，模板 artifact glob 路径不动）；
    过滤后为空 → 该组不产条目（前端自然显示为空）。
    """
    out: list[dict] = []

    def push(group: str, step_id: str, kind: str, versions: list[dict], note_text=None):
        if not versions:
            return
        out.append({
            "group": group, "step": step_id, "kind": kind,
            "current": {k: versions[0][k] for k in ("name", "path", "nnn")},
            "history": [{"name": v["name"], "path": v["path"]} for v in versions[1:]],
            "note_text": note_text,
        })

    for step in tpl.get("steps", []):
        t = step.get("type")
        if t not in ("dispatch", "dispatch_group"):
            continue  # 屏障/门/check 的 glob 与工作步重复，不另产组
        gname = _artifact_group_name(step["id"])
        if t == "dispatch_group":
            for m in _group_member_steps(step):
                g = _artifact_group_name(m["id"])
                if g and m.get("artifact"):
                    files = [p for p in _glob_all(str(m["artifact"]))
                             if _belongs_to_instance(p, name)]
                    push(g, m["id"], "md", _sort_versions(files))
        elif gname and step.get("artifact"):
            files = [p for p in _glob_all(str(step["artifact"]))
                     if _belongs_to_instance(p, name)]
            push(gname, step["id"], "md", _sort_versions(files))

    # 附加①：排版HTML（*-formatted.html，[预览]/[打开源码]）——同口径按实例过滤
    htmls = _sort_versions([p for p in _glob_all(str(ARTIFACTS_DIR / "*-formatted.html"))
                            if _belongs_to_instance(p, name)])
    push("排版HTML", "ww_layout_html", "html", htmls)
    # 附加②：封面（cover-*.png，<img> 直链）——同口径按实例过滤
    covers = _sort_versions([p for p in _glob_all(str(ARTIFACTS_DIR / "cover-*.png"))
                             if _belongs_to_instance(p, name)])
    push("封面", "ww_cover", "img", covers)
    # 附加③：修改意见（history 中 decision=C 的 note 全文，非文件条目）
    for h in state.get("history", []):
        if h.get("action") == "decision" and h.get("decision") == "C" and h.get("note"):
            out.append({
                "group": "修改意见", "step": "ww_notes", "kind": "note",
                "current": None, "history": [], "note_text": str(h["note"]),
            })
    return out


# ---------------------------------------------------------------------------
# §5 warnings 阻塞警示窗
# ---------------------------------------------------------------------------

def _h_time(h: dict) -> str:
    """history 时间戳字段；无则 '(未知)'。"""
    for k in ("time", "ts", "timestamp", "at", "created_at"):
        if h.get(k):
            return str(h[k])
    return "(未知)"


def _step_artifact_glob(step: dict) -> str:
    """该 step 的 artifact glob（group 步取首个成员）；无则 '(无)'。"""
    if step.get("artifact"):
        return str(step["artifact"])
    for m in _group_member_steps(step):
        if m.get("artifact"):
            return str(m["artifact"])
    arts = step.get("artifacts") or []
    return str(arts[0]) if arts else "(无)"


def build_warnings(name: str, state: dict, tpl: dict) -> list[dict]:
    """§5 三类机械判定：escalate 历史条 / 当前步卡 blocked / 同步连续 wait≥5。"""
    smap = _step_map(tpl)
    hist = state.get("history", [])
    warns: list[dict] = []

    # 1) history 中的 escalate 条目
    for h in hist:
        action = h.get("action")
        if action not in ("verdict_escalate", "wait_escalate", "check_escalate"):
            continue
        sid = h.get("step", "")
        step = smap.get(sid, {})
        detail = h.get("detail") or h.get("log") or ""
        w = {
            "step": sid, "action": action,
            "detail": str(detail), "log": str(h.get("log", "")),
            "suggestions": list(SUGGESTIONS_TPL.get(action, [])),
            "prompt": PROMPT_TPL.format(
                instance=name, step=sid, action=action, time=_h_time(h),
                detail=str(detail), artifact_glob=_step_artifact_glob(step)),
            "card_status": None,
        }
        warns.append(w)

    # 2) 当前步（或任一成员）kanban 卡 blocked
    cur_id = state.get("step")
    cur = smap.get(cur_id or "", {})
    if cur.get("type") in ("dispatch", "dispatch_group"):
        probe_ids = ([m["id"] for m in _group_member_steps(cur)]
                     if cur.get("type") == "dispatch_group" else [cur_id])
        for pid in probe_ids:
            card = _query_card(name, pid)
            if card and card.get("status") == "blocked":
                bi = _query_block_info(card.get("id", ""))
                reason = f"（{bi['reason']}）" if bi and bi.get("reason") else ""
                detail = f"kanban 卡 {card.get('id')} 状态 blocked{reason}"
                warns.append({
                    "step": pid, "action": "card_blocked",
                    "detail": detail, "log": detail,
                    "suggestions": list(SUGGESTIONS_TPL["card_blocked"]),
                    "prompt": PROMPT_TPL.format(
                        instance=name, step=pid, action="card_blocked",
                        time=_h_time({}), detail=detail,
                        artifact_glob=_step_artifact_glob(smap.get(pid, cur))),
                    "card_status": "blocked",
                })

    # 3) 同一 step 连续 dispatch_wait ≥5（history 尾部连续段）
    #    2026-09-18 修复（卡 t_9261a467）：被等卡 status=blocked 时 dispatch_wait 是
    #    引擎对「在途卡」的合法空转（engine.py dispatch 分支幂等跳过留本步等 done），
    #    此时 wait_stall 与 card_blocked 是同根双报 → 抑制，把连续等待计数降级为
    #    card_blocked 条目的附注字段 wait_stall_note。仅当被等卡全部确认 blocked 才
    #    抑制；查询降级 None / 任一卡在途（running/ready/todo）→ 照常报真 stall。
    tail: list[dict] = []
    for h in reversed(hist):
        if h.get("action") == "dispatch_wait" and h.get("step"):
            tail.append(h)
        else:
            break
    if len(tail) >= 5:
        sid = tail[-1].get("step", "")
        last = tail[-1]
        wait_note = f"连续 dispatch_wait {len(tail)} 次"
        step_def = smap.get(sid, {})
        probe_ids = ([m["id"] for m in _group_member_steps(step_def)]
                     if step_def.get("type") == "dispatch_group" else [sid])
        statuses = [(card or {}).get("status")
                    for card in (_query_card(name, pid) for pid in probe_ids)]
        all_blocked = bool(statuses) and all(s == "blocked" for s in statuses)
        if all_blocked:
            # 附注注入本函数早先产出的 card_blocked 条目（同 step 优先）。
            # 2026-09-18 P4（卡 t_968935f1）：抑制与附注必须原子绑定——当
            # state["step"] 与 history 尾部 dispatch_wait 的 step 不一致（人工改
            # state / 委派旁路窗等）时，判定2 没产 card_blocked 条目可注入，
            # 旧写法 note 丢弃且不产 wait_stall → warnings=[] 静默（宁报勿漏破口）。
            # 修法：全部注入成功才抑制；有落点缺失 → 回落产 wait_stall。
            injected = False
            for w in warns:
                if w["action"] == "card_blocked" and w["step"] in probe_ids:
                    w["wait_stall_note"] = wait_note
                    injected = True
            if not injected:
                detail = f"{wait_note}（{sid}，被等卡全 blocked 但 card_blocked 无落点：当前步非该 dispatch 步）"
                warns.append({
                    "step": sid, "action": "wait_stall",
                    "detail": detail, "log": str(last.get("log", "")),
                    "suggestions": list(SUGGESTIONS_TPL["wait_stall"]),
                    "prompt": PROMPT_TPL.format(
                        instance=name, step=sid, action="wait_stall", time=_h_time(last),
                        detail=detail, artifact_glob=_step_artifact_glob(step_def)),
                    "card_status": None,
                })
        else:
            detail = f"{wait_note}（{sid}，尾部心跳空转）"
            warns.append({
                "step": sid, "action": "wait_stall",
                "detail": detail, "log": str(last.get("log", "")),
                "suggestions": list(SUGGESTIONS_TPL["wait_stall"]),
                "prompt": PROMPT_TPL.format(
                    instance=name, step=sid, action="wait_stall", time=_h_time(last),
                    detail=detail, artifact_glob=_step_artifact_glob(step_def)),
                "card_status": None,
            })

    # 无人值守通道：.unattended 存在且警示非空 → prompts 落 reports（内容变化才写）
    if warns and (INSTANCES_DIR / f"{name}.unattended").exists():
        _write_unattended_alerts(name, [w["prompt"] for w in warns])
    return warns


def _write_unattended_alerts(instance: str, prompts: list[str]) -> None:
    """写 exchange/reports/unattended-alerts-<instance>.md（唯一允许的 reports 写点；
    prompt 集合未变则不重写，避免 mtime 抖动——ETag 输入不含 reports，无自触发循环）。"""
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        fp = REPORTS_DIR / f"unattended-alerts-{instance}.md"
        body_prompts = "\n\n".join(
            f"## 排查 prompt {i + 1}\n\n{p}" for i, p in enumerate(prompts))
        if fp.exists():
            try:
                if all(p in fp.read_text(encoding="utf-8") for p in prompts):
                    return
            except OSError:
                pass
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        fp.write_text(
            f"# 无人值守排查通道 {instance}\n\n生成时间: {ts}\n\n{body_prompts}\n",
            encoding="utf-8")
    except OSError:
        pass  # 通道写失败不阻断仪表（只读优先）


# ---------------------------------------------------------------------------
# §6 groups 并行支路视图
# ---------------------------------------------------------------------------

_CARD_PERCENT = {"done": 100, "running": 60, "ready": 20, "todo": 20, "blocked": 60}


def _group_pending_members(state: dict, group_id: str) -> set[str]:
    """该组最新 dispatch_group_wait 记录的 pending 成员集。"""
    pending: set[str] = set()
    for h in state.get("history", []):
        if h.get("step") == group_id and h.get("action") == "dispatch_group_wait":
            pending = set(h.get("pending") or [])
    return pending


def build_groups(name: str, state: dict, tpl: dict) -> list[dict]:
    """§6：dispatch_group 步并列列视图（渲染条件：当前步在组/history 有组记录/组卡存在）。"""
    smap = _step_map(tpl)
    cur_id = state.get("step")
    out: list[dict] = []
    for step in tpl.get("steps", []):
        if step.get("type") != "dispatch_group":
            continue
        gid = step["id"]
        members = _group_member_steps(step)
        member_ids = [m["id"] for m in members]
        has_hist = any(h.get("step") == gid for h in state.get("history", []))
        cards_exist = any(_query_card(name, mid) for mid in member_ids)
        if not (cur_id == gid or has_hist or cards_exist):
            continue
        finished = any(h.get("step") == gid and h.get("action") == "dispatch_group"
                       for h in state.get("history", []))
        pending = _group_pending_members(state, gid)
        columns = []
        for m in members:
            mid = m["id"]
            card = _query_card(name, mid)
            card_status = card.get("status") if card else None
            # 20260921：实例过滤——他实例的同 slug 产物不得计入本实例进度/verdict 链接
            artifact_hit = _glob_latest_for(name, str(m.get("artifact", "")))
            if artifact_hit:
                percent = 100
            elif mid in pending and not finished:
                # 组未 finished 才按 pending 记录罚分——组已完成时，最后一拍
                # wait 记录的 pending 是陈旧快照；且遗留实例产物早于命名契约
                # （无实例后缀）本就查不到归属，不该罚 0（ww-m4-drill 实证）
                percent = _CARD_PERCENT.get(card_status or "", 0)
            else:
                percent = 100
            skills, unbound = _skills_entries(tpl, mid)
            verdict_path = None
            if artifact_hit:
                try:
                    rp = artifact_hit.resolve()
                    if rp.is_relative_to(VERDICTS_DIR.resolve()):
                        verdict_path = str(rp)
                except (OSError, ValueError):
                    verdict_path = None
            flow = FLOW_NAMES.get(mid)
            if not flow:
                slug = str(m.get("slug", ""))
                first = (m.get("description") or slug or mid).strip().split()[0] if \
                    (m.get("description") or slug or mid).strip() else mid
                flow = first
            columns.append({
                "member": mid, "flow": flow, "assignee": m.get("assignee"),
                "percent": percent, "skills": skills, "unbound": unbound,
                "card_status": card_status, "verdict_path": verdict_path,
            })
        done_cnt = sum(1 for c in columns if c["percent"] == 100)
        title = f"{(smap.get(gid) or {}).get('description', gid).split('：')[0]}"
        out.append({
            "step": gid, "title": title, "done": done_cnt, "total": len(columns),
            "finished": finished, "columns": columns,
        })
    return out


# ---------------------------------------------------------------------------
# 项目库视图（成果区两层目录，066 §8）
# ---------------------------------------------------------------------------

def _project_title(meta_path: Path) -> str:
    """meta.yaml 只读抽 title/final_title（正则单行，不引 YAML 全量解析）。"""
    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")[:3000]
    except OSError:
        return ""
    for field in ("final_title", "title"):
        m = re.search(rf"^\s*{field}\s*:\s*(.+)$", text, re.MULTILINE)
        if m:
            v = m.group(1).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]  # 成对引号整体剥壳（先剥壳再反转义，防吃掉内部引号）
            return v.replace('\\"', '"').replace("''", "'")
    return ""


def _file_kind(fp: Path) -> str:
    ext = fp.suffix.lower()
    if ext in (".md", ".markdown"):
        return "md"
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".png", ".jpg", ".jpeg"):
        return "img"
    if ext in (".yaml", ".yml", ".json"):
        return "meta"
    return "file"


def _project_instance(meta_path: Path) -> str:
    """meta.yaml 只读抽 instance（20260921：项目库按当前实例过滤的数据源）。"""
    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")[:3000]
    except OSError:
        return ""
    m = re.search(r"^\s*instance\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return ""
    v = m.group(1)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def build_projects() -> list[dict]:
    """扫成果区项目（2026-09-26 三层布局 `<模板名>/<日期>/<NN-slug>` +
    兼容旧两层 `<日期>/<NN-slug>`；meta.yaml 存在才算项目）。"""
    projects: list[dict] = []
    dirs: list[Path] = []
    try:
        for pat in ("*/*", "*/*/*"):
            dirs += [p for p in WECHAT_PROJECT_DIR.glob(pat) if p.is_dir()]
    except OSError:
        return []
    seen: set[Path] = set()
    for d in sorted(dirs):
        if d in seen:
            continue
        seen.add(d)
        meta = d / "meta.yaml"
        if not meta.exists():
            continue
        name = d.name  # 形如 01-jiagecike
        m = re.match(r"^(\d+)-(.+)$", name)
        seq, slug = (m.group(1), m.group(2)) if m else ("", name)
        files = []
        try:
            for fp in sorted(d.iterdir()):
                if fp.is_file():
                    files.append({"name": fp.name, "path": str(fp.resolve()),
                                  "kind": _file_kind(fp)})
        except OSError:
            pass
        # date=父目录名（日期层）；template=祖父目录名（三层时），旧两层为空
        parent_name = d.parent.name
        is_date_layer = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", parent_name))
        tpl = d.parent.parent.name if (is_date_layer and d.parent.parent != WECHAT_PROJECT_DIR) else ""
        projects.append({
            "path": str(d.resolve()), "date": parent_name if is_date_layer else "",
            "template": tpl, "seq": seq, "slug": slug,
            "title": _project_title(meta) or slug, "files": files,
            "instance": _project_instance(meta),
        })
    return projects


def find_archived_project(instance: str) -> dict | None:
    """066 §8 面板侧③：两层 glob `Wechat-project/*/*/meta.yaml` 反查该实例成果区项目。

    命中即返回项目目录 + 可打开件（优先 formatted.html，回退 meta.yaml）——
    已归档项目的产物链由面板切到成果区路径（不依赖 exchange 中间产物）。
    """
    metas: list = []
    try:
        for pat in ("*/*/meta.yaml", "*/*/*/meta.yaml"):
            metas += sorted(WECHAT_PROJECT_DIR.glob(pat))
    except OSError:
        return None
    for meta in metas:
        try:
            text = meta.read_text(encoding="utf-8", errors="replace")[:3000]
        except OSError:
            continue
        m = re.search(r"^\s*instance\s*:\s*(.+?)\s*$", text, re.MULTILINE)
        if not m:
            continue
        v = m.group(1)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if v != instance:
            continue
        d = meta.parent
        html = d / "formatted.html"
        target = html if html.exists() else meta
        return {"dir": str(d.resolve()), "name": d.name, "date": d.parent.name,
                "open": str(target.resolve()), "open_name": target.name,
                "has_html": html.exists()}
    return None


# ---------------------------------------------------------------------------
# /api/state 组装与 ETag
# ---------------------------------------------------------------------------

def build_instance_view(name: str, tpl: dict | None = None) -> dict | None:
    """§4 instance_view 全量组装（只读引擎文件 + kanban 只读查询）。

    2026-09-26 大修：tpl=None → 按实例 state.template 解析各自模板
    （显式传入仍优先，兼容既有测试调用）。"""
    state = _load_instance(name)
    if state is None:
        return None
    if tpl is None:
        tpl = _template_for_state(state)
    try:
        mtime = int((INSTANCES_DIR / f"{name}.json").stat().st_mtime)
    except OSError:
        mtime = 0
    return {
        "name": name,
        "status": state.get("status"),
        "step": state.get("step"),
        "iteration": state.get("iteration"),
        "mode": state.get("mode"),
        "template": state.get("template") or template_loader.DEFAULT_TEMPLATE_ID,
        "mtime": mtime,  # 015-P2：同状态按 mtime 新的在前
        "unattended": (INSTANCES_DIR / f"{name}.unattended").exists(),
        "defaults_enabled": (INSTANCES_DIR / f"{name}.defaults.json").exists(),
        "overview": build_overview(name, state, tpl),
        "skills": build_skills(name, state, tpl),
        "decisions": build_decisions(name, state, tpl),
        "artifacts": build_artifacts(name, state, tpl),
        "warnings": build_warnings(name, state, tpl),
        "groups": build_groups(name, state, tpl),
        "archived_project": find_archived_project(name),
    }


def build_state() -> dict:
    """§4 /api/state 顶层组装（generated_at/pipeline_total/stages/instances/projects）。

    2026-09-26 大修：实例视图各用各的绑定模板（不再默认模板一刀切）；
    pipeline_total/stages 保留默认模板口径（顶层字段，前端兼容）。"""
    tpl = load_pipeline()
    instances = []
    for name in _list_instances():
        iv = build_instance_view(name)   # tpl=None → 按实例 state.template 解析
        if iv is not None:
            instances.append(iv)
    return {
        "generated_at": int(time.time()),
        "pipeline_total": len(_seq19(tpl)),
        "stages": list(STAGE_NAMES),
        "instances": instances,
        "projects": build_projects(),
    }


def compute_etag() -> str:
    """ETag = W/"<sha1>"；输入 = 影响 state 的文件 (路径, mtime_ns, size) 集合。
    不含 exchange/reports（无人值守通道自写自读会形成自触发循环）。"""
    items: list[tuple[str, int, int]] = []

    def add(p: Path):
        try:
            st = p.stat()
            items.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            items.append((str(p), 0, 0))

    add(PIPELINE_PATH)
    # 大修：etag 覆盖全部模板文件（任一模板变更 → 面板视图刷新）
    try:
        for tp in sorted(TEMPLATES_DIR.glob("*.yaml")):
            add(tp)
    except OSError:
        pass
    if INSTANCES_DIR.is_dir():
        seen = set()
        for pat in ("*.json", "*.decision", "*.defaults.json", "*.unattended"):
            for p in INSTANCES_DIR.glob(pat):
                if p not in seen:
                    seen.add(p)
                    add(p)
    try:
        add(kanban_client.resolve_db_path())
    except Exception:  # noqa: BLE001 — DB 路径解析失败不致 500
        pass
    for d in (ARTIFACTS_DIR, VERDICTS_DIR):
        try:
            st = d.stat()
            items.append((str(d), st.st_mtime_ns, len(list(d.iterdir()))))
        except OSError:
            items.append((str(d), 0, 0))
    digest = hashlib.sha1(repr(sorted(items)).encode("utf-8")).hexdigest()
    return f'W/"{digest}"'

# ---------------------------------------------------------------------------
# 内嵌前端（单文件、无外部 CDN；深色仪表盘，中文标签；DOM 契约选择器见 066/spec §6）
# ---------------------------------------------------------------------------

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>we-write 写作面板</title>
<style>
:root { --bg:#0e1116; --card:#171b22; --line:#232936; --fg:#d7dce3; --muted:#8a93a3;
  --accent:#4f8cff; --ok:#3f8f5f; --warn:#d8a63f; --bad:#e06c5f; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.6 "Microsoft YaHei", "PingFang SC", sans-serif; }
header.top { display:flex; align-items:baseline; gap:16px; padding:14px 20px;
  border-bottom:1px solid var(--line); }
header.top h1 { font-size:18px; margin:0; }
#refresh-at { color:var(--muted); font-size:12px; }
main { padding:0 20px 60px; max-width:1280px; margin:0 auto; }
.muted { color:var(--muted); }
h2 { font-size:16px; }
/* 实例卡 */
.instance-card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px; margin:16px 0; }
.card-head { display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; }
.card-title { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.iname { font-size:16px; font-weight:600; color:#fff; }
.badge { padding:1px 10px; border-radius:10px; font-size:12px; background:#2b3550; }
.badge.completed { background:#24513a; color:#9fe0bd; }
.badge.running, .badge.idle { background:#2b3550; }
.switches { display:flex; gap:14px; font-size:13px; color:var(--muted); }
.switches label { display:inline-flex; align-items:center; gap:4px; cursor:pointer; }
.sec { border-top:1px solid var(--line); margin-top:12px; padding-top:10px; }
.sec h4 { margin:2px 0 8px; font-size:13px; color:var(--muted); }
/* §1 总览 */
.lanes { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-bottom:8px; }
.stage-lane { padding:2px 12px; border-radius:12px; background:#202634; font-size:12px; }
.stage-lane.current { background:#2d4d8f; color:#fff; }
.lane-arrow { color:var(--muted); font-size:12px; }
.progress-row { display:flex; align-items:center; gap:10px; }
.progress-bar { display:flex; gap:2px; flex:1; min-width:280px; }
.seg { flex:1; height:14px; border-radius:2px; background:#2a3140; position:relative; }
.seg.done { background:var(--ok); }
.seg.current { background:var(--warn); animation:pulse 1.6s ease-in-out infinite; }
.seg.rework::after { content:"↺"; position:absolute; top:-7px; right:-2px;
  color:#e0b34c; font-size:9px; }
@keyframes pulse { 0%,100% { opacity:.5; } 50% { opacity:1; } }
.progress-text { font-size:13px; white-space:nowrap; }
.owners { margin-top:6px; font-size:13px; }
.active-card { color:var(--muted); font-size:12px; margin-top:2px; }
/* §2 skills */
.skills-cols { display:flex; gap:20px; flex-wrap:wrap; }
.skills-done, .skills-current { flex:1; min-width:280px; }
.skill-step { padding:3px 0; border-bottom:1px dashed #202634; font-size:13px; }
.skill-step .skill-name { color:#cfe0ff; }
.skill-step .skill-owner { color:var(--muted); margin-left:6px; font-size:12px; }
/* §3 拍板 */
.decision-record { padding:4px 0; display:flex; gap:10px; flex-wrap:wrap;
  align-items:baseline; font-size:13px; border-bottom:1px dashed #202634; }
.gate-label { color:var(--warn); min-width:34px; }
.decision-tag { font-size:12px; background:#2b3550; padding:0 8px; border-radius:8px; }
.rework-flag { width:100%; color:var(--muted); font-size:12px; }
.decision-note { width:100%; color:#e8d7a8; font-size:12px; cursor:pointer; }
.decision-note.collapsed { white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  max-width:100%; }
.decision-note.expanded { white-space:pre-wrap; }
.pending-decision { background:#43371a; border:1px solid #8a6d2f; border-radius:8px;
  padding:10px 12px; margin:8px 0; }
.pending-question { color:#ffe9b0; font-weight:600; }
.pending-options { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
button { background:#243048; color:var(--fg); border:1px solid #33456b; border-radius:6px;
  padding:4px 10px; cursor:pointer; font-size:13px; }
button:hover { background:#2d3c5c; }
.note-row { margin-top:8px; }
input[type=text] { background:#12161d; color:var(--fg); border:1px solid var(--line);
  border-radius:6px; padding:5px 8px; width:100%; max-width:520px; }
.ctx-files { margin-top:8px; }
.ctx-file { margin-top:4px; }
.ctx-file pre { background:#12161d; padding:8px; border-radius:6px; white-space:pre-wrap;
  max-height:160px; overflow:auto; font-size:12px; color:#aeb8c8; }
/* §4 产物 */
details.artifact-group { margin:4px 0; }
details.artifact-group > summary { cursor:pointer; padding:4px 0; font-size:13px; }
.artifact-current { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
  font-size:13px; padding:2px 0 2px 12px; }
.artifact-name { color:#cfe0ff; word-break:break-all; }
.artifact-history { margin-left:12px; }
.artifact-history-item { cursor:pointer; color:var(--muted); font-size:12px;
  padding:2px 0; }
.artifact-history-item:hover { color:var(--fg); }
.artifact-note { padding:6px 12px; color:#e8d7a8; font-size:13px; white-space:pre-wrap; }
img.artifact-img { max-width:340px; border-radius:6px; margin:6px 0 6px 12px; display:block; }
/* §5 警示 */
.warning-window { background:#3a1d1f; border:1px solid #7a2e33; border-radius:8px;
  padding:10px 12px; margin-bottom:10px; }
.warning { padding:6px 0; border-bottom:1px dashed #5c2b30; font-size:13px; }
.warning.collapsed { cursor:pointer; color:var(--muted); }
.warning-detail { color:#ffb3a6; }
.warning-suggestions { margin:6px 0 6px 20px; padding:0; font-size:12px; color:#e8c8c2; }
.warning-suggestions li { margin:2px 0; }
.warning-actions { display:flex; gap:8px; }
button.hermes-probe { background:#4a3a12; border-color:#8a6d2f; color:#ffe9b0; }
button.warning-ack { background:#243048; }
/* §6 并行组 */
.parallel-group { margin-top:10px; }
.group-header { font-size:13px; color:#cfe0ff; margin-bottom:6px; }
.group-header .done-flag { color:var(--ok); }
.group-header .count { color:var(--muted); margin-left:8px; }
.group-cols { display:flex; gap:8px; flex-wrap:wrap; }
/* §8 成果区回指（已归档项目产物链切到成果区） */
.archive-link { margin-left:8px; padding:2px 8px; border:1px solid var(--line);
  border-radius:10px; background:#20303f; color:#9fd6c0; font-size:11px; cursor:pointer; }
.archive-link:hover { border-color:var(--ok); }
.group-col { flex:1; min-width:150px; background:#1d222c; border:1px solid var(--line);
  border-radius:8px; padding:8px 10px; font-size:12px; }
.col-member { color:#fff; font-weight:600; margin-bottom:4px; }
.col-flow, .col-owner, .col-percent, .col-skill, .col-card-status { padding:1px 0; }
.col-skill { color:#cfe0ff; word-break:break-all; }
.col-card-status { color:var(--muted); }
/* 项目库 */
.project-card { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:8px 12px; margin:8px 0; }
.file-link { cursor:pointer; padding:2px 0; font-size:13px; color:#cfe0ff;
  word-break:break-all; }
.file-link:hover { text-decoration:underline; }
/* 015-P2 实例分页 tab（running 绿 / paused 黄 / completed 灰） */
#instance-tabs { display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 4px; }
.tab { display:inline-flex; align-items:center; gap:8px; padding:5px 14px;
  border:1px solid var(--line); border-radius:8px; background:var(--card);
  cursor:pointer; font-size:13px; color:var(--muted); }
.tab:hover { border-color:var(--accent); }
.tab.active { color:#fff; border-color:var(--accent); background:#1c2740; }
.tab .dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
.tab .dot.running { background:var(--ok); }
.tab .dot.idle { background:var(--accent); }
.tab .dot.paused { background:var(--warn); }
.tab .dot.failed { background:var(--bad); }
.tab .dot.completed { background:#6b7382; }
.tab .iter { color:var(--muted); font-size:11px; }
/* 015-P1 进度条条块点击展开详情卡 */
.seg { cursor:pointer; }
.seg:hover { filter:brightness(1.2); }
.seg.expanded { outline:2px solid var(--accent); outline-offset:1px; }
.step-detail { background:#1d222c; border:1px solid var(--line); border-radius:8px;
  padding:10px 14px; margin:8px 0 2px; font-size:13px; }
.step-detail .sd-title { color:#cfe0ff; font-weight:600; margin-bottom:4px; }
.step-detail .sd-row { margin:2px 0; word-break:break-all; }
.step-detail .sd-label { color:var(--muted); margin-right:6px; }
.step-detail .sd-trace { color:#ffb3a6; font-size:12px; }
.step-detail .sd-file { cursor:pointer; color:#cfe0ff; }
.step-detail .sd-file:hover { text-decoration:underline; }
/* 抽屉 / toast */
#drawer-mask { position:fixed; inset:0; background:rgba(0,0,0,.55); display:none; }
#drawer-mask.show { display:block; }
#drawer { position:fixed; top:0; right:0; bottom:0; width:min(760px, 92vw);
  background:#12161d; border-left:1px solid var(--line); transform:translateX(100%);
  transition:transform .18s ease; display:flex; flex-direction:column; z-index:20; }
#drawer.open { transform:translateX(0); }
.drawer-head { display:flex; align-items:center; gap:10px; padding:10px 14px;
  border-bottom:1px solid var(--line); }
#drawer-title { font-size:13px; color:var(--muted); word-break:break-all; }
.drawer-content { padding:14px; overflow:auto; flex:1; }
.drawer-content pre { white-space:pre-wrap; word-break:break-all; font-size:12px;
  color:#c6cedb; margin:0; }
.drawer-frame { width:100%; height:100%; min-height:560px; border:0; background:#fff; }
.drawer-img { max-width:100%; }
#toast { position:fixed; left:50%; bottom:26px; transform:translateX(-50%);
  background:#2f6f4f; color:#eafff2; padding:8px 18px; border-radius:8px; font-size:13px;
  display:none; z-index:30; }
#toast.show { display:block; }
</style>
</head>
<body>
<header class="top"><h1>we-write 写作流水线面板</h1><span id="refresh-at"></span></header>
<main id="app"><div class="muted">加载中…</div></main>
<div id="drawer-mask"></div>
<aside id="drawer">
  <div class="drawer-head"><button id="drawer-close">关闭</button><span id="drawer-title"></span></div>
  <div class="drawer-content" id="drawer-content"></div>
</aside>
<div id="toast"></div>
<script>
"use strict";
var etag = null;
var STATE = null;
/* 015-P2：当前选中的实例 tab（null=默认取排序第一个，即「当前项目」）*/
var selectedInstance = null;
/* 015-P1：每实例展开中的进度条条块（instance → step_id | null）*/
var expandedStep = {};

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
  });
}
function toast(msg) {
  var t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  setTimeout(function () { t.classList.remove("show"); }, 2600);
}

/* ---- 轮询：10s fetch /api/state 带 If-None-Match，304 跳过重绘 ---- */
function poll(force) {
  var headers = {};
  if (etag && !force) headers["If-None-Match"] = etag;
  fetch("/api/state", {headers: headers}).then(function (r) {
    if (r.status === 304) return null;
    if (!r.ok) return null;
    etag = r.headers.get("ETag");
    return r.json();
  }).then(function (st) {
    if (!st) return;
    STATE = st;
    render();
  }).catch(function () { /* 网络抖动静默，下轮再试 */ });
}
setInterval(function () { poll(false); }, 10000);

/* ---- 渲染 ---- */
function render() {
  if (!STATE) return;
  document.getElementById("refresh-at").textContent =
    "刷新于 " + new Date(STATE.generated_at * 1000).toLocaleTimeString();
  /* 015-P2：顶部实例分页 tab + 只渲染选中实例一页（默认=排序第一个）*/
  var sel = currentInstance();
  if (sel) selectedInstance = sel.name;
  var html = instanceTabsHtml() +
    (sel ? instanceHtml(sel) : '<div class="muted">（无实例）</div>') +
    projectsHtml(STATE.projects, sel ? sel.name : null);
  document.getElementById("app").innerHTML = html;
}

/* 015-P2：可见实例 = status∈{running,idle,paused,failed,completed}，排除 t-* 测试与 .archive；
   排序 running → idle → paused/failed → completed，同状态 mtime 新的在前（当前项目永远第一个 tab）。
   20260921 修复：idle（API 新建未首拍的实例）原先被过滤 → 新建项目在面板「隐身」，
   user 以为没建成（article-4 实证）；idle/failed 必须可见。*/
function visibleInstances() {
  var order = {running: 0, idle: 1, paused: 2, failed: 2, completed: 3};
  return (STATE.instances || []).filter(function (iv) {
    if (/^t-/.test(iv.name) || iv.name.indexOf(".archive") >= 0) return false;
    return order[iv.status] != null;
  }).sort(function (a, b) {
    var oa = order[a.status], ob = order[b.status];
    if (oa !== ob) return oa - ob;
    return (b.mtime || 0) - (a.mtime || 0);
  });
}
function currentInstance() {
  var vis = visibleInstances();
  for (var i = 0; i < vis.length; i++) {
    if (vis[i].name === selectedInstance) return vis[i];
  }
  return vis[0] || null;
}
function instanceTabsHtml() {
  var vis = visibleInstances();
  if (!vis.length) return "";
  var sel = currentInstance();
  var out = '<div id="instance-tabs">';
  vis.forEach(function (iv) {
    out += '<span class="tab' + (sel && iv.name === sel.name ? " active" : "") +
      '" data-act="tab" data-instance="' + esc(iv.name) + '">' +
      '<span class="dot ' + esc(iv.status) + '"></span>' + esc(iv.name) +
      '<span class="iter">迭代 ' + esc(iv.iteration) + "</span></span>";
  });
  return out + "</div>";
}

function projectsHtml(projects, selName) {
  var out = '<section class="projects-sec"><h2>项目库（成果区）</h2>';
  /* 20260921：只显示当前选中实例的成果目录（meta.yaml instance 反查绑定；
     user 裁定「项目库应显示本项目的成果」，不再全库混排）。 */
  var mine = (projects || []).filter(function (p) {
    return selName && (p.instance || "") === selName;
  });
  if (!mine.length)
    return out + '<div class="muted">（本项目暂无成果目录——立项时自动预建，收官拍板后落成品）</div></section>';
  mine.forEach(function (p) {
    out += '<details class="project-card"' + (p.instance ? ' data-instance="' + esc(p.instance) + '"' : "") +
      '><summary>' + esc(p.date) + " " +
      esc(p.seq) + "-" + esc(p.slug) + " — " + esc(p.title) + "</summary>" +
      '<div class="project-files">' +
      (p.files || []).map(function (f) {
        return '<div class="file-link" data-act="open" data-path="' + esc(f.path) +
          '">' + esc(f.name) + ' <span class="muted">(' + esc(f.kind) + ")</span></div>";
      }).join("") + "</div></details>";
  });
  return out + "</section>";
}

function instanceHtml(iv) {
  return '<section class="instance-card" data-instance="' + esc(iv.name) + '">' +
    warningsHtml(iv) + cardHeadHtml(iv) + overviewHtml(iv) + skillsHtml(iv) +
    decisionsHtml(iv) + artifactsHtml(iv) + groupsHtml(iv) + "</section>";
}

function cardHeadHtml(iv) {
  var st = iv.status || "";
  var arc = "";
  if (iv.archived_project) {
    arc = '<button class="archive-link" data-act="open" data-path="' +
      esc(iv.archived_project.open) + '" title="' + esc(iv.archived_project.dir) +
      '">成果区 ' + esc(iv.archived_project.name) + ' ↗</button>';
  }
  return '<header class="card-head"><div class="card-title">' +
    '<span class="iname">' + esc(iv.name) + "</span>" +
    '<span class="badge ' + esc(st) + '">' + esc(st) + "</span>" +
    '<span class="muted">' + esc(iv.step || "-") + " · 迭代 " + esc(iv.iteration) +
    " · " + esc(iv.mode || "") + "</span>" + arc + "</div>" +
    '<div class="switches">' +
    '<label><input type="checkbox" class="unattended-switch" data-instance="' + esc(iv.name) +
    '"' + (iv.unattended ? " checked" : "") + ">无人值守</label>" +
    '<label><input type="checkbox" class="defaults-switch" data-instance="' + esc(iv.name) +
    '"' + (iv.defaults_enabled ? " checked" : "") + ">预设默认</label>" +
    "</div></header>";
}

/* §5 警示窗：无阻塞不渲染该元素 */
function warningsHtml(iv) {
  if (!iv.warnings || !iv.warnings.length) return "";
  var out = '<div class="warning-window" data-instance="' + esc(iv.name) + '">';
  iv.warnings.forEach(function (w) {
    var ackKey = "wwpanel.ack." + iv.name + "." + w.step;
    if (localStorage.getItem(ackKey)) {
      out += '<div class="warning collapsed" data-step="' + esc(w.step) +
        '" data-action="' + esc(w.action) + '" data-act="unack" data-instance="' +
        esc(iv.name) + '">⚠ ' + esc(w.step) + " " + esc(w.action) +
        " — 已知悉（点击展开）</div>";
      return;
    }
    out += '<div class="warning" data-step="' + esc(w.step) + '" data-action="' +
      esc(w.action) + '">' +
      '<div class="warning-detail">⛔ ' + esc(w.step) + "（" + esc(w.action) +
      "）: " + esc(w.detail) + "</div>" +
      '<ul class="warning-suggestions">' +
      (w.suggestions || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") +
      "</ul>" +
      '<div class="warning-actions">' +
      '<button class="hermes-probe" data-act="probe" data-hermes-send="' + esc(w.prompt) +
      '">🔍 让 hermes 排查</button>' +
      '<button class="warning-ack" data-act="ack" data-instance="' + esc(iv.name) +
      '" data-step="' + esc(w.step) + '">知道了</button></div></div>';
  });
  return out + "</div>";
}

/* §1 总览：泳道 + 19 格进度条（015-P1 条块可点击展开详情）+ 负责人 + 活跃卡 */
function overviewHtml(iv) {
  var ov = iv.overview;
  var lanes = ov.stages.map(function (s, i) {
    return '<span class="stage-lane' + (i === ov.stage_idx ? " current" : "") +
      '">' + esc(s) + "</span>";
  }).join('<span class="lane-arrow">→</span>');
  var expanded = expandedStep[iv.name] || null;
  var segs = ov.nodes.map(function (nd) {
    var cls = "seg" + (nd.done ? " done" : "") + (nd.current ? " current" : "") +
      (nd.rework > 0 ? " rework" : "") + (nd.id === expanded ? " expanded" : "");
    var extra = nd.rework > 0 ? ' data-rework-count="' + nd.rework + '"' : "";
    return '<span class="' + cls + '" data-step="' + esc(nd.id) + '"' + extra +
      ' data-act="stepseg" data-instance="' + esc(iv.name) + '"' +
      ' title="' + esc(nd.id) + "（点击展开/收起详情）" +
      (nd.rework > 0 ? " · 返工 " + nd.rework + " 次" : "") +
      '"></span>';
  }).join("");
  var detail = expanded ? stepDetailHtml(iv, expanded) : "";
  var owners = ov.owners && ov.owners.length ? ov.owners.map(esc).join(" + ") : "—";
  var card = ov.active_card
    ? '<div class="active-card">[当前活跃卡: ' + esc(ov.active_card.id) + " " +
      esc(ov.active_card.status) + "]</div>" : "";
  return '<div class="sec overview"><div class="lanes">' + lanes + "</div>" +
    '<div class="progress-row"><div class="progress-bar">' + segs + "</div>" +
    '<span class="progress-text">' + ov.done_count + "/" + ov.total + " · " +
    ov.percent + '%</span></div>' + detail +
    '<div class="owners">负责人: ' + owners + "</div>" + card + "</div>";
}

/* 015-P1 详情卡：负责人 / skill / 看板卡 / 成果 / 打回痕迹
   （全部来自 /api/state overview.nodes 的派生字段，零额外请求）*/
function stepDetailHtml(iv, sid) {
  var nd = null;
  (iv.overview.nodes || []).forEach(function (n) { if (n.id === sid) nd = n; });
  if (!nd) return "";
  var card = nd.card
    ? "卡 " + esc(nd.card.id) + " · " + esc(nd.card.status) +
      (nd.card.summary ? " · " + esc(nd.card.summary) : "")
    : "（无卡）";
  var skills = (nd.unbound || !nd.skills || !nd.skills.length)
    ? "（无绑定 skill）"
    : nd.skills.map(function (s) {
        return esc(s.skill) + "（" + esc(s.owner) + "）";
      }).join("、");
  function fmtSize(n) {
    return n >= 1024 ? (n / 1024).toFixed(1) + "KB" : n + "B";
  }
  var arts = (nd.artifacts || []).length
    ? nd.artifacts.map(function (a) {
        return '<div class="sd-row"><span class="sd-file" data-act="open" data-path="' +
          esc(a.path) + '">' + esc(a.name) + '</span> <span class="muted">' +
          fmtSize(a.size) + " · " + esc(a.mtime) + "（点击经 /api/artifact 打开）</span></div>";
      }).join("")
    : '<div class="sd-row muted">（无产物）</div>';
  var traces = (nd.traces || []).length
    ? nd.traces.map(function (t) {
        return '<div class="sd-row sd-trace">↺ ' + esc(t.action) + ": " +
          esc(t.detail) + "</div>";
      }).join("")
    : "";
  return '<div class="step-detail" data-step="' + esc(sid) + '">' +
    '<div class="sd-title">' + esc(sid) + "</div>" +
    '<div class="sd-row"><span class="sd-label">负责人</span>' +
    esc(nd.assignee || "—") + "</div>" +
    '<div class="sd-row"><span class="sd-label">使用 skill</span>' + skills + "</div>" +
    '<div class="sd-row"><span class="sd-label">看板卡</span>' + card + "</div>" +
    '<div class="sd-row"><span class="sd-label">成果</span></div>' + arts +
    traces + "</div>";
}

/* §2 skills 双栏 */
function skillStepHtml(sid, unbound, skills) {
  if (unbound || !skills || !skills.length) {
    return '<div class="skill-step" data-step="' + esc(sid) +
      '"><span class="skill-name">(无绑定 skill)</span><span class="skill-owner"></span></div>';
  }
  return skills.map(function (s) {
    return '<div class="skill-step" data-step="' + esc(s.step || sid) +
      '"><span class="skill-name">✓ ' + esc(sid) + " · " + esc(s.skill) +
      '</span><span class="skill-owner">(' + esc(s.owner) + ")</span></div>";
  }).join("");
}
function skillsHtml(iv) {
  var cur = iv.skills.current;
  var card = cur.active_card
    ? '<div class="active-card">[当前活跃卡: ' + esc(cur.active_card.id) + " " +
      esc(cur.active_card.status) + "]</div>" : "";
  return '<div class="sec skills-cols">' +
    '<div class="skills-done"><h4>已完成流程</h4>' +
    (iv.skills.done || []).map(function (d) {
      return skillStepHtml(d.step, d.unbound, d.skills);
    }).join("") || '<div class="muted">（暂无）</div>' + "</div>" +
    '<div class="skills-current"><h4>当前流程 ' + esc(cur.step || "-") + "</h4>" +
    skillStepHtml(cur.step, cur.unbound, cur.skills) + card + "</div></div>";
}

/* §3 拍板时间线 + 待拍板横幅 */
function decisionsHtml(iv) {
  var dc = iv.decisions;
  var out = '<div class="sec decisions"><h4>拍板记录 (' + (dc.records || []).length +
    ")</h4>";
  if (dc.pending) {
    var p = dc.pending;
    out += '<div class="pending-decision" data-gate="' + esc(p.step) + '">' +
      '<div class="pending-question">⏸ 待拍板 ' + esc(p.gate_label) + "：" +
      esc(p.question) + "</div>" +
      '<div class="pending-options">' + (p.options || []).map(function (o) {
        return '<button class="decide" data-act="decide" data-instance="' + esc(iv.name) +
          '" data-key="' + esc(o.key) + '">' + esc(o.key) + " · " + esc(o.label) + "</button>";
      }).join("") + "</div>" +
      (p.step === "ww_gate_final"
        ? '<div class="note-row"><input type="text" class="decision-note-input" ' +
          'placeholder="C 路径修改意见（选 C 时随决策提交：写 C|意见）"></div>' : "") +
      '<div class="ctx-files">' + (p.context_files || []).map(function (f) {
        return '<details class="ctx-file"><summary>' + esc(f.name) +
          (f.title ? " — " + esc(f.title) : "") + "</summary><pre>" + esc(f.head) +
          "</pre></details>";
      }).join("") + "</div></div>";
  }
  (dc.records || []).forEach(function (r) {
    out += '<div class="decision-record" data-gate="' + esc(r.gate) + '">' +
      '<span class="gate-label">' + esc(r.gate_label) + "</span>" +
      '<span class="decision-option">' + esc(r.decision) + " — " + esc(r.option_label) +
      "</span>" +
      '<span class="decision-tag">' + esc(r.tag) + "</span>" +
      (r.note ? '<div class="decision-note collapsed" data-act="note">意见: ' + esc(r.note) +
        '<span class="muted">（点击展开/收起）</span></div>' : "") +
      (r.reworked ? '<div class="rework-flag">↪ 已打回返工，返工后回本门再确认</div>' : "") +
      "</div>";
  });
  return out + "</div>";
}

/* §4 产物手风琴 */
function artifactsHtml(iv) {
  var out = '<div class="sec artifacts"><h4>流程成果（点开/收起）</h4>';
  (iv.artifacts || []).forEach(function (a) {
    if (a.kind === "note") {
      out += '<details class="artifact-group" data-step="' + esc(a.step) + '"><summary>' +
        esc(a.group) + ' · 修改意见 <span class="muted">（非文件）</span></summary>' +
        '<div class="artifact-note">' + esc(a.note_text) + "</div></details>";
      return;
    }
    var cur = a.current;
    if (!cur) return;
    var btns = '<button class="artifact-open" data-act="open" data-path="' + esc(cur.path) +
      '">[打开]</button>';
    if (a.kind === "html") {
      btns = '<button class="artifact-preview" data-act="preview" data-path="' +
        esc(cur.path) + '">[预览]</button>' + btns;
    }
    var media = a.kind === "img"
      ? '<img class="artifact-img" src="/api/artifact?path=' +
        encodeURIComponent(cur.path) + '" alt="' + esc(cur.name) + '">' : "";
    var hist = a.history && a.history.length
      ? '<details class="artifact-history"><summary>历史版本 (' + a.history.length +
        ")</summary>" + a.history.map(function (h) {
          return '<div class="artifact-history-item" data-act="open" data-path="' +
            esc(h.path) + '">' + esc(h.name) + "</div>";
        }).join("") + "</details>" : "";
    out += '<details class="artifact-group" data-step="' + esc(a.step) + '"><summary>' +
      esc(a.group) + ' <span class="muted">' + esc(cur.name) + "</span></summary>" +
      '<div class="artifact-current"><span class="artifact-name">' + esc(cur.name) +
      "</span>" + btns + "</div>" + media + hist + "</details>";
  });
  return out + "</div>";
}

/* §6 并行支路 */
function groupsHtml(iv) {
  var out = "";
  (iv.groups || []).forEach(function (g) {
    out += '<div class="parallel-group" data-step="' + esc(g.step) + '">' +
      '<div class="group-header">' + esc(g.title) + " (" + esc(g.step) + ") " +
      (g.finished ? '<span class="done-flag">✓ 已完成</span>' : "") +
      '<span class="count">' + g.done + "/" + g.total + " 完成</span></div>" +
      '<div class="group-cols">' + g.columns.map(function (c) {
        var skillTxt = c.unbound || !c.skills.length
          ? "skill: (无绑定)"
          : "skill: " + c.skills.map(function (s) {
              return esc(s.skill) + " (" + esc(s.owner) + ")";
            }).join(", ");
        return '<div class="group-col" data-member="' + esc(c.member) + '">' +
          '<div class="col-member">' + esc(c.member) + "</div>" +
          '<div class="col-flow">流程: ' + esc(c.flow) + "</div>" +
          '<div class="col-owner">负责人: ' + esc(c.assignee || "—") + "</div>" +
          '<div class="col-percent">完成度: ' + c.percent + "%</div>" +
          '<div class="col-skill">' + skillTxt + "</div>" +
          '<div class="col-card-status">卡: ' + esc(c.card_status || "未建卡") + "</div>" +
          (c.verdict_path ? '<button data-act="open" data-path="' + esc(c.verdict_path) +
            '">[打开 verdict]</button>' : "") + "</div>";
      }).join("") + "</div></div>";
  });
  return out;
}

/* ---- 抽屉（内容经 /api/artifact 取，URL 不变，无整页刷新）---- */
function closeDrawer() {
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("drawer-mask").classList.remove("show");
}
function openDrawer(title) {
  document.getElementById("drawer").classList.add("open");
  document.getElementById("drawer-mask").classList.add("show");
  document.getElementById("drawer-title").textContent = title || "";
  document.getElementById("drawer-content").innerHTML =
    '<div class="muted">加载中…</div>';
}
function modeFor(path) {
  var p = String(path).toLowerCase();
  if (/\\.(png|jpe?g|gif|webp)$/.test(p)) return "img";
  if (/\\.html?$/.test(p)) return "preview";
  return "text";
}
function openArtifact(path, mode) {
  mode = mode || modeFor(path);
  openDrawer(String(path).split(/[\\\\/]/).pop() + " — " + mode);
  var content = document.getElementById("drawer-content");
  var url = "/api/artifact?path=" + encodeURIComponent(path);
  if (mode === "img") { content.innerHTML = '<img class="drawer-img" src="' + url + '">'; return; }
  fetch(url).then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.text();
  }).then(function (t) {
    if (mode === "preview") {
      content.innerHTML = '<iframe class="drawer-frame" sandbox="" srcdoc="' + esc(t) +
        '"></iframe>';
    } else {
      content.innerHTML = "<pre>" + esc(t) + "</pre>";
    }
  }).catch(function (e) {
    content.textContent = "读取失败: " + e.message + "（路径须在白名单根内）";
  });
}

/* ---- 事件委托：拍板/开关/抽屉/排查/知道了 ---- */
document.addEventListener("click", function (ev) {
  var el = ev.target.closest("[data-act]");
  if (el) {
    var act = el.dataset.act;
    if (act === "tab") { selectedInstance = el.dataset.instance; render(); return; }
    if (act === "stepseg") {
      /* 015-P1：条块点击展开/收起（再点同一条块收起） */
      var cardEl = el.closest(".instance-card");
      var key = (cardEl && cardEl.dataset.instance) || "_";
      expandedStep[key] = (expandedStep[key] === el.dataset.step) ? null : el.dataset.step;
      render(); return;
    }
    if (act === "open") { openArtifact(el.dataset.path); return; }
    if (act === "preview") { openArtifact(el.dataset.path, "preview"); return; }
    if (act === "note") {
      el.classList.toggle("expanded"); el.classList.toggle("collapsed"); return;
    }
    if (act === "ack") {
      localStorage.setItem("wwpanel.ack." + el.dataset.instance + "." + el.dataset.step, "1");
      render(); toast("已记为知悉（仅本浏览器记忆，不改引擎数据）"); return;
    }
    if (act === "unack") {
      localStorage.removeItem("wwpanel.ack." + el.dataset.instance + "." + el.dataset.step);
      render(); return;
    }
    if (act === "probe") {
      var msg = el.dataset.hermesSend;
      if (window.hermes && typeof window.hermes.send === "function") {
        window.hermes.send(msg);
      } else {
        if (navigator.clipboard) { navigator.clipboard.writeText(msg).catch(function () {}); }
        toast("已复制排查 prompt，请粘贴给 hermes");
      }
      return;
    }
    if (act === "decide") {
      var decision = el.dataset.key;
      var pendingBox = el.closest(".pending-decision");
      var noteEl = pendingBox && pendingBox.querySelector(".decision-note-input");
      if (noteEl && noteEl.value.trim() && decision.toUpperCase() === "C") {
        decision = "C|" + noteEl.value.trim();
      }
      fetch("/decide", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({instance: el.dataset.instance, decision: decision})
      }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
        .then(function (res) {
          toast(res.ok ? "拍板已写入: " + decision : "失败: " + (res.j.error || ""));
          poll(true);
        }).catch(function () { toast("提交失败（网络）"); });
      return;
    }
  }
  if (ev.target.id === "drawer-close" || ev.target.id === "drawer-mask") closeDrawer();
});

document.addEventListener("change", function (ev) {
  var t = ev.target;
  if (!t.classList) return;
  var url = null;
  if (t.classList.contains("unattended-switch")) url = "/unattended";
  if (t.classList.contains("defaults-switch")) url = "/defaults";
  if (!url) return;
  fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({instance: t.dataset.instance, enabled: t.checked})
  }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
    .then(function (res) {
      if (res.ok) { toast("开关已更新"); poll(true); }
      else { toast("失败: " + (res.j.error || "")); t.checked = !t.checked; }
    }).catch(function () { toast("提交失败（网络）"); t.checked = !t.checked; });
});

poll(true);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# §9 可编程接口（规格 §9 契约：验收测试与抽屉复用，函数名/签名逐字固定）
# ---------------------------------------------------------------------------

def render_html(state: dict) -> str:
    """返回完整页面 HTML（GET / 的响应体）。

    面板是「单页外壳 + /api/state 轮询渲染」，服务端不预渲染 DOM；为了让无浏览器
    的契约测试拿到本次状态快照，这里把 state 作为 window.__WW_STATE__ 内嵌进外壳
    （纯附加，不干扰前端已有的轮询/poll 逻辑）。
    """
    snapshot = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    tag = f"<script>window.__WW_STATE__={snapshot};</script>\n"
    if "</body>" in PAGE_HTML:
        return PAGE_HTML.replace("</body>", tag + "</body>", 1)
    return PAGE_HTML + tag


def read_artifact(path: str) -> tuple[int, str]:
    """白名单内产物读取（与 HTTP /api/artifact 同源判定，供抽屉与测试复用）。

    返回 (200, 文本) / (403, "") / (404, "")；相对路径按 EXCHANGE_DIR 解析，
    resolve 后必须落在白名单四根之内（挡 ../ 穿越）。
    """
    raw = (path or "").strip()
    if not raw:
        return 403, ""
    p = Path(raw)
    if not p.is_absolute():
        p = EXCHANGE_DIR / p
    try:
        rp = p.resolve()
    except OSError:
        return 403, ""
    if not any(rp.is_relative_to(root) for root in _whitelist_roots()):
        return 403, ""
    if not rp.is_file():
        return 404, ""
    try:
        return 200, rp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 404, ""


def make_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT):
    """构造未启动的 ThreadingHTTPServer（port=0 → 随机空闲端口，测试隔离用）。"""
    httpd = ThreadingHTTPServer((host, port), PanelHandler)
    httpd.daemon_threads = True
    return httpd


# ---------------------------------------------------------------------------
# §6 HTTP 服务（ThreadingHTTPServer + BaseHTTPRequestHandler，单文件内嵌前端）
# ---------------------------------------------------------------------------

# /api/artifact 白名单根：resolve 后 is_relative_to 校验（必须挡 ../ 穿越）
def _whitelist_roots() -> list[Path]:
    roots = []
    for r in (ARTIFACTS_DIR, VERDICTS_DIR, WECHAT_PROJECT_DIR, REPORTS_DIR):
        try:
            roots.append(r.resolve())
        except OSError:
            roots.append(r)
    return roots


def _artifact_mime(path: Path) -> str:
    """白名单内容的回包类型：md/txt 纯文本、html 文本、png/jpg 二进制，其余流式。"""
    ext = path.suffix.lower()
    if ext in (".md", ".markdown", ".txt"):
        return "text/plain; charset=utf-8"
    if ext in (".html", ".htm"):
        return "text/html; charset=utf-8"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return "application/octet-stream"


def _decision_default_keys(tpl: dict) -> dict[str, str]:
    """§6 POST /defaults：每个 decision 步的默认 key——options 里 key 大写=='A'
    优先，否则第一个 option 的 key。"""
    out: dict[str, str] = {}
    for s in tpl.get("steps", []):
        if s.get("type") != "decision":
            continue
        opts = s.get("options") or []
        key = None
        for o in opts:
            if str(o.get("key", "")).strip().upper() == "A":
                key = str(o.get("key", ""))
                break
        if key is None and opts:
            key = str(opts[0].get("key", ""))
        if key:
            out[s["id"]] = key
    return out


class PanelHandler(BaseHTTPRequestHandler):
    """面板 HTTP 处理器：GET /、/api/state（ETag/304）、/api/artifact（白名单）；
    POST /decide、/defaults、/unattended（400 守卫，不越权写引擎 state）。"""
    server_version = "ww_panel/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: D102 — 关闭访问日志噪音（10s 轮询会刷屏）
        pass

    # ---- 基础回包 ----
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, obj, code: int = 200, extra: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", extra)

    def _send_err(self, code: int, msg: str):
        self._send_json({"ok": False, "error": msg}, code)

    def _read_json(self) -> dict | None:
        """POST body 解析：非 JSON / 超 1MB / 非对象 → None（调用方回 400）。"""
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return None
        if n <= 0 or n > 1_048_576:
            return None
        try:
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
        except (OSError, ValueError):
            return None
        return obj if isinstance(obj, dict) else None

    # ---- GET ----
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if route == "/api/state":
            try:
                etag = compute_etag()
                inm = self.headers.get("If-None-Match", "")
                if etag in [v.strip() for v in inm.split(",") if v.strip()]:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    return
                body = json.dumps(build_state(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8",
                           {"ETag": etag, "Cache-Control": "no-cache"})
            except Exception as e:  # noqa: BLE001 — state 组装失败回 500，不挂线程
                self._send_err(500, f"state 组装失败: {e}")
            return
        if route == "/api/artifact":
            self._do_artifact(parse_qs(parsed.query))
            return
        self._send_err(404, f"未知路径: {route}")

    def _do_artifact(self, qs: dict):
        """白名单校验：相对路径按 EXCHANGE_DIR 解析；resolve 后必须落在四根之内，
        否则 403（含 ../ 穿越）；缺文件 404。"""
        vals = qs.get("path") or []
        if not vals or not vals[0].strip():
            self._send_err(400, "缺 path 参数")
            return
        raw = vals[0].strip()
        p = Path(raw)
        if not p.is_absolute():
            p = EXCHANGE_DIR / p
        try:
            rp = p.resolve()
        except OSError:
            self._send_err(403, "路径不可解析")
            return
        if not any(rp.is_relative_to(root) for root in _whitelist_roots()):
            self._send_err(403, f"路径越权（白名单外）: {raw}")
            return
        if not rp.is_file():
            self._send_err(404, f"文件不存在: {raw}")
            return
        try:
            data = rp.read_bytes()
        except OSError:
            self._send_err(404, f"文件不可读: {raw}")
            return
        self._send(200, data, _artifact_mime(rp), {"Cache-Control": "no-cache"})

    # ---- POST ----
    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/decide":
            self._post_decide()
            return
        if route == "/defaults":
            self._post_defaults()
            return
        if route == "/unattended":
            self._post_unattended()
            return
        if route == "/reassign":
            self._post_reassign()
            return
        self._send_err(404, f"未知路径: {route}")

    def _instance_state(self, name: str) -> dict | None:
        if not name or not re.fullmatch(r"[\w.-]+", name):
            return None
        return _load_instance(name)

    def _post_decide(self):
        """POST /decide {"instance","decision"}：守卫（实例在/当前步是门/未 completed/
        decision 非空）不过 → 400 且不写文件；通过 → 写 <name>.decision（无换行）。"""
        body = self._read_json()
        if body is None or "instance" not in body or "decision" not in body:
            self._send_err(400, "body 须为 JSON 且含 instance/decision")
            return
        name = str(body["instance"])
        decision = str(body["decision"])
        if not decision.strip():
            self._send_err(400, "decision 为空")
            return
        state = self._instance_state(name)
        if state is None:
            self._send_err(400, f"实例不存在: {name}")
            return
        if state.get("status") == "completed":
            self._send_err(400, "实例已 completed，不接受拍板")
            return
        try:
            tpl = _template_for_state(state)   # 大修：按实例绑定模板校验拍板门
        except (OSError, ValueError) as e:
            self._send_err(500, f"pipeline 读取失败: {e}")
            return
        step = _step_map(tpl).get(state.get("step") or "", {})
        if step.get("type") != "decision":
            self._send_err(400, f"当前步 {state.get('step')} 不是拍板门，拒写")
            return
        try:
            INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
            (INSTANCES_DIR / f"{name}.decision").write_text(decision, encoding="utf-8")
        except OSError as e:
            self._send_err(500, f"决策文件写入失败: {e}")
            return
        self._send_json({"ok": True, "written": decision})

    def _post_defaults(self):
        """POST /defaults {"instance","enabled"}：开=写全门默认 key 映射，关=删文件。"""
        body = self._read_json()
        if body is None or "instance" not in body or "enabled" not in body:
            self._send_err(400, "body 须为 JSON 且含 instance/enabled")
            return
        name = str(body["instance"])
        enabled = bool(body["enabled"])
        if self._instance_state(name) is None:
            self._send_err(400, f"实例不存在: {name}")
            return
        fp = INSTANCES_DIR / f"{name}.defaults.json"
        try:
            if enabled:
                # 大修：按实例绑定模板生成全门默认 key（非默认模板实例不再写 ww_ 门）
                tpl = _template_for_state(self._instance_state(name))
                INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
                fp.write_text(json.dumps(_decision_default_keys(tpl), ensure_ascii=False,
                                         indent=2), encoding="utf-8")
            else:
                fp.unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            self._send_err(500, f"defaults 文件写入失败: {e}")
            return
        self._send_json({"ok": True, "enabled": enabled})

    def _post_unattended(self):
        """POST /unattended {"instance","enabled"}：开=写 .unattended 标记，关=删。"""
        body = self._read_json()
        if body is None or "instance" not in body or "enabled" not in body:
            self._send_err(400, "body 须为 JSON 且含 instance/enabled")
            return
        name = str(body["instance"])
        enabled = bool(body["enabled"])
        if self._instance_state(name) is None:
            self._send_err(400, f"实例不存在: {name}")
            return
        fp = INSTANCES_DIR / f"{name}.unattended"
        try:
            if enabled:
                INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().isoformat(timespec="seconds")
                fp.write_text(f"unattended by ww_panel {ts}\n", encoding="utf-8")
            else:
                fp.unlink(missing_ok=True)
        except OSError as e:
            self._send_err(500, f"unattended 文件写入失败: {e}")
            return
        self._send_json({"ok": True, "enabled": enabled})

    def _post_reassign(self):
        """POST /reassign {"instance","step"}：blocked 卡手动重派（016 批次三 #10）。

        门禁（同既有安全模型，逐条不过 → 4xx 且零 CLI 调用）：
        ① 实例存在（INSTANCES_DIR 有 <name>.json）；
        ② step id 形态合法；
        ③ 卡经 wweng:<instance>:<step> 键空间解析得出（客户端传卡 id 无效——
           键空间错即查无卡，杜绝拿别板/别实例卡 id 越权重派）；
        ④ 卡 status=blocked（ready/running/done 卡不重派）。
        通过 → hermes kanban unblock <卡id> --reason "user 手动重派"
             → hermes kanban dispatch（立即派发重跑）。
        """
        body = self._read_json()
        if body is None or "instance" not in body or "step" not in body:
            self._send_err(400, "body 须为 JSON 且含 instance/step")
            return
        name = str(body["instance"])
        step_id = str(body["step"])
        if self._instance_state(name) is None:
            self._send_err(400, f"实例不存在: {name}")
            return
        if not re.fullmatch(r"[\w-]+", step_id or ""):
            self._send_err(400, f"step 非法: {step_id}")
            return
        card = _query_card(name, step_id)
        if not card:
            self._send_err(404, f"未找到卡（wweng:{name}:{step_id}）")
            return
        if card.get("status") != "blocked":
            self._send_err(409, f"卡 {card.get('id')} status={card.get('status')}，"
                                f"仅 blocked 卡可重派")
            return
        cid = str(card.get("id") or "")
        if not cid.startswith("t_"):
            self._send_err(400, f"卡 id 非法: {cid}")
            return
        rc1, out1 = _kanban_cli(["unblock", cid, "--reason", "user 手动重派"])
        if rc1 != 0:
            self._send_err(500, f"unblock 失败 rc={rc1}: {out1[-200:]}")
            return
        rc2, out2 = _kanban_cli(["dispatch"])
        if rc2 != 0:
            self._send_err(500, f"dispatch 失败 rc={rc2}: {out2[-200:]}")
            return
        self._send_json({"ok": True, "instance": name, "step": step_id, "card": cid,
                         "note": "卡已 unblock 并触发重派（hermes kanban dispatch）"})
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="we-write 写作流水线前面板仪表盘（只读 + 三类决策文件，066 方案）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8660）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--print-state", action="store_true",
                    help="打印 /api/state 同构 JSON 到 stdout 后退出（供验收）")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = ap.parse_args()

    if args.print_state:
        print(json.dumps(build_state(), ensure_ascii=False, indent=2))
        return 0

    httpd = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    httpd.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    print(f"ww_panel 已启动: {url}（Ctrl+C 退出）", flush=True)
    if args.open:
        import webbrowser  # 标准库，仅 --open 时加载
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
