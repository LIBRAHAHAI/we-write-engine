#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_skill_pool.py — 合成总写作 skill 索引池（问题清单 #3）。

骨架 = 默认模板 hot-topic-commentary.yaml skills_map（步骤绑定，权威；005 模板化后路径随迁）；
 flesh = 三 agent 索引卡（062/063/064）表格行 + hermes 自报。
输出 engine/config/skill_pool.json + 可读版 exchange/artifacts 由 exwrite 另落。
"""
import pathlib, re, yaml, json

ROOT = pathlib.Path(".")
A = ROOT / "exchange/artifacts"
pool = {}

def add(name, owner, status, purpose="", step=""):
    name = name.strip()
    if not re.fullmatch(r"[a-z][a-z0-9\-_/]{3,60}", name):
        return
    if name not in pool:
        pool[name] = {"owners": [], "status": status, "purpose": purpose[:70], "steps": []}
    if owner not in pool[name]["owners"]:
        pool[name]["owners"].append(owner)
    if step and step not in pool[name]["steps"]:
        pool[name]["steps"].append(step)
    if status == "需学习" and pool[name]["status"] not in ("缺口",):
        pool[name]["status"] = status

# 1) skills_map 骨架
pipe = yaml.safe_load(open(ROOT / "engine/config/templates/hot-topic-commentary.yaml", encoding="utf-8"))
for step, skills in (pipe.get("skills_map") or {}).items():
    for s in skills:
        add(s["skill"], s["owner"], "已掌握", "", step)

# 2) 三 agent 索引表格
def parse_table(t, agent):
    for line in t.splitlines():
        m = re.match(r"^\|\s*`?([a-z][a-z0-9\-_/]{3,60})`?\s*\|", line)
        if m:
            cells = [c.strip().strip('*`') for c in line.split("|")[1:-1]]
            if len(cells) >= 2:
                status = "熟练备用"
                for kw in ("已掌握", "需学习", "缺口", "备用"):
                    if kw in line:
                        status = kw
                        break
                add(cells[0], agent, status, cells[1])

for f, agent in [("062-duanmu-duanmu-ww-skill-index.md", "duanmu"),
                 ("063-athena-athena-skill-index-panel-design.md", "athena"),
                 ("064-xuanwu-ww-skill-index.md", "xuanwu")]:
    parse_table((A / f).read_text(encoding="utf-8"), agent)

# 3) hermes 自报
add("hermes-logic-review", "hermes", "已掌握", "三层逻辑审（L1框架/L2自洽/L3推理链）")
add("baokuan-title-generator", "hermes", "缺口", "爆款标题生成（实体缺失待补，ww_headline 绑定）")

by = {}
for k, v in pool.items():
    by.setdefault(v["status"], []).append(k)
print("总池:", len(pool), {s: len(ks) for s, ks in by.items()})
out = ROOT / "engine/config/skill_pool.json"
out.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved ->", out)
