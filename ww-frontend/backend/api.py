"""
we-write 写作引擎 API Server — FastAPI 后端（仿 flow-engine api.py 模式）

端口 8670（避开 flow-engine 8650 / ww_panel 8660），CORS 全开。

端点一览：
  GET  /api/health                    — 健康检查（侧栏引擎状态灯数据源）
  GET  /api/templates                 — 文章模板列表（005 需求1：config/templates/ 目录）
  GET  /api/instances                 — 列出全部文章实例（读 instances/*.json）
  POST /api/instances                 — 新建实例 {name, template?, mode?, materials?}
                                         （005：mode 缺省 live；材料注入实例 state，
                                          引擎首次派选题卡时写卡 comment）
  GET  /api/instances/{name}          — 实例详情（完整 state JSON，含 history）
  POST /api/instances/{name}/advance  — 推进一步（subprocess 调 engine.py，超时 300s）
  POST /api/instances/{name}/decision — 写拍板决定（落 instances/<名>.decision；
                                         支持 `C|意见` 带附件材料的对话框提交）
  GET  /api/pipeline                  — 解析模板（?template= 缺省默认模板；含拍板
                                         选项 question/options 供拍板界面全文渲染）
  GET  /api/logs                      — 内存日志（advance / decision / create 留痕）

数据源与写边界：
  - 引擎目录只读代码；唯一写盘位置 = instances/ 下的实例 JSON / .decision 文件
    （这是引擎自身设计的数据通道，非修改引擎代码）。
  - subprocess 用 sys.executable，cwd=引擎目录（engine.py 依赖同目录模块导入）。

运行：
  cd ./ww-frontend/backend
  python -m uvicorn api:app --host 0.0.0.0 --port 8670
  → http://localhost:8670/api
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 路径常量（env 可覆盖，便于测试隔离）───────────────────
_ENGINE_DIR_DEFAULT = r"./engine"
ENGINE_DIR = Path(os.environ.get("WW_ENGINE_DIR", _ENGINE_DIR_DEFAULT))
# 与 engine.py 同源：INSTANCES_DIR 尊重 WW_INSTANCES_DIR 覆盖
INSTANCES_DIR = Path(os.environ.get(
    "WW_INSTANCES_DIR", str(ENGINE_DIR / "instances")))
ENGINE_PY = ENGINE_DIR / "engine.py"
# 005 需求1：pipeline.yaml 退役 → 模板目录 + 默认模板（engine/config/templates/）
TEMPLATES_DIR = ENGINE_DIR / "config" / "templates"
DEFAULT_TEMPLATE = "hot-topic-commentary"
# B1/B2 同源：exchange 根（引擎 engine.py G-1 同口径 env 覆盖）
EXCHANGE_DIR = Path(os.environ.get(
    "WW_EXCHANGE_DIR", "./exchange"))
# 20260921（user 需求「立项即有正式规则命名的文件夹」）：新建实例时即按成果区
# 既有规则预建 <日期>/<NN>-<slug>/（meta status: created），收官归档时复用同一
# 目录（archive._archive 识别预建 meta 原地落成品）。slug 取实例名的 ASCII 压缩
# （与 resolve_slug 实例名兜底同规则，article-002→article002 同款）。
WECHAT_PROJECT_DIR = Path(os.environ.get(
    "WW_WECHAT_PROJECT_DIR", "Wechat-project"))

# 生命周期耦合（2026-10-10）：新建实例 → 保证心跳 cron 在跑。cron_coupling 与
# engine 同目录；append（非 insert(0)）避免 engine 目录模块名遮蔽三方依赖。
if str(ENGINE_DIR) not in sys.path:
    sys.path.append(str(ENGINE_DIR))
import cron_coupling  # noqa: E402


def _ensure_project_folder(name: str) -> str:
    """立项预建成果目录（幂等）：Wechat-project/<日期>/<NN>-<slug>/ + meta.yaml。

    复用 engine/archive.py 的路径与规则单源（project_root/next_seq/find_archived/
    slugify），保证与收官归档、面板项目库完全同口径。返回目录路径字符串。
    """
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    import archive as ww_archive
    root = ww_archive.project_root()
    hit = ww_archive.find_archived(name, root)
    if hit is not None:
        return str(hit)  # 已预建/已归档 → 原地复用
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    seq = ww_archive.next_seq(date, root)
    slug = ww_archive.slugify(name) or "project"
    dest = root / date / f"{seq:02d}-{slug}"
    dest.mkdir(parents=True, exist_ok=True)
    meta = dest / "meta.yaml"
    if not meta.exists():
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        meta.write_text(
            "---\n"
            f"title: \"{name}（立项预建，收官归档后更新为本篇标题）\"\n"
            "project: we-write-engine\n"
            "artifact_type: meta\n"
            "status: created\n"
            f"created_at: '{now}'\n"
            f"instance: {name}\n"
            f"seq: '{seq:02d}'\n"
            f"archive_dir: {dest.as_posix()}\n"
            "---\n\n"
            f"# {name} 立项预建成果目录\n"
            "- 收官拍板后由 ww_done 归档动作在本目录落 8 标配件\n"
            "  （article-final.md / formatted.html / preview.html / cover 等），\n"
            "  meta.yaml 原地更新为归档 meta。\n",
            encoding="utf-8")
    return str(dest)

API_PORT = 8670
PANEL_PORT = 8660          # ww_panel 独立仪表盘（前端 iframe 嵌入目标）
ADVANCE_TIMEOUT_S = 300    # 单步推进超时（引擎一步含建卡/查询，留足余量）

# ── 实例名校验 ────────────────────────────────────────────
# 只许字母数字-（与引擎 instances/<名>.json 命名一致）；
# t- 前缀是引擎测试实例保留名（archive 对 t-* 自动关闭），正式文章禁用。
NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")
STEP_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # 步骤 id 允许下划线（ww_ 前缀）
TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")  # 与 engine template_loader 同源
MATERIAL_TYPES = ("text", "url", "file", "folder")  # 005 需求2 材料四形态
MAX_MATERIALS = 50          # 单实例材料条数上限（防误提交超长清单）
MAX_MATERIAL_LEN = 2000     # 单条材料内容上限（引擎侧截 500 字入卡评论）


def _validate_name(name: str) -> str:
    """实例名门禁：非空、字符白名单、拒 t- 前缀。通过返回去空格名。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "实例名不能为空")
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "实例名只允许字母、数字、连字符（-）")
    if name.lower().startswith("t-"):
        raise HTTPException(400, "t- 前缀是引擎测试实例保留名，禁止用于正式文章")
    return name


