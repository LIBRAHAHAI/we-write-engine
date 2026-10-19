# -*- coding: utf-8 -*-
"""migrate_archive_layout.py — 成果区两层→三层布局迁移（2026-09-26 user 规则）。

旧: Wechat-project/<YYYY-MM-DD>/<NN-slug>/
新: Wechat-project/<模板名>/<YYYY-MM-DD>/<NN-slug>/

5 个既有项目全部产自默认模板 hot-topic-commentary（we-write 流水线）。
步骤: 预检(全部为日期层+meta实存) → 移动 → 修 meta.yaml 内 archive_dir 路径
→ 回读验证(find_archived 反查命中 + build_projects 可扫到) → 报告。
幂等: 目标已存在则跳过该项; 已迁移(根下无日期层目录)则空跑。
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path("Wechat-project")
TEMPLATE = "hot-topic-commentary"   # 5 个既有项目的模板归属
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def main():
    date_dirs = sorted(d for d in ROOT.iterdir() if d.is_dir() and DATE_RE.match(d.name))
    if not date_dirs:
        print("根下无日期层目录——已是三层布局（或空），无需迁移")
        return 0

    # ---- 预检 ----
    plans = []
    for dd in date_dirs:
        for proj in sorted(dd.iterdir()):
            if not proj.is_dir():
                print(f"⚠ 预检: {proj} 非目录，跳过并报告")
                continue
            if not (proj / "meta.yaml").exists():
                print(f"⚠ 预检: {proj} 缺 meta.yaml，跳过并报告（人工确认后再迁）")
                continue
            dest = ROOT / TEMPLATE / dd.name / proj.name
            plans.append((proj, dest))
    if not plans:
        print("无可迁移项目")
        return 0

    print(f"迁移计划 {len(plans)} 项 → {TEMPLATE}/ 层:")
    for src, dest in plans:
        print(f"  {src.relative_to(ROOT)}  →  {dest.relative_to(ROOT)}")

    # ---- 移动 + 修 meta ----
    moved = 0
    for src, dest in plans:
        if dest.exists():
            print(f"  跳过(目标已存在): {dest.relative_to(ROOT)}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        # 修 meta.yaml 内 archive_dir（反斜杠/正斜杠两种写法都换）
        meta = dest / "meta.yaml"
        text = meta.read_text(encoding="utf-8")
        old_rel = f"{src.parent.name}/{src.name}"
        new_rel = f"{TEMPLATE}/{src.parent.name}/{src.name}"
        fixed = text.replace(old_rel.replace("/", "\\"), new_rel.replace("/", "\\"))
        fixed = fixed.replace(old_rel, new_rel)
        if fixed != text:
            meta.write_text(fixed, encoding="utf-8")
            print(f"  ✓ 移动+meta路径已修: {dest.relative_to(ROOT)}")
        else:
            print(f"  ✓ 移动完成(meta 无需修): {dest.relative_to(ROOT)}")
        moved += 1

    # ---- 清理空日期层 ----
    for dd in date_dirs:
        if dd.exists() and not any(dd.iterdir()):
            dd.rmdir()
            print(f"  ✓ 移除空目录: {dd.name}/")

    # ---- 回读验证 ----
    print("\n=== 回读验证 ===")
    ok = True
    for src, dest in plans:
        if not (dest / "meta.yaml").exists():
            print(f"  ❌ meta 丢失: {dest}")
            ok = False
    # find_archived / build_projects 用引擎模块实测
    sys.path.insert(0, "./engine")
    import archive
    import ww_panel
    for inst in ["article-001", "article-002", "article-003", "article-4", "article-005"]:
        hit = archive.find_archived(inst)
        print(f"  find_archived({inst}): {hit.relative_to(ROOT) if hit else '未命中'}")
        if hit is None:
            ok = False
    projects = ww_panel.build_projects()
    print(f"  build_projects(): {len(projects)} 个项目")
    for p in projects:
        print(f"    - [{p['template'] or '(旧两层)'}] {p['date']}/{p['seq']}-{p['slug']} inst={p['instance']}")
    if len(projects) < len(plans):
        ok = False

    print(f"\n迁移 {moved} 项，验证 {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
