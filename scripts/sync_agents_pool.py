#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_agents_pool.py — 把 skill_pool.json 渲染进 AGENTS.md「本项目 Skill 池」（问题清单 #3 收尾）。

背景（2026-09-27 实证）：AGENTS.md 池区段是空占位，执行 worker 读到空池后
①浪费预算瞎找 skill ②自派下游卡时编造 skill 名（duanmu 写 xuanwu-safety-audit
→ agent crash x2 → 卡 blocked）。本脚本把池落到 worker 必读位置，从根上堵住。

幂等：marker 区段整体重写（<!-- POOL:BEGIN --> … <!-- POOL:END -->）。
用法：python scripts/sync_agents_pool.py [--check]
"""
import json
import sys
from pathlib import Path

ROOT = Path(".")
POOL = ROOT / "engine/config/skill_pool.json"
AGENTS = ROOT / "AGENTS.md"

BEGIN = "<!-- POOL:BEGIN（由 scripts/sync_agents_pool.py 从 engine/config/skill_pool.json 生成，勿手改） -->"
END = "<!-- POOL:END -->"


def render() -> str:
    import yaml
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    # 步骤索引的单一事实源 = 各模板 skills_map（2026-09-27：不再依赖 pool.json 的
    # 陈旧 steps 字段——那只反映默认模板，6 新模板绑定会漏）
    by_step: dict[str, list[tuple[str, str, str]]] = {}
    tpl_dir = ROOT / "engine/config/templates"
    for tp in sorted(tpl_dir.glob("*.yaml")):
        try:
            tpl = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — 单模板坏不拖垮整表
            continue
        for sid, entries in (tpl.get("skills_map") or {}).items():
            for e in entries or []:
                if isinstance(e, dict) and e.get("skill"):
                    # 2026-09-27 吸引力批次：引擎源条目 {skill, source: engine}
                    # 与 owner 互斥二选一——池里标记「引擎源」防 worker 误读成无归属。
                    if e.get("source") == "engine":
                        owner_disp = "引擎源"
                    else:
                        owner_disp = str(e.get("owner", ""))
                    by_step.setdefault(sid, []).append(
                        (str(e["skill"]), owner_disp, ""))
    # 去重（同 step 同 skill 同 owner）
    for sid in by_step:
        seen = set()
        uniq = []
        for item in by_step[sid]:
            if item not in seen:
                seen.add(item)
                uniq.append(item)
        by_step[sid] = sorted(uniq)

    # 未绑步的备用池（pool.json 中 steps 为空者）
    unbound: list[tuple[str, str, str]] = []
    for name, meta in sorted(pool.items()):
        if not (meta.get("steps") or []):
            unbound.append((name, ", ".join(meta.get("owners") or []), meta.get("status") or ""))

    lines = [BEGIN, "",
             "**执行卡动手前只在本池内挑选 skill，且必须使用下方出现的**skill 名原文"
             "（拼错/自造名 → agent 启动即崩，2026-09-27 实证）；卡 body 已列出本步 skill 的，"
             "以卡 body 为准，不必回读本池。**", "",
             "### 按步骤索引（来源=各模板 skills_map，单一事实源）", ""]
    for st in sorted(by_step):
        entries = "；".join(f"{n}（{o}）" for n, o, _ in by_step[st])
        lines.append(f"- `{st}`: {entries}")
    lines += ["", "### 未绑步的备用池（按需挑选，非本链步骤默认依赖）", ""]
    for n, o, s in unbound:
        lines.append(f"- `{n}`（{o}）— {s}")
    lines += ["", END]
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    text = AGENTS.read_text(encoding="utf-8")
    block = render()
    if BEGIN in text and END in text:
        head = text.split(BEGIN)[0]
        tail = text.split(END, 1)[1]
        new = head + block + tail
    else:
        # 首次：插到「本项目要点」之前
        marker = "## 本项目要点"
        if marker not in text:
            print("AGENTS.md 结构意外：找不到插入锚点，人工确认")
            return 1
        new = text.replace(marker, block + "\n\n" + marker, 1)
    if new == text:
        print("AGENTS.md 池区段已是最新（无改动）")
        return 0
    if check:
        print("AGENTS.md 池区段需要刷新（--check 不改盘）")
        return 1
    AGENTS.write_text(new, encoding="utf-8")
    print(f"AGENTS.md 池区段已刷新（{len(new)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
