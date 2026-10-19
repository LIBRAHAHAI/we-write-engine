#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emit_pool_doc.py — 生成总池可读版并走 exwrite 落 artifacts（问题清单 #3 收尾）。"""
import json, subprocess, pathlib

ROOT = pathlib.Path(".")
pool = json.loads((ROOT / "engine/config/skill_pool.json").read_text(encoding="utf-8"))

sec = {}
for k, v in sorted(pool.items()):
    sec.setdefault(v["status"], []).append(
        f"| {k} | {', '.join(v['owners'])} | {', '.join(v['steps']) or '—'} | {v['purpose']} |")

titles = {"已掌握": "A. 已掌握（本链产出过）", "熟练备用": "B. 熟练备用（实存未上链）",
          "备用": "C. 备用", "需学习": "D. 需学习", "缺口": "E. 缺口（待补实体）"}
body = ["# 写作引擎总 skill 索引池（问题清单 #3）", "",
        "> 合成来源：pipeline.yaml skills_map（权威骨架，12 步绑定）+ 062/063/064 三 agent 索引卡 + hermes 自报。",
        "> 状态口径沿玄武判据：已掌握=本链产出过 / 熟练备用=磁盘实存未上链 / 需学习=实存未内化 / 缺口=无实体。", ""]
for s in ("已掌握", "熟练备用", "备用", "需学习", "缺口"):
    if sec.get(s):
        body += [f"## {titles[s]}（{len(sec[s])}）", "",
                 "| skill | owner | 绑定步骤 | 用途 |", "|---|---|---|---|"] + sec[s] + [""]

tmp = ROOT / "_pool_body.md"
tmp.write_text("\n".join(body), encoding="utf-8")
r = subprocess.run(
    ["python", "exwrite.py",
     "--dir", str(ROOT / "exchange"), "--author", "hermes", "--type", "artifact",
     "--status", "done", "--slug", "ww-skill-pool",
     "--title", "写作引擎总 skill 索引池（61 条合成）"],
    capture_output=True, text=True)
print(r.stdout[-200:] or r.stderr[-200:])
# 找到骨架文件，把正文接进去
arts = sorted((ROOT / "exchange/artifacts").glob("*-ww-skill-pool*.md"))
if arts:
    skeleton = arts[-1].read_text(encoding="utf-8")
    marker = "<!-- 正文从这里开始"
    if marker in skeleton:
        merged = skeleton.split(marker)[0] + marker + " -->\n\n" + "\n".join(body)
    else:
        merged = skeleton + "\n" + "\n".join(body)
    arts[-1].write_text(merged, encoding="utf-8")
    print("merged ->", arts[-1].name, len(merged), "chars")
tmp.unlink(missing_ok=True)