# ── 内存日志（advance / decision / create 留痕，页面 /logs 消费）──
LOGS: list[dict[str, Any]] = []
LOG_CAP = 500  # 上限，超出丢弃最旧


def _log(level: str, source: str, message: str) -> None:
    LOGS.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "source": source,
        "message": message,
    })
    if len(LOGS) > LOG_CAP:
        del LOGS[:-LOG_CAP]


# ── FastAPI 初始化 ────────────────────────────────────────

app = FastAPI(
    title="we-write 写作引擎 API",
    version="1.0.0",
    description="we-write-engine 包装层 REST API — 实例查询/推进/拍板/流水线/日志",
)

# CORS（本地服务，全放开：dev 5175 / preview / iframe 场景都要跨）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 数据模型 ──────────────────────────────────────────────


class MaterialItem(BaseModel):
    """005 需求2：写作材料条目（打字文本 / 网址 / 文件路径 / 文件夹路径）。"""
    type: str = "text"      # text | url | file | folder
    content: str


class CreateInstanceRequest(BaseModel):
    """005 需求2/5：新建文章——模板选择 + 材料清单 + 模式（缺省 live）。"""
    name: str
    template: str | None = None        # 缺省 hot-topic-commentary
    mode: str | None = None            # 缺省 live（需求5：不再静默成 test）
    materials: list[MaterialItem] | None = None


class AdvanceRequest(BaseModel):
    """step 可选：不传 = 引擎按实例 state 当前步推进；传 = 从指定步续跑。"""
    step: str | None = None


class DecisionRequest(BaseModel):
    decision: str


class ReassignRequest(BaseModel):
    """016 批次三 #10：blocked 卡重派（step = 当前步 id）。"""
    step: str


# ── 工具：实例 JSON 读取 ─────────────────────────────────


def _instance_path(name: str) -> Path:
    return INSTANCES_DIR / f"{name}.json"


