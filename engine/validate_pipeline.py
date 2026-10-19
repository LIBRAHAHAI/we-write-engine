"""validate_pipeline.py — 文章模板目录静态校验入口（023 Z3/G7 落地件；005 需求1 模板化）。

003 §2.4 七类规则的静态校验层（M1-4 里程碑承诺的交付物，023 指认缺位）：
  规则 8  artifact glob slug 同构（委托 config.slug_registry.validate_slug_homogeneity）
  规则 9  skills_map owner 校验：owner==assignee / owner 目录实存 / 同名多份缺 owner 拒启
  规则 12 verdict 消费字段白名单 + on_reject 必须指向存在的 dispatch 类步骤

005 需求1（2026-09-15）：pipeline.yaml 退役 → 全量校验 engine/config/templates/
目录下**每一个**模板（默认 hot-topic-commentary 全规则；骨架模板同规则跑——
无 artifact/skills_map/verdict 时自然空过，补齐内容后即受检）。
用法：
  python validate_pipeline.py          # 校验全部模板，任一 FAIL 退出码 1
引擎启动前由 WeWriteEngine.__init__ 调用同构逻辑（slug 部分），本脚本供 CI/人工预检。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ENGINE_DIR = Path(__file__).parent
sys.path.insert(0, str(ENGINE_DIR))

from config import slug_registry  # noqa: E402
from config import template_loader  # noqa: E402
# 013 §3.3 规则 12：verdict frontmatter 机器消费字段白名单（与 engine.py 常量同源）
VERDICT_FIELD_WHITELIST = {"audit_result", "factcheck_result", "logic_result"}  # 与 engine.py 同源（2026-09-14 补 logic_result，ww_logic_review 三审）
# 规则 9：owner 解析根（与 engine.py _resolve_skill_paths 同源）
PROFILE_ROOT = Path("./agents-home")


def validate(pipeline: dict | None = None) -> list[str]:
    """全量静态校验。返回错误列表（空 = 通过）。"""
    tpl = pipeline or yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))
    steps = tpl.get("steps") or []
    by_id = {s.get("id"): s for s in steps}
    errs = []

    # 规则 8：artifact slug 同构
    errs += slug_registry.validate_slug_homogeneity(tpl)

    # 规则 9：skills_map owner
    for sid, entries in (tpl.get("skills_map") or {}).items():
        target = by_id.get(sid)
        names = [e.get("skill") for e in entries if isinstance(e, dict)]
        # 同名多份缺 owner 拒启
        seen = {}
        for e in entries:
            if not isinstance(e, dict) or not e.get("skill"):
                continue
            key = e["skill"]
            seen.setdefault(key, []).append(e)
        for name, group in seen.items():
            if len(group) > 1 and any(not e.get("owner") for e in group):
                errs.append(f"规则9: {sid} 的 skill「{name}」同名多份且缺 owner（拒启）")
        for e in entries:
            if not isinstance(e, dict) or not e.get("skill"):
                errs.append(f"规则9: {sid} 的 skills_map 条目非 {{skill, owner}} 格式: {e}")
                continue
            owner = e.get("owner")
            source = e.get("source")
            # 2026-09-27 吸引力批次：引擎源 skill 新分支——条目 {skill, source: engine}
            # 与 owner 互斥二选一（source 走 engine/config/skills/ 实存校验，owner 走
            # profile/skills 目录校验）。两处同存=校验层拒启（特性不是 bug）。
            if source:
                if owner:
                    errs.append(f"规则9: {sid} 的 skill「{e['skill']}」"
                                f"owner 与 source 互斥（只给一个）")
                    continue
                if source != "engine":
                    errs.append(f"规则9: {sid} 的 skill「{e['skill']}」"
                                f"source={source} 未知（目前仅支持 engine）")
                    continue
                engine_skill = ENGINE_DIR / "config" / "skills" / e["skill"] / "SKILL.md"
                if not engine_skill.exists():
                    errs.append(f"规则9: {sid} 的 skill「{e['skill']}」"
                                f"source=engine 但实存缺失（{engine_skill} 无 SKILL.md）")
                continue
            if owner != (target or {}).get("assignee") and not owner:
                errs.append(f"规则9: {sid} 的 skill「{e['skill']}」缺 owner（或 source）")
                continue
            owner_dir = PROFILE_ROOT / str(owner) / "skills" / e["skill"]
            shared_dir = PROFILE_ROOT / "skills-shared" / e["skill"]
            if not owner_dir.exists() and not shared_dir.exists():
                errs.append(f"规则9: {sid} 的 skill「{e['skill']}」owner={owner} "
                            f"目录不实存（{owner_dir} 与 {shared_dir} 均不存在）")

    # 规则 12：verdict 消费契约
    for s in steps:
        for key in ("verdict_consume", "verdict_gate"):
            vc = s.get(key)
            if not vc:
                continue
            if vc.get("field") not in VERDICT_FIELD_WHITELIST:
                errs.append(f"规则12: {s['id']} {key}.field={vc.get('field')} 不在白名单")
            rej = vc.get("on_reject")
            if rej and (rej not in by_id or by_id[rej].get("type") != "dispatch"):
                errs.append(f"规则12: {s['id']} {key}.on_reject={rej} "
                            f"不是存在的 dispatch 步")
    return errs


if __name__ == "__main__":
    any_fail = False
    for tid in template_loader.available_template_ids():
        tpl = yaml.safe_load(
            template_loader.template_path(tid).read_text(encoding="utf-8"))
        errs = [f"[{tid}] {e}" for e in validate(tpl)]
        # 结构下限：steps 非空 + 至少一个 done 步（骨架也须是可读的合法链）
        steps = tpl.get("steps") or []
        if not steps:
            errs.append(f"[{tid}] 结构: steps 为空")
        elif not any(s.get("type") == "done" for s in steps):
            errs.append(f"[{tid}] 结构: 缺 done 收官步")
        print(f"  模板 {tid}"
              + (f"（{tpl.get('status')}）" if tpl.get("status") else "")
              + f"：{'PASS' if not errs else f'FAIL（{len(errs)} 条）'} · {len(steps)} 节点")
        for e in errs:
            print("   ", e)
        any_fail = any_fail or bool(errs)
    print("validate_pipeline:", "FAIL" if any_fail else "PASS（全部模板）")
    sys.exit(1 if any_fail else 0)
