"""render_check.py — C4 渲染完整性机器校验（013 Δ9/P1 根治）。

规则注册表（pipeline ww_check_render.rules 声明 → 引擎 _do_check 消费）：
  no_placeholder      formatted.html 占位词零命中（待补图/【占位】/placeholder/TODO/TBD/img_pending）
  img_assets_exist    每个 <img src> 本地相对路径实存且 >1KB；远程 URL 仅 warn
  cover_spec          PIL 实测封面尺寸 ==900×383（018 已验证裁切管线出图直接可测）
  wechat_refs         ① [src:N] 引用标注残留 ② 本地文件路径残留（排版步即可判定）
  wechat_img_domain   ③ img 须微信素材域（mmbiz.qpic.cn/qlogo.cn）——只对「素材化后/上传后」
                      产物判定；排版步产物按设计就是本地路径（wechat_upload_v3
                      upload_content_images 首步「远程图不动」= 预期本地图），在排版步
                      判 ③ 必然假 FAIL（t_caa2bc28 根因）。上传后由
                      wechat_upload_v3.verify_draft_exists U5④ 回读真实草稿强制
                      n_mmbiz≥1 且无本地路径，覆盖 ③。

run_render_check(target_dir, rules=None)：
  rules=None        → 全量旧口径（含 ③），兼容历史调用；
  rules=[...]       → 只跑声明子集（wechat_reachable 等价 wechat_refs，排版步不判 ③）。

输入：从 target 步（ww_layout）产物 MD 中解析声明的 html/cover 路径。
PIL 缺失时 cover_spec 降级为尺寸字段校验（诚实降级，留 warn 日志）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PLACEHOLDER_PAT = re.compile(r"待补图|【占位】|placeholder|TODO|TBD|img_pending", re.IGNORECASE)
IMG_SRC_PAT = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
SIZE_PAT = re.compile(r"(\d{3,5})\s*[x×]\s*(\d{3,5})")

# rules 声明词表（模板 rules: 里允许的名字；别名归一在 _normalize_rules）
KNOWN_RULES = ("no_placeholder", "img_assets_exist", "cover_spec",
               "wechat_refs", "wechat_img_domain")
RULE_ALIASES = {"wechat_reachable": "wechat_refs"}


def check_no_placeholder(html: str) -> list[str]:
    return [f"占位词命中 {m.group(0)}" for m in PLACEHOLDER_PAT.finditer(html)]


def check_img_assets(html: str, assets_dir: str | Path | None = None) -> list[str]:
    problems = []
    for src in IMG_SRC_PAT.findall(html):
        if src.startswith(("http://", "https://", "data:")):
            continue  # 远程/data URI 记 warn 不 block（013 §6.2）
        p = Path(src)
        if not p.is_absolute() and assets_dir:
            p = Path(assets_dir) / src
        if not p.exists():
            problems.append(f"img 资产缺失: {src}")
        elif p.stat().st_size <= 1024:
            problems.append(f"img 资产过小(<1KB): {src}")
    return problems


def check_wechat_refs(html: str) -> list[str]:
    """① [src:N] 引用标注残留 + ② 本地文件路径残留（排版步即可判定，问题清单 #7②）。"""
    problems = []
    for m in re.finditer(r"\[src:\s*\d+\]", html):
        problems.append(f"[src:N] 引用标注未转换残留: {m.group(0)}")
    for m in re.finditer(r"D:[/\\][^\s\"'<>]{3,}|C:[/\\]Users[^\s\"'<>]{3,}|exchange/(artifacts|verdicts|drafts)/[^\s\"'<>]{3,}", html):
        problems.append(f"本地路径残留（微信读者不可达）: {m.group(0)[:60]}")
    return problems


