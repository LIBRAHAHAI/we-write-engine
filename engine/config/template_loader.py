"""template_loader.py — 文章模板目录加载器（005 需求1，2026-09-15）。

模板化改造：pipeline.yaml 从全局单文件退役为「模板目录 + 默认模板」——
  engine/config/templates/<template-id>.yaml   每模板一个 YAML（步骤链+各步生成方法）
  默认模板 = hot-topic-commentary（原 pipeline.yaml 原样抽取，20 节点语义零改动）

本模块是模板路径的**唯一解析点**（engine.py / validate_pipeline.py /
slug_registry.py / model_router.py / ww_panel.py 全部经此取路径，禁止各自拼路径）。

约束：
  - 纯标准库（ww_panel 为「纯标准库」自研 YAML 解析器，import 本模块不得引入
    yaml 依赖；需要 dict 的调用方自行 yaml.safe_load(read_text())）。
  - 模板 id 白名单 [a-z0-9][a-z0-9-]*（防路径穿越）。
  - 未知模板 id → 抛 ValueError（fail-closed，不静默回落默认模板跑错步骤链）。
"""
from __future__ import annotations

import re
from pathlib import Path

# engine/config/template_loader.py → 模板目录 = engine/config/templates/
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# 默认模板：实例 state 未绑定模板时的回落值（兼容 005 之前落盘的全部旧实例）
DEFAULT_TEMPLATE_ID = "hot-topic-commentary"

# 模板 id 白名单：小写字母/数字/连字符，首字符不许连字符（=文件名安全集）
TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def template_path(template_id: str) -> Path:
    """模板 id → YAML 路径。id 非法或不在目录中 → ValueError（fail-closed）。"""
    tid = str(template_id or "").strip()
    if not TEMPLATE_ID_RE.fullmatch(tid):
        raise ValueError(f"非法模板 id: {template_id!r}（白名单 [a-z0-9][a-z0-9-]*）")
    p = TEMPLATES_DIR / f"{tid}.yaml"
    if not p.exists():
        available = ", ".join(available_template_ids()) or "(无)"
        raise ValueError(
            f"模板不存在: {tid}（可用: {available}；模板目录 {TEMPLATES_DIR}）")
    return p


def default_template_path() -> Path:
    """默认模板路径（ww_panel / slug_registry / model_router / api.py 消费）。"""
    return template_path(DEFAULT_TEMPLATE_ID)


def available_template_ids() -> list[str]:
    """扫描模板目录 → 有序 id 列表（默认模板置顶，其余按文件名排序）。"""
    if not TEMPLATES_DIR.is_dir():
        return []
    ids = sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml")
                 if TEMPLATE_ID_RE.fullmatch(p.stem))
    if DEFAULT_TEMPLATE_ID in ids:
        ids.remove(DEFAULT_TEMPLATE_ID)
        ids.insert(0, DEFAULT_TEMPLATE_ID)
    return ids


def resolve_template_id(template_id: str | None,
                        state_template: str | None = None) -> str:
    """三层解析：显式指定 > 实例 state 绑定 > 默认模板。

    显式与 state 绑定冲突 → ValueError（对齐 mode Z5 fail-closed 哲学：
    不静默跑错模板）。state_template 为 None/空（旧实例）→ 落默认模板。
    """
    explicit = str(template_id or "").strip() or None
    bound = str(state_template or "").strip() or None
    if explicit and bound and explicit != bound:
        raise ValueError(
            f"模板冲突拒跑（fail-closed）：实例 state 绑定 template={bound}，"
            f"本次显式指定 {explicit}。请用与实例一致的模板续跑。")
    return explicit or bound or DEFAULT_TEMPLATE_ID
