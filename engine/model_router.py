"""model_router.py — 步骤级模型路由 + 负载禁配（T6/S5，013 Δ7）。

职责：
  1. 读 pipeline.yaml models/load_classes 段 → dispatch 卡时把目标模型注入卡 body
     （模型路由声明），执行器日志写明「步骤 X → 实际调用 provider=P model=M」。
  2. Δ7 禁配规则：步骤标 load_class: audit 时，provider ∈ deny_providers 的路由
     顺延 fallback 链首个不禁配者；全部禁配 → 拒派（返回 None，调用方不建卡）。
     每次绕行写 DENIED 日志行（S5 验收判据）。
  3. get_routing() 供日志与测试核对；resolve_routing() 返回 (routing, log) 语义结果。

禁配表是配置不是代码：后续新增风控 provider 改 pipeline.yaml load_classes 即可。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ENGINE_DIR = Path(__file__).parent
# 005 需求1：pipeline.yaml 退役，models/load_classes 段随迁默认模板
# （hot-topic-commentary）。非默认模板的 models 段路由暂不解析（当前唯一可运行
# 模板即默认模板，行为零变化；新模板转正时在此扩实例感知）。
from config import template_loader

PIPELINE_PATH = template_loader.default_template_path()


def load_models() -> dict:
    if not PIPELINE_PATH.exists():
        return {}
    tpl = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8")) or {}
    return tpl.get("models") or {}


def load_classes() -> dict:
    """读负载分类段：{audit: {deny_providers: [...], reason: str}}。"""
    if not PIPELINE_PATH.exists():
        return {}
    tpl = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8")) or {}
    return tpl.get("load_classes") or {}


def get_load_class(step_id: str) -> str | None:
    """步骤 → load_class（G2/023：成员缺省时上溯父组继承；dispatch 顶层优先）。

    解析序：dispatch 顶层 load_class → 组成员自身声明 → 所在 dispatch_group
    的组级 load_class（pipeline 注释「组级标注：成员继承」的实现化）。
    """
    if not PIPELINE_PATH.exists():
        return None
    tpl = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8")) or {}
    for s in tpl.get("steps") or []:
        if s.get("id") == step_id:
            return s.get("load_class")
        for m in s.get("members") or []:
            if m.get("id") == step_id:
                if m.get("load_class"):
                    return m["load_class"]
                # 成员未声明 → 继承父组 load_class（G2/023）
                return s.get("load_class")
    return None


def get_routing(step_id: str) -> dict | None:
    """步骤 → {provider, model}（**原始声明**，未经禁配过滤；供对拍）。"""
    models = load_models()
    cfg = models.get(step_id)
    if cfg and cfg.get("provider") and cfg.get("model"):
        return {"provider": str(cfg["provider"]), "model": str(cfg["model"])}
    return None


def resolve_routing(step_id: str) -> tuple[dict | None, str]:
    """Δ7 禁配解析：返回 (最终路由, 日志行)。

    - 未配置路由 → (None, "走 assignee profile 默认模型")
    - provider 未禁配 → 原样返回，日志无 DENIED
    - provider ∈ deny_providers → 顺延 fallback 链首个不禁配者（G1/023：fallback
      条目显式带 model 时直接用声明值，不再扫 models 段——插入顺序敏感根治；
      兼容旧格式裸 provider 名字符串，此时才走 models 段别名解析）
      + DENIED 日志
    - fallback 链全部禁配 → (None, 拒派报错日志)，调用方必须拒绝建卡（不静默降级）
    """
    r = get_routing(step_id)
    if not r:
        return None, f"步骤 {step_id} → 未配置路由，走 assignee profile 默认模型"
    lc = get_load_class(step_id)
    if not lc:
        return r, f"步骤 {step_id} → 实际调用 provider={r['provider']} model={r['model']}"
    denied = (load_classes().get(lc) or {}).get("deny_providers") or []
    if r["provider"] not in denied:
        return r, f"步骤 {step_id} → 实际调用 provider={r['provider']} model={r['model']}"
    # 禁配命中 → fallback 链绕行
    models = load_models()
    fb = models.get(step_id, {}).get("fallback") or []
    chain_log = []
    for entry in fb:
        # G1/023：显式 {provider, model} 条目直接用；裸字符串才扫 models 段
        if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
            provider = str(entry["provider"])
            candidate = {"provider": provider, "model": str(entry["model"])}
        else:
            provider = str(entry)
            candidate = None
            for sid, cfg in models.items():
                if isinstance(cfg, dict) and cfg.get("provider") == provider:
                    candidate = {"provider": provider, "model": str(cfg["model"])}
                    break
            if candidate is None:
                candidate = {"provider": provider, "model": provider}
        if provider in denied:
            chain_log.append(f"{provider} DENIED")
            continue
        log = (f"步骤 {step_id} → 实际调用 provider={candidate['provider']} "
               f"model={candidate['model']} "
               f"[model_router] {step_id}: {r['provider']} DENIED"
               f"(load_class={lc} 禁配风控) → fallback {candidate['provider']}")
        return candidate, log
    return None, (f"[model_router] {step_id}: 拒派——load_class={lc} 下 "
                  f"{r['provider']} DENIED 且 fallback 链 {chain_log or '空'} 全部禁配")


def routing_log_line(step_id: str) -> str:
    """日志行（含禁配解析）：dispatch 建卡时同步写入。"""
    return resolve_routing(step_id)[1]