def _read_instance(name: str) -> dict[str, Any]:
    """读实例 state JSON；文件缺失/损坏抛 404/422。"""
    fp = _instance_path(name)
    if not fp.exists():
        raise HTTPException(404, f"实例不存在: {name}")
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(422, f"实例 JSON 损坏: {name}（{e}）") from e


def _summary(st: dict[str, Any], fp: Path | None = None) -> dict[str, Any]:
    """列表卡片字段：instance/status/step/mode/iteration/template（前端卡片契约）。

    005 需求1：旧实例 state 无 template 字段 → 显示默认模板（引擎侧首次读取
    会回填落盘，此处兜底仅影响展示）。
    C2（20260920）扩展：created_at / completed_at（存量实例回退文件 ctime/mtime，
    不回填 state）、archived_dir（成果区反查）、hidden。
    """
    out: dict[str, Any] = {
        "instance": st.get("instance"),
        "status": st.get("status"),
        "step": st.get("step"),
        "mode": st.get("mode"),
        "iteration": st.get("iteration"),
        "template": st.get("template") or DEFAULT_TEMPLATE,
    }
    ctime = mtime = None
    if fp is not None:
        try:
            stat = fp.stat()
            ctime = datetime.fromtimestamp(stat.st_ctime).astimezone()\
                .isoformat(timespec="seconds")
            mtime = datetime.fromtimestamp(stat.st_mtime).astimezone()\
                .isoformat(timespec="seconds")
        except OSError:
            ctime = mtime = None
    out["created_at"] = st.get("created_at") or ctime
    out["completed_at"] = st.get("completed_at") or (
        mtime if st.get("status") == "completed" else None)
    inst = st.get("instance")
    out["archived_dir"] = _archived_dir(inst) if inst else None
    out["hidden"] = bool(inst) and (INSTANCES_DIR / f"{inst}.hidden").exists()
    # 016 批次三 #10：当前步卡 blocked → 前端「推进」置灰 + 显示「重派」按钮
    blocked = _blocked_card(inst, st.get("step")) if inst else None
    out["blocked"] = blocked is not None
    out["blocked_card"] = (blocked or {}).get("id")
    return out


def _blocked_card(name: str, step: str | None) -> dict[str, Any] | None:
    """016 批次三 #10：当前步 kanban 卡状态查询（只读；wweng: 键空间）。

    复用 ww_panel 同一 resolve_step_card（键空间 wweng:）；异常降级 None，
    不阻断列表。仅 blocked 状态返回卡 dict（其余状态 None → 前端不显示重派）。
    """
    if not step:
        return None
    try:
        if str(ENGINE_DIR) not in sys.path:
            sys.path.insert(0, str(ENGINE_DIR))
        import ww_panel
        card = ww_panel.kanban_client.resolve_step_card(
            name, step, key_prefix=ww_panel.KEY_PREFIX)
    except Exception:  # noqa: BLE001 — 查询降级不阻断列表
        return None
    if card and card.get("status") == "blocked":
        return card
    return None


def _archived_dir(name: str) -> str | None:
    """C2：反查实例已归档成果区目录（复用 ww_panel.find_archived_project 同一
    函数；异常降级 None，不影响列表）。"""
    try:
        if str(ENGINE_DIR) not in sys.path:
            sys.path.insert(0, str(ENGINE_DIR))
        import ww_panel
        hit = ww_panel.find_archived_project(name)
        return hit.get("dir") if hit else None
    except Exception:  # noqa: BLE001 — 反查降级不阻断列表
        return None


# ── 模板目录（005 需求1）────────────────────────────────


def _load_template_meta(tid: str) -> dict[str, Any] | None:
    """读单个模板 YAML 的元信息（id/title/description/status/步骤数）。"""
    fp = TEMPLATES_DIR / f"{tid}.yaml"
    if not fp.exists():
        return None
    try:
        import yaml
        tpl = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — 单模板损坏降级标注，不炸整页
        return {"id": tid, "title": f"{tid}（解析失败）", "description": str(e),
                "status": "broken", "runnable": False, "is_default": False,
                "step_count": None}
    status = tpl.get("status") or "complete"
    return {
        "id": tid,
        "title": tpl.get("title") or tid,
        "description": tpl.get("description") or "",
        "status": status,
        "runnable": status != "skeleton",   # 骨架占位未开放（引擎 run 拒跑）
        "is_default": tid == DEFAULT_TEMPLATE,
        "step_count": len(tpl.get("steps") or []),
    }


