#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ww_pilot_monitor.py — 写作引擎实例首跑监控（athena, 2026-09-26）。

跟踪一个实例的实时进展并诊断：
  1. 实例状态（step/iter/status/template/mode）
  2. 当前 kanban 卡（id/assignee/status/heartbeat 新鲜度）+ 悬挂告警
  3. 产物落盘清单（artifacts + verdicts，按实例名匹配）
  4. history 最近 5 步
  5. 当前步类型研判：dispatch=等卡 / decision=等拍板 / done=完成
  6. 诊断结论（正常推进 / 等拍板 / 疑似悬挂 / 已完成）

用法：
  python ww_pilot_monitor.py                      # 默认监控 novel-pilot
  python ww_pilot_monitor.py --instance article-4
  python ww_pilot_monitor.py --stale-min 8        # 自定义悬挂阈值(分钟)
退出码：0=正常/等拍板/完成，2=疑似悬挂或异常（供 cron 告警门控）
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

PROJECT = Path(".")
INSTANCES = PROJECT / "engine" / "instances"
ARTIFACTS = PROJECT / "exchange" / "artifacts"
VERDICTS = PROJECT / "exchange" / "verdicts"
TEMPLATES = PROJECT / "engine" / "config" / "templates"

CARD_RE = re.compile(r"建卡\s+(t_[a-f0-9]+)")
HB_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\][^\n]*heartbeat")


def sh(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"(命令异常: {e})"


def load_instance(name):
    p = INSTANCES / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_template(tid):
    p = TEMPLATES / f"{tid}.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def current_card(st):
    for h in reversed(st.get("history") or []):
        m = CARD_RE.search(h.get("log") or "")
        if m:
            return m.group(1)
    return None


def card_info(card_id):
    if not card_id:
        return None
    out = sh(f"hermes kanban show {card_id} 2>&1")
    g = lambda pat: (re.search(pat, out).group(1) if re.search(pat, out) else None)
    hbs = HB_RE.findall(out)
    return {"id": card_id, "status": g(r"status:\s+(\w+)"),
            "assignee": g(r"assignee:\s+(\w+)"),
            "last_hb": hbs[-1] if hbs else None}


def hb_age_min(last_hb):
    if not last_hb:
        return None
    try:
        return (datetime.now() - datetime.strptime(last_hb, "%Y-%m-%d %H:%M")).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return None


def artifacts_for(name):
    hits = []
    for d in (ARTIFACTS, VERDICTS):
        if d.is_dir():
            hits += [f.name for f in d.glob("*.md") if name in f.name]
    return sorted(hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="novel-pilot")
    ap.add_argument("--stale-min", type=int, default=10)
    a = ap.parse_args()

    st = load_instance(a.instance)
    if not st:
        print(f"实例 {a.instance} 不存在")
        return 2

    tid = st.get("template")
    tpl = load_template(tid)
    steps = {s["id"]: s for s in (tpl.get("steps") or [])}
    cur = st.get("step")
    cur_type = steps.get(cur, {}).get("type", "?")

    print(f"=== {a.instance} 首跑监控 @ {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    print(f"模板: {tid} | 模式: {st.get('mode')} | 状态: {st.get('status')} | 迭代: {st.get('iteration')}")
    print(f"当前步: {cur} (type={cur_type})")

    ci = card_info(current_card(st))
    ok = True
    if ci:
        age = hb_age_min(ci["last_hb"])
        age_s = f"{age:.0f}分钟前" if age is not None else "无heartbeat"
        print(f"当前卡: {ci['id']} → {ci['assignee']} | status={ci['status']} | heartbeat={ci['last_hb']}({age_s})")
        if ci["status"] == "running" and age is not None and age > a.stale_min:
            print(f"  ⚠ 疑似悬挂：running 但 heartbeat 已 {age:.0f} 分钟未跳（阈值 {a.stale_min}）")
            ok = False
    else:
        print("当前卡: (history 无建卡记录)")

    arts = artifacts_for(a.instance)
    print(f"产物落盘({len(arts)}): {arts if arts else '(尚无)'}")

    hist = st.get("history") or []
    print(f"history 最近 {min(5, len(hist))} 步:")
    for h in hist[-5:]:
        print(f"  [{h.get('step')}] {h.get('action')}: {(h.get('log') or h.get('decision') or '')[:70]}")

    print("=== 诊断 ===")
    if st.get("status") == "completed" or cur_type == "done":
        print("✓ 实例已完成")
    elif cur_type == "decision":
        default = (tpl.get("decision_defaults") or {}).get(cur)
        opt = ("已开opt-in" if (INSTANCES / f"{a.instance}.defaults.json").exists()
               or (INSTANCES / f"{a.instance}.unattended").exists() else "未开opt-in(停等user)")
        print(f"◆ 停在拍板门 {cur}：模板默认={default or '无(永不默认)'} | 实例{opt}")
        print("  → 等 user 拍板（或开 opt-in 让低风险门自动过）")
    elif cur_type == "dispatch" and ci and ci["status"] in ("ready", "running"):
        print(f"✓ 正常推进：{ci['assignee']} 正在跑 {cur}（卡 {ci['status']}）")
    elif cur_type == "dispatch" and ci and ci["status"] == "done":
        print("✓ 卡已 done，等心跳下一拍推进")
    else:
        print(f"? 状态待研判：step={cur} type={cur_type} card={ci}")

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
