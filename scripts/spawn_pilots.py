# -*- coding: utf-8 -*-
"""批量建 5 个模板试运行实例（mode=test）+ 低风险门预设默认 opt-in。
终稿门/收官门永不默认（模板未列 decision_defaults），会停等 user——正是试运行要验证的拍板行为。"""
import json, subprocess, os, sys
from pathlib import Path

ENGINE = Path("./engine")
INST = ENGINE / "instances"

PILOTS = {
    "fa-pilot": ("finance-analysis", {"fa_gate_topic": "A", "fa_gate_outline": "A"}),
    "hn-pilot": ("history-narrative", {"hn_gate_topic": "A", "hn_gate_outline": "A"}),
    "bm-pilot": ("book-movie-review", {"bm_gate_topic": "A", "bm_gate_outline": "A"}),
    "nl-pilot": ("novel-long", {"nl_gate_outline": "A"}),
    "sc-pilot": ("screenplay", {"sc_gate_structure": "A", "sc_gate_scene": "A"}),
}

for inst, (tpl, defaults) in PILOTS.items():
    # 1) 建实例（--status 触发首拍绑定落盘）
    r = subprocess.run([sys.executable, "engine.py", "--instance", inst, "--template", tpl,
                        "--mode", "test", "--status"],
                       cwd=ENGINE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    bound = "template" in r.stdout and tpl in r.stdout
    # 2) 写 opt-in 预设默认（20260921 user 裁定：defaults.json 开启才自动拍）
    (INST / f"{inst}.defaults.json").write_text(
        json.dumps(defaults, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{inst:10s} tpl={tpl:20s} 绑定={'OK' if bound else 'FAIL'} defaults={list(defaults)}")
    if not bound:
        print("  stdout:", r.stdout[:200], "stderr:", r.stderr[:200])
