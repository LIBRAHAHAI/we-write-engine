"""slug_registry.py — 产物 slug 注册表生成 + 同构静态校验（013 Δ8/P8）。

职责：
  1. build_registry(pipeline)：遍历全部 artifact(s) glob → 提取 slug 段集合 →
     生成分号串（EXWRITE_SLUG_REGISTRY 值），dispatch 建卡时注入卡 body 环境段。
  2. validate_slug_homogeneity(pipeline)：静态校验——每个声明 artifact 的步骤，
     glob 中 slug 段 == step id 去 ww_ 前缀转连字符（ww_mat_select → ww-mat-select）；
     不同构 → 返回错误列表（validate_pipeline 拒启，P8 命名漂移根治）。

slug 提取规则：glob 文件名段形如 `*-<slug>*.md` / `*-<slug>*-verdict*.md`，
约定 = artifacts 路径段中最后一个含 step-slug 语义的段。实现上以 glob basename
去掉前导 `*-' 与后缀 `*.md' 之间的完整串为准（如 `*-ww-factcheck-report*.md` →
`ww-factcheck-report`），校验其含期望 slug。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# 005 需求1（2026-09-15）：pipeline.yaml 已退役，缺省口径 = 默认模板
# （engine/config/templates/hot-topic-commentary.yaml），路径唯一解析点在
# template_loader（engine.py / validate_pipeline / ww_panel 同源）。
try:
    from config import template_loader  # engine/ 在 sys.path（引擎链路）
except ImportError:  # 直接脚本运行（脚本目录 = engine/config）
    import template_loader

PIPELINE_PATH = str(template_loader.default_template_path())


def _load_pipeline(path: str | None = None) -> dict:
    p = path or PIPELINE_PATH
    return yaml.safe_load(open(p, encoding="utf-8")) or {}


def _globs_of(step: dict) -> list[str]:
    out = []
    if step.get("artifact"):
        out.append(step["artifact"])
    out += step.get("artifacts") or []
    for m in step.get("members") or []:
        if isinstance(m, dict) and m.get("artifact"):
            out.append(m["artifact"])
    return out


def slug_of_glob(glob: str) -> str | None:
    """从 glob 提取 slug 串：`.../*-<slug>*.md` → `<slug>`（多星段合并）。"""
    base = glob.replace("\\", "/").rsplit("/", 1)[-1]
    m = re.match(r"\*-(.+?)\*\.md$", base)
    return m.group(1) if m else None


def build_registry(pipeline: dict | None = None) -> str:
    """全部 artifact slug → 分号串（EXWRITE_SLUG_REGISTRY 值）。"""
    tpl = pipeline or _load_pipeline()
    slugs = []
    for s in tpl.get("steps") or []:
        for g in _globs_of(s):
            slug = slug_of_glob(g)
            if slug and slug not in slugs:
                slugs.append(slug)
    return ";".join(slugs)


def expected_slug(step_id: str) -> str:
    """step id → 期望 slug（去 ww_ 前缀转连字符）。"""
    core = step_id[3:] if step_id.startswith("ww_") else step_id
    return core.replace("_", "-")


def validate_slug_homogeneity(pipeline: dict | None = None) -> list[str]:
    """Δ8 静态校验：不同构返回错误列表（空 = 通过）。

    规则：glob slug 须含期望前缀（step id 去 ww_ 转连字符）；步骤/成员显式声明
    `slug:` 字段时以声明为准（历史既定产物契约显式豁免机制，声明即文档化）。
    G3（023）：豁免作用域 = 本步声明 / 本步组成员声明（屏障的 artifacts 本就
    属于其上游组成员，屏障自身步声明也算）。不再给全局池——声明在别处、漂移处
    不可见的跨步污染杜绝（023 §三 G3 实证：ww_topic 声明可豁免 ww_write 漂移）。
    """
    tpl = pipeline or _load_pipeline()
    by_id = {s.get("id"): s for s in tpl.get("steps") or []}
    errors = []
    for s in tpl.get("steps") or []:
        sid = s.get("id", "")
        # 本步豁免池 = 本步 slug 声明 + 本步成员 slug 声明（组屏障场景）
        local_slugs = set()
        if s.get("slug"):
            local_slugs.add(s["slug"])
        for m in s.get("members") or []:
            if isinstance(m, dict) and m.get("slug"):
                local_slugs.add(m["slug"])
        # wait 屏障的 artifacts 是上游组成员产物：上游组（next 指向本步的
        # dispatch_group）的成员声明也纳入本步豁免池（作用域=直接上游，非全局）
        for other in tpl.get("steps") or []:
            if other.get("next") == sid and other.get("type") == "dispatch_group":
                for m in other.get("members") or []:
                    if isinstance(m, dict) and m.get("slug"):
                        local_slugs.add(m["slug"])
        for g in _globs_of(s):
            slug = slug_of_glob(g)
            if not slug:
                continue
            exp = expected_slug(sid)
            if not slug.startswith(exp) and exp not in slug:
                # 成员产物？找显式 slug 声明豁免（限本步/上游组作用域）
                declared = [v for v in local_slugs if slug.startswith(v)]
                if declared or s.get("slug") == slug:
                    continue  # 显式声明 = 文档化豁免（013 P8：改名权在产物 owner）
                errors.append(
                    f"{sid}: artifact slug '{slug}' 与 step id 期望 '{exp}' 不同构"
                    f"（拒启；若为历史契约请在该步/成员显式声明 slug: {slug}）")
    return errors


if __name__ == "__main__":
    errs = validate_slug_homogeneity()
    print("slug 同构校验:", "PASS" if not errs else "FAIL")
    for e in errs:
        print(" ", e)
    print("EXWRITE_SLUG_REGISTRY =", build_registry())