def _list_templates() -> list[dict[str, Any]]:
    """扫描模板目录：默认模板置顶，其余按 id 排序。"""
    if not TEMPLATES_DIR.is_dir():
        return []
    ids = sorted(
        p.stem for p in TEMPLATES_DIR.glob("*.yaml")
        if TEMPLATE_ID_RE.fullmatch(p.stem))
    if DEFAULT_TEMPLATE in ids:
        ids.remove(DEFAULT_TEMPLATE)
        ids.insert(0, DEFAULT_TEMPLATE)
    return [m for m in (_load_template_meta(t) for t in ids) if m]


# ── 端点 ──────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, Any]:
    """健康检查：引擎目录/模板目录/面板端口可达性一并汇报（侧栏状态灯数据源）。"""
    panel_up = (TEMPLATES_DIR / f"{DEFAULT_TEMPLATE}.yaml").exists()
    return {
        "status": "ok",
        "engine_dir": str(ENGINE_DIR),
        "engine_py_exists": ENGINE_PY.exists(),
        "pipeline_exists": panel_up,  # 字段名保留（前端契约）：默认模板在位性
        "panel_url": f"http://127.0.0.1:{PANEL_PORT}/",
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/templates")
def list_templates() -> list[dict[str, Any]]:
    """005 需求1：文章模板列表（默认模板置顶；骨架标注 runnable=false）。"""
    return _list_templates()


@app.get("/api/instances")
def list_instances(include_hidden: int = 0) -> list[dict[str, Any]]:
    """列出全部实例：扫 instances/*.json，损坏文件降级标注不炸整页。

    C2（20260920）：默认过滤 hidden=true 的实例（?include_hidden=1 时返回全部）。
    """
    if not INSTANCES_DIR.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for fp in sorted(INSTANCES_DIR.glob("*.json")):
        if fp.name.endswith(".defaults.json"):
            continue  # 20260921：预设默认映射非实例（glob 误收 → instance=null 幽灵条目）
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 与 engine.py --list 同口径：损坏文件显示 (损坏) 占位
            out.append({
                "instance": fp.stem, "status": "(损坏)", "step": None,
                "mode": None, "iteration": None,
            })
            continue
        if not include_hidden and (INSTANCES_DIR / f"{fp.stem}.hidden").exists():
            continue
        out.append(_summary(st, fp))
    return out


@app.post("/api/instances", status_code=201)
def create_instance(req: CreateInstanceRequest) -> dict[str, Any]:
    """新建实例：写一份与 engine._load_state 首跑等价的 state JSON。

    005 需求2/5：
      - template 绑定（缺省默认模板；骨架模板 400 拒绝——引擎 run 也会拒跑）
      - materials 材料清单入 state（引擎首次派选题卡时写卡 comment）
      - mode 缺省 **live**（修复「新建静默成 test」；首跑定终身，UI 须提示）
    首次 advance 时引擎读到它，直接从模板第一步开跑。
    """
    name = _validate_name(req.name)
    fp = _instance_path(name)
    if fp.exists():
        raise HTTPException(409, f"实例已存在: {name}")
    if not ENGINE_PY.exists():
        raise HTTPException(503, f"引擎不可达: {ENGINE_PY}")

    # 模板校验：id 白名单 + 在位 + 非骨架（fail-closed，与引擎拒跑同口径）
    template = (req.template or DEFAULT_TEMPLATE).strip()
    if not TEMPLATE_ID_RE.fullmatch(template):
        raise HTTPException(400, f"非法模板 id: {template}")
    meta = _load_template_meta(template)
    if meta is None:
        raise HTTPException(
            404, f"模板不存在: {template}（可用: "
                 f"{', '.join(t['id'] for t in _list_templates())}）")
    if not meta.get("runnable"):
        raise HTTPException(
            400, f"模板 {template} 为骨架占位（未开放运行），请选择完整模板")

    # 模式校验：缺省 live（需求5）；test 须显式勾选
    mode = (req.mode or "live").strip().lower()
    if mode not in ("live", "test"):
        raise HTTPException(400, f"mode 只允许 live/test，收到: {req.mode}")

    # 材料清单：type 白名单 + 条数/长度上限（引擎侧注入卡 comment 时再截断）
    materials: list[dict[str, str]] = []
    for m in req.materials or []:
        content = (m.content or "").strip()
        mtype = (m.type or "text").strip().lower()
        if not content:
            continue  # 空条目静默丢弃（对话框前端已拦，此处兜底）
        if mtype not in MATERIAL_TYPES:
            raise HTTPException(400, f"材料 type 只允许 {MATERIAL_TYPES}，收到: {m.type}")
        if len(content) > MAX_MATERIAL_LEN:
            raise HTTPException(400, f"单条材料过长（>{MAX_MATERIAL_LEN} 字符）")
        materials.append({"type": mtype, "content": content})
    if len(materials) > MAX_MATERIALS:
        raise HTTPException(400, f"材料条数超上限（>{MAX_MATERIALS}）")

    state = {
        "instance": name,
        "step": None,           # None → 引擎 run() 自动落到模板第一步
        "iteration": 0,
        "history": [],
        "status": "idle",
        "dispatched": {},
        "mode": mode,           # 005 需求5：缺省 live（首跑定终身）
        "template": template,   # 005 需求1：实例绑定模板（定终身）
        "materials": materials,  # 005 需求2：选题卡 comment 注入源
        # C1（20260920）：立项时间戳（与 engine._load_state 新实例同口径）
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    # 生命周期耦合（2026-10-10）：实例落盘即活动实例（idle）→ 立刻对齐心跳，
    # 且必须先于 _ensure_project_folder——后者若抛错，不能留下
    # 「实例已建但心跳停着 → 永久 idle 无人首拍」的缝。
    cron_coupling.reconcile_silent()
    proj = _ensure_project_folder(name)  # 20260921：立项即建正式规则成果目录
    _log("info", "create",
         f"新建文章实例 {name}（template={template}，mode={mode}，"
         f"材料 {len(materials)} 条，成果目录 {proj}，等待首次推进）")
    return _summary(state)


def _instance_artifacts(name: str, st: dict[str, Any]) -> list[dict[str, Any]]:
    """B2（20260920）：实例详情附产物链——直接复用 ww_panel.build_artifacts
    同一函数（含 -<instance> 展示层过滤口径），保证仪表盘与前端两套 UI 一致。
    模板缺失/解析失败等异常降级空列表，不炸详情页。"""
    try:
        if str(ENGINE_DIR) not in sys.path:
            sys.path.insert(0, str(ENGINE_DIR))
        import ww_panel  # 引擎目录只读仪表模块（无写副作用）
        import yaml
        tid = st.get("template") or DEFAULT_TEMPLATE
        tpl_path = TEMPLATES_DIR / f"{tid}.yaml"
        tpl = yaml.safe_load(tpl_path.read_text(encoding="utf-8")) or {}
        return ww_panel.build_artifacts(name, st, tpl)
    except Exception as e:  # noqa: BLE001 — 产物链降级，详情主体不受影响
        _log("warn", "instance", f"{name} 产物链读取失败降级为空: {e}")
        return []


@app.get("/api/instances/{name}")
def get_instance(name: str) -> dict[str, Any]:
    """实例详情：完整 state（含 history / dispatched / uploads）+ 产物链。

    B2：artifacts 字段与 ww_panel 仪表盘同口径（按实例名后缀过滤）。
    """
    name = _validate_name(name)
    detail = _read_instance(name)
    detail["artifacts"] = _instance_artifacts(name, detail)
    return detail


@app.post("/api/instances/{name}/advance")
def advance_instance(name: str, req: AdvanceRequest) -> dict[str, Any]:
    """推进一个动作：subprocess 调 engine.py --instance <名> [--step <步>]。

    - sys.executable 保证与 API 同解释器（依赖 yaml 等可用）
    - cwd=引擎目录（engine.py 同目录 import archive/kanban_client 等）
    - 超时 300s；stdout 原样返回给前端展示
    """
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁：实例必须已存在
    step = (req.step or "").strip() or None
    if step and not STEP_RE.fullmatch(step):
        raise HTTPException(400, f"非法步骤 id: {step}")

    cmd = [sys.executable, str(ENGINE_PY), "--instance", name]
    if step:
        cmd += ["--step", step]

    t0 = time.monotonic()
    try:
        # Windows 控制台中文输出：显式 utf-8 解码，坏字节替换不炸
        r = subprocess.run(
            cmd, cwd=str(ENGINE_DIR), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=ADVANCE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _log("error", "advance",
             f"{name} 推进超时（>{ADVANCE_TIMEOUT_S}s，已终止）"
             + (f" step={step}" if step else ""))
        raise HTTPException(
            504, f"推进超时（>{ADVANCE_TIMEOUT_S}s），引擎子进程已终止") from None

    dur_ms = int((time.monotonic() - t0) * 1000)
    stdout = (r.stdout or "").strip()
    stderr = (r.stderr or "").strip()
    level = "info" if r.returncode == 0 else "error"
    _log(level, "advance",
         f"{name} rc={r.returncode} {dur_ms}ms"
         + (f" step={step}" if step else "")
         + f" → {stdout[:200] or stderr[:200] or '(无输出)'}")
    return {
        "instance": name,
        "step": step,
        "returncode": r.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": dur_ms,
    }


@app.post("/api/instances/{name}/decision")
def decide_instance(name: str, req: DecisionRequest) -> dict[str, Any]:
    """写拍板决定：落 instances/<名>.decision，引擎下一拍 advance 时消费。

    支持 `C|修改意见` 格式（门3 带意见修改路径，engine._do_decision 契约）。
    """
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁
    decision = (req.decision or "").strip()
    if not decision:
        raise HTTPException(400, "拍板内容不能为空")
    if len(decision) > 2000:
        raise HTTPException(400, "拍板内容过长（>2000 字符）")
    fp = INSTANCES_DIR / f"{name}.decision"
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    fp.write_text(decision, encoding="utf-8")
    _log("info", "decision", f"{name} 拍板已写入: {decision[:120]}")
    return {"instance": name, "decision": decision, "file": str(fp)}


# ── C2（20260920）：实例管理端点（隐藏 / 软删 / 打开成果目录）─────────


@app.post("/api/instances/{name}/reassign")
def reassign_instance(name: str, req: ReassignRequest) -> dict[str, Any]:
    """016 批次三 #10：blocked 卡手动重派——代理转发 ww_panel(8660) /reassign。

    门禁在 ww_panel 侧（实例存在 + wweng:<instance>:<step> 键空间解析 +
    卡 status=blocked）；本端点只做形态校验与转发（面板未运行 → 502 明报，
    前端提示「请先启动 ww_panel」）。
    """
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁：实例必须已存在
    step = (req.step or "").strip()
    if not step or not STEP_RE.fullmatch(step):
        raise HTTPException(400, f"非法步骤 id: {step}")
    payload = json.dumps({"instance": name, "step": step}).encode("utf-8")
    req2 = urllib.request.Request(
        f"http://127.0.0.1:{PANEL_PORT}/reassign", data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req2, timeout=150) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            detail = e.reason
        raise HTTPException(e.code, f"ww_panel 拒绝重派: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPException(
            502, f"ww_panel(8660) 不可达：{e}（请先启动 ww_panel，桌面快捷方式一键拉起）") from None
    _log("info", "reassign", f"{name} 卡 {out.get('card')} 已重派（step={step}）")
    return out


@app.post("/api/instances/{name}/hide")
def hide_instance(name: str) -> dict[str, Any]:
    """隐藏实例：创建 instances/<名>.hidden 标记文件（仿 .unattended 模式）。"""
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁
    (INSTANCES_DIR / f"{name}.hidden").write_text(
        datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    _log("info", "hide", f"{name} 已隐藏（列表默认不显示）")
    return {"instance": name, "hidden": True}


@app.post("/api/instances/{name}/unhide")
def unhide_instance(name: str) -> dict[str, Any]:
    """取消隐藏：删除 hidden 标记文件。"""
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁
    (INSTANCES_DIR / f"{name}.hidden").unlink(missing_ok=True)
    _log("info", "unhide", f"{name} 已取消隐藏")
    return {"instance": name, "hidden": False}


@app.delete("/api/instances/{name}")
def delete_instance(name: str) -> dict[str, Any]:
    """归档式软删（用户清场偏好）：实例 JSON 及同名附属文件移入
    instances/.archive/<名>-<时间戳>/，不物理删除，可在文件系统找回。
    .archive 内不参与列表（glob *.json 不递归）。"""
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = INSTANCES_DIR / ".archive" / f"{name}-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for suffix in (".json", ".decision", ".defaults.json", ".unattended", ".hidden"):
        src = INSTANCES_DIR / f"{name}{suffix}"
        if src.exists():
            shutil.move(str(src), str(dest / src.name))
            moved.append(src.name)
    _log("info", "delete", f"{name} 软删除 → {dest}（移入 {len(moved)} 件: "
                           f"{', '.join(moved)}）")
    return {"instance": name, "archived_to": str(dest), "moved": moved}


@app.post("/api/instances/{name}/open-folder")
def open_instance_folder(name: str) -> dict[str, Any]:
    """打开实例成果目录（20260921 起立项即预建正式目录，未收官也能打开）；
    预建/归档目录缺失时回退 exchange/artifacts 兜底；全不存在 404。
    os.startfile 仅 Windows。"""
    name = _validate_name(name)
    _read_instance(name)  # 404 门禁
    target = _archived_dir(name)
    if not target:
        art = EXCHANGE_DIR / "artifacts"
        target = str(art) if art.is_dir() else None
    if not target or not Path(target).exists():
        raise HTTPException(404, f"实例 {name} 无可打开的成果目录")
    opener = getattr(os, "startfile", None)
    if opener is None:
        raise HTTPException(500, "打开文件夹仅支持 Windows 桌面环境")
    opener(target)
    _log("info", "open-folder", f"{name} 已打开成果目录: {target}")
    return {"instance": name, "opened": target}


@app.get("/api/pipeline")
def get_pipeline(template: str | None = None) -> dict[str, Any]:
    """解析模板 YAML → 步骤列表。解析不了返回空列表兜底（前端显示空态）。

    005 需求1/4：
      - ?template= 指定模板（缺省默认模板 hot-topic-commentary；拍板界面按
        实例 state.template 传参取该实例的步骤链）
      - decision 步序列化 options（key/label/next 全文）——拍板界面渲染
        完整选项内容（不再只有 A/B/C/D 代号）
    """
    tid = (template or DEFAULT_TEMPLATE).strip()
    if not TEMPLATE_ID_RE.fullmatch(tid):
        return {"name": "", "title": "", "max_iterations": None,
                "steps": [], "error": f"非法模板 id: {tid}"}
    tpl_path = TEMPLATES_DIR / f"{tid}.yaml"
    try:
        import yaml  # 引擎环境标配；缺失/解析失败都走兜底
        tpl = yaml.safe_load(tpl_path.read_text(encoding="utf-8")) or {}
        steps_raw = tpl.get("steps") or []
        steps = []
        for s in steps_raw:
            if not isinstance(s, dict) or not s.get("id"):
                continue
            steps.append({
                "id": s.get("id"),
                "type": s.get("type"),
                "assignee": s.get("assignee"),
                "description": s.get("description"),
                "question": s.get("question"),
                "next": s.get("next"),
                "members": len(s.get("members") or []) or None,
                "options": [
                    {"key": o.get("key"), "label": o.get("label"), "next": o.get("next")}
                    for o in (s.get("options") or [])
                    if isinstance(o, dict)
                ] or None,
            })
        return {
            "name": tpl.get("name", ""),
            "title": tpl.get("title", ""),
            "template": tid,
            "max_iterations": tpl.get("max_iterations"),
            "steps": steps,
        }
    except Exception as e:  # noqa: BLE001 — 兜底契约：解析不了给空列表不炸面板
        _log("warn", "pipeline", f"模板 {tid} 解析失败，返回空列表: {e}")
        return {"name": "", "title": "", "template": tid,
                "max_iterations": None, "steps": []}


@app.get("/api/logs")
def get_logs() -> list[dict[str, Any]]:
    """内存日志（倒序：最新在前）。仅本 API 进程生命周期内有效。"""
    return list(reversed(LOGS))


@app.on_event("startup")
def _on_startup() -> None:
    _log("info", "api", f"ww-frontend 后端启动（端口 {API_PORT}，引擎目录 {ENGINE_DIR}）")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="warning")