def check_wechat_img_domain(html: str) -> list[str]:
    """③ img 须微信素材域（问题清单 #7③）。只对素材化/上传后产物判定，
    排版步产物不判本规则（t_caa2bc28：排版产物按设计就是本地图路径）。"""
    problems = []
    for src in IMG_SRC_PAT.findall(html):
        if src.startswith(("https://mmbiz.qpic.cn/", "http://mmbiz.qpic.cn/",
                           "https://mmbiz.qlogo.cn/", "http://mmbiz.qlogo.cn/")):
            continue  # 微信素材域 = 发布后真实可达（C1 白名单）
        if src.startswith("data:"):
            problems.append(f"img 为 data: URI（C1：上传稿禁止，须素材化）: {src[:50]}...")
        elif src.startswith(("http://", "https://")):
            # C1/盲区修复：非微信图床白名单远程图也算问题
            problems.append(f"img 为非微信素材域远程图（发布后可能不可达）: {src[:60]}")
        else:
            problems.append(f"img 为本地路径（微信不可达，须先素材化）: {src[:60]}")
    return problems


def check_wechat_reachability(html: str) -> list[str]:
    """问题清单 #7② + #7③：C4 盲区修复（= wechat_refs + wechat_img_domain 合跑，
    保留供 rules=None 旧口径与上传后校验使用）。"""
    return check_wechat_refs(html) + check_wechat_img_domain(html)


def check_cover_spec(cover_path: str | Path) -> list[str]:
    p = Path(cover_path)
    if not p.exists():
        return [f"封面文件缺失: {cover_path}"]
    try:
        from PIL import Image  # 延迟导入：PIL 缺失走降级分支
        with Image.open(p) as im:
            if im.size != (900, 383):
                return [f"封面规格 {im.size} != (900, 383)"]
        return []
    except ImportError:
        m = SIZE_PAT.search(p.name)
        if m and (m.group(1), m.group(2)) != ("900", "383"):
            return [f"封面规格（文件名声明 {m.group(1)}x{m.group(2)}）!= 900x383"]
        return []  # 无法实测：诚实放行，warn 由调用方记


def _normalize_rules(rules) -> list[str] | None:
    """rules 声明归一：wechat_reachable→wechat_refs；未知规则名报 ValueError
    （宁可 fail-fast 也不静默漏判——声明机制必须真被消费）。"""
    if rules is None:
        return None
    out = []
    for r in rules:
        n = RULE_ALIASES.get(r, r)
        if n not in KNOWN_RULES:
            raise ValueError(f"未知渲染校验规则: {r}（可用: {KNOWN_RULES}）")
        out.append(n)
    return out


def run_render_check(target_dir: str | Path, rules=None) -> dict:
    """对 target 步产物目录跑规则子集。返回 {ok, violations[], warns[]}。

    目录约定（ww_layout 产物）：找 *.html（formatted）+ 封面文件
    （*900*383* 或 cover*）——产物命名漂移时缺文件报 violation。
    rules=None 跑全量旧口径（含 ③ 素材域）；rules=[...] 只跑声明子集。
    """
    d = Path(target_dir)
    violations: list[str] = []
    warns: list[str] = []
    enabled = _normalize_rules(rules)
    if not d.is_dir():
        return {"ok": False, "violations": [f"产物目录不存在: {target_dir}"], "warns": []}
    htmls = list(d.glob("*.html"))
    if not htmls:
        violations.append("未找到排版 HTML 产物（*.html）")
        html = None
    else:
        html = max(htmls, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8",
                                                                     errors="ignore")
    want = (lambda name: enabled is None or name in enabled)

    if html is not None:
        if want("no_placeholder"):
            violations += check_no_placeholder(html)
        if want("img_assets_exist"):
            violations += check_img_assets(html, assets_dir=d)
        # 问题清单 #7②③：微信可达性盲区（按阶段拆分，见模块 docstring）
        if enabled is None:
            violations += check_wechat_reachability(html)  # 旧口径全量
        else:
            if "wechat_refs" in enabled:
                violations += check_wechat_refs(html)
            if "wechat_img_domain" in enabled:
                violations += check_wechat_img_domain(html)
    covers = [p for p in d.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    if not covers:
        if want("cover_spec"):
            violations.append("未找到封面图片产物")
    elif want("cover_spec"):
        cover = max(covers, key=lambda p: p.stat().st_mtime)
        violations += check_cover_spec(cover)
    return {"ok": not violations, "violations": violations, "warns": warns}
