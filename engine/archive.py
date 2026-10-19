"""archive.py — ww_done 成果归档动作（066 §8 两层目录标配方案）。

铁律对齐（AGENTS.md 成果分离）：成果区 = `Wechat-project/<YYYY-MM-DD>/<NN>-<slug>/`。
本模块**只在成果区落盘**；引擎目录零生成性文件（无缓存、无日志文件——全部结论以返回值携带）。

归档契约（066 §8 四条验收）：
  1. ww_done 归档 → `Wechat-project/<当日日期>/<NN>-<slug>/`，8 标配件全在，
     formatted.html 与 exchange 源 md5 一致（md5 校验内置于本模块，结果入返回值）。
  2. 同日第二个项目 → NN = 当日已有项目数 + 1（两位数：01 → 02）。
  3. 项目库视图两层 glob 由 ww_panel.build_projects() 承担（`Wechat-project/*/*/meta.yaml`）。
  4. 归档 + 面板运行全程引擎目录零新增生成性文件。

meta.yaml 的 `source_artifacts` 为**相对 exchange/artifacts 的可定位路径**
（如 `imgs-article-001/sources.json`；位于 artifacts 根下的仍为裸文件名）——
保证回指项可用 glob 在 exchange 下反查命中，审计链不悬空。

幂等 / 不覆盖（066 §8 + 本卡 body）：
  - 该实例已归档（任一天目录 meta.yaml 的 `instance == 本实例`）→ skipped_exists，仅 log；
  - 目标目录已存在（非本实例占用）→ 同样 skipped_exists，**绝不覆盖**已有目录。

失败降级：归档异常不阻断收官（调用方 _do_done 内 try/except 包裹），
返回 status=failed + log，由调用方记入收官简报（escalate 记录位）。

启用开关：`WW_ARCHIVE_ENABLED=0` 关闭；实例名 `t-` 前缀（测试约定，对齐 wrapper G-2 守卫）
自动关闭——防 pytest 写真实成果区。
根目录：`WW_WECHAT_PROJECT_DIR`（默认 `Wechat-project`），**每次调用读 env**
（测试可重定向，不依赖 import 时快照）。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

DEFAULT_WECHAT_PROJECT_DIR = "Wechat-project"

# 066 §8 / 063 §3.2：每项目 8 标配件（meta.yaml 最后写，其余按此序落盘）
STANDARD_FILES = ("meta.yaml", "article-final.md", "formatted.html", "preview.html",
                  "cover-900x383.png", "imgs", "sources.json", "reviews.md")


# ---------------------------------------------------------------------------
# 环境与守卫
# ---------------------------------------------------------------------------

def project_root() -> Path:
    """成果区根（每次调用读 env，测试可重定向）。"""
    return Path(os.environ.get("WW_WECHAT_PROJECT_DIR", DEFAULT_WECHAT_PROJECT_DIR))


def archive_enabled(instance: str) -> bool:
    """归档启用判定：env 关断 + t- 测试实例约定（防 pytest 写真实成果区）。"""
    if os.environ.get("WW_ARCHIVE_ENABLED", "1") == "0":
        return False
    return not str(instance).startswith("t-")


def _iso_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# 目录规则：两层路径 / NN 序号 / 已归档反查
# ---------------------------------------------------------------------------

def read_meta_field(path: Path, field: str) -> str | None:
    """从 meta.yaml 读单行字段（不引 YAML 全量解析；异常返回 None）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    m = re.search(rf"^\s*{re.escape(field)}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    v = m.group(1)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def find_archived(instance: str, root: Path | None = None) -> Path | None:
    """反查该实例已归档目录（2026-09-26 三层布局：`<模板名>/<日期>/<NN-slug>`；
    兼容旧两层 `<日期>/<NN-slug>` 与长篇 `<模板>/<小说名>/<章节名>`）。"""
    base = Path(root) if root is not None else project_root()
    metas: list[Path] = []
    for pat in ("*/*/meta.yaml", "*/*/*/meta.yaml"):
        try:
            metas += sorted(base.glob(pat))
        except OSError:
            pass
    for meta in metas:
        if read_meta_field(meta, "instance") == str(instance):
            return meta.parent
    return None


def next_seq(date_str: str, root: Path | None = None) -> int:
    """NN = 该基准目录当日已有项目数 + 1（仅计 `NN-xxx` 形目录）。
    root 可传模板层目录（三层布局）或成果区根（旧两层布局兼容）。"""
    day = (Path(root) if root is not None else project_root()) / date_str
    if not day.is_dir():
        return 1
    try:
        n = sum(1 for d in day.iterdir() if d.is_dir() and re.match(r"^\d{2}-", d.name))
    except OSError:
        return 1
    return n + 1


# ---------------------------------------------------------------------------
# slug 解析（066 §8：标题短拼音/英文 slug）
# ---------------------------------------------------------------------------

def slugify(text: str, limit: int = 24) -> str:
    """ASCII 字母数字压缩为 slug（本机无 pypinyin，中文标题走显式覆盖/实例名兜底）。"""
    s = "".join(ch for ch in str(text or "").lower() if ch.isascii() and ch.isalnum())
    return s[:limit]


def resolve_slug(instance: str, state: dict, instances_dir: Path | None,
                 title: str) -> tuple[str, str]:
    """slug 解析链（前者优先），返回 (slug, 来源说明)。

    ⚠ 中文标题的「短拼音」需显式指定（本机无 pypinyin 依赖）——优先级：
      ① env WW_ARCHIVE_SLUG（测试/运维显式）
      ② instances/<instance>.archive.json {"slug": "..."}（运营显式锁定，推荐）
      ③ state["slug"] / state["title_slug"]
      ④ 标题 ASCII 部分（英文/数字标题可用）
      ⑤ 实例名兜底（保证 slug 非空，可测）
    """
    env = (os.environ.get("WW_ARCHIVE_SLUG") or "").strip()
    if env:
        return slugify(env) or "project", "env:WW_ARCHIVE_SLUG"
    if instances_dir is not None:
        ov = Path(instances_dir) / f"{instance}.archive.json"
        if ov.exists():
            try:
                import json
                slug = slugify(json.loads(ov.read_text(encoding="utf-8")).get("slug", ""))
                if slug:
                    return slug, f"instances/{instance}.archive.json"
            except (OSError, ValueError):
                pass
    for key in ("slug", "title_slug"):
        slug = slugify(state.get(key) or "")
        if slug:
            return slug, f"state.{key}"
    slug = slugify(title)
    if slug:
        return slug, f"标题 ASCII 部分（{title[:20]}）"
    return slugify(instance) or "project", f"实例名兜底（{instance}）"


# ---------------------------------------------------------------------------
# 源定位（只读 exchange/）
# ---------------------------------------------------------------------------

def _sort_key(p: Path):
    m = re.match(r"^(\d+)-", p.name)
    try:
        mt = p.stat().st_mtime
    except OSError:
        mt = 0.0
    return (int(m.group(1)) if m else -1, mt)


def _latest(paths) -> Path | None:
    cands = [p for p in paths if p.is_file()]
    return max(cands, key=_sort_key) if cands else None


def _match_instance(names: list[Path], instance: str) -> list[Path]:
    """优先取文件名含实例名的；无命中则回退全量（单实例仓库场景）。"""
    hit = [p for p in names if instance in p.name]
    return hit or names


def _pick_cover(instance: str, artifacts: Path) -> Path | None:
    cands = [p for p in artifacts.glob("cover-900x383-*.png")]
    if not cands:
        return None
    parts = [t for t in instance.split("-") if len(t) >= 2]
    def score(p: Path) -> tuple:
        n = p.name.lower()
        s = 3 if instance.lower() in n else (2 if any(t.lower() in n for t in parts) else 1)
        return (s, _sort_key(p)[1])
    return max(cands, key=score)


def _pick_dir(instance: str, artifacts: Path, prefix: str) -> Path | None:
    cands = [p for p in artifacts.glob(f"{prefix}-*") if p.is_dir()]
    if not cands:
        return None
    parts = [t for t in instance.split("-") if len(t) >= 2]
    def score(p: Path) -> tuple:
        n = p.name.lower()
        s = 3 if instance.lower() in n else (2 if any(t.lower() in n for t in parts) else 1)
        return (s, p.stat().st_mtime)
    return max(cands, key=score)


def collect_sources(instance: str, artifacts_dir: Path, verdicts_dir: Path) -> dict:
    """定位 8 标配件的数据源（只读，不复制）。缺项以 None 表示，由调用方记 missing。"""
    a, v = Path(artifacts_dir), Path(verdicts_dir)

    def pick(pattern: str) -> Path | None:
        return _latest(_match_instance([p for p in a.glob(pattern)], instance))

    final_md = (pick("*-ww-polish*.md") or pick("*-ww-draft*.md")
                or pick("*-polish*.md") or pick("*-draft*.md"))
    html = pick("*-formatted.html")
    cover = _pick_cover(instance, a)
    imgs = _pick_dir(instance, a, "imgs")
    sources_json = None
    if imgs is not None and (imgs / "sources.json").is_file():
        sources_json = imgs / "sources.json"
    elif (a / "sources.json").is_file():
        sources_json = a / "sources.json"

    reviews = []
    for label, base, pattern in (
            ("事实核查（factcheck）", a, "*-ww-factcheck-report*.md"),
            ("合规审（audit）", v, "*-ww-audit-verdict*.md"),
            ("价值审（value）", v, "*-ww-value-verdict*.md"),
            ("逻辑审（logic）", v, "*-ww-logic-verdict*.md")):
        p = _latest(_match_instance([q for q in base.glob(pattern)], instance))
        if p is None:
            # 2026-09-26 泛化兜底：非 ww 模板（fa/hn/bm/ns/nl/sc 前缀）按通配名取
            generic = {"事实核查（factcheck）": (a, "*-factcheck*.md"),
                       "合规审（audit）": (v, "*-audit*.md"),
                       "价值审（value）": (v, "*-value*.md"),
                       "逻辑审（logic）": (v, "*-logic*.md")}[label]
            p = _latest(_match_instance([q for q in generic[0].glob(generic[1])], instance))
        if p is not None:
            reviews.append({"label": label, "path": p})

    upload_reports = [q for q in a.glob("*-upload*.md") if q.is_file()]

    return {"final_md": final_md, "html": html, "cover": cover, "imgs": imgs,
            "sources_json": sources_json, "reviews": reviews,
            "upload_reports": upload_reports}


# ---------------------------------------------------------------------------
# 生成件：article-final.md / preview.html / reviews.md / meta.yaml
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    return _FM_RE.sub("", text, count=1).lstrip("\n")


def first_h1(text: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    t = m.group(1).strip()
    return t.strip("「」\"'“”") or None


# polish 类产物把成品正文放在「## 润色稿正文」/「## 正文」小节里（artifact 头 H1 不是文章标题）
_BODY_SECTION_RE = re.compile(r"^#{2,4}[^\n]*正文[^\n]*$", re.MULTILINE)


def split_article(text: str) -> tuple[str, str]:
    """从产物 md 抽取 (文章标题, 文章正文)。

    规则：① 有「正文」小节 → 从该节起算（polish 类产物的成品正文区）；
         ② 否则按全文（draft 类产物 frontmatter 后即正文）；
         ③ 段内第一个 H1 = 文章标题，其余为正文（终稿只留单一 H1）。
    """
    body = strip_frontmatter(text or "").strip()
    m = _BODY_SECTION_RE.search(body)
    if m:
        body = body[m.end():].lstrip("\n")
    h1 = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    if h1:
        title = h1.group(1).strip().strip("「」\"'“”") or ""
        return title, body[h1.end():].lstrip("\n")
    return "", body


def build_article_final(article_body: str, title: str) -> str:
    """终稿正文（063 §3.2：polish 终稿 + 标题合并为单一 H1，去重不双 H1）。"""
    text = strip_frontmatter(article_body or "").strip()
    if not title:
        return text.rstrip() + "\n"
    return f"# {title}\n\n{text}".rstrip() + "\n"


def build_preview_html(title: str, formatted_html: str, instance: str) -> str:
    """预览页（063 §3.2：带「复制到公众号」按钮的 wrap_preview 等价物）。

    单文件自包含：内联 formatted.html 原文 + 顶部固定复制按钮（复制渲染结果富文本）。
    """
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>预览 · {title or instance}</title>
<style>
 body{{margin:0;padding:56px 12px 40px;background:#f5f5f5;
      font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;}}
 .bar{{position:fixed;top:0;left:0;right:0;height:48px;display:flex;align-items:center;gap:12px;
      padding:0 16px;background:#fff;border-bottom:1px solid #e2e2e2;z-index:9;}}
 .bar b{{font-size:14px;color:#16243a;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
 .bar button{{margin-left:auto;padding:7px 16px;border:0;border-radius:6px;background:#07c160;
      color:#fff;font-size:13px;cursor:pointer;}}
 .bar button:active{{opacity:.85;}}
 .paper{{max-width:677px;margin:0 auto;background:#fff;border-radius:8px;padding:16px;}}
 .toast{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);padding:8px 18px;
      border-radius:18px;background:rgba(0,0,0,.78);color:#fff;font-size:13px;opacity:0;
      transition:opacity .2s;pointer-events:none;}}
 .toast.on{{opacity:1;}}
</style></head><body>
<div class="bar"><b>{('预览 · ' + title) if title else ('预览 · ' + instance)}</b>
<button id="copy">复制到公众号</button></div>
<div class="paper" id="paper">
{formatted_html}
</div>
<div class="toast" id="toast">已复制，去公众号编辑器粘贴</div>
<script>
document.getElementById('copy').onclick = function () {{
  var paper = document.getElementById('paper');
  var done = function () {{
    var t = document.getElementById('toast');
    t.classList.add('on'); setTimeout(function () {{ t.classList.remove('on'); }}, 1600);
  }};
  try {{
    var range = document.createRange(); range.selectNodeContents(paper);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    var ok = document.execCommand('copy');
    sel.removeAllRanges();
    if (!ok && navigator.clipboard) {{ navigator.clipboard.writeText(paper.innerHTML).then(done); return; }}
    done();
  }} catch (e) {{ if (navigator.clipboard) navigator.clipboard.writeText(paper.innerHTML).then(done); }}
}};
</script></body></html>
"""


def _read_head(path: Path, limit: int = 6000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _verdict_line(text: str) -> str:
    """取判定值：frontmatter 的 *_result 字段优先，否则首个非空正文行。"""
    m = re.search(r"^\s*\w*_?result\s*:\s*(\S+)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    for line in strip_frontmatter(text).splitlines():
        s = line.strip()
        if s and not s.startswith(("#", ">", "|", "<!--")):
            return s[:120]
    return "(未取到判定)"


def _verdict_excerpt(text: str, limit: int = 320) -> str:
    """结论摘录：优先「结论」小节首段，否则正文首段。"""
    body = strip_frontmatter(text)
    m = re.search(r"^#{2,4}\s*[^\n]*结论[^\n]*\n+(.+?)(?:\n\s*\n|\Z)",
                  body, re.MULTILINE | re.DOTALL)
    seg = m.group(1) if m else ""
    if not seg:
        for para in re.split(r"\n\s*\n", body):
            s = para.strip()
            if s and not s.startswith(("#", ">", "|", "<!--", "**执行前自报")):
                seg = s
                break
    seg = re.sub(r"\s+", " ", seg).strip()
    return seg[:limit] + ("…" if len(seg) > limit else "")


def build_reviews_md(instance: str, entries: list[dict], title: str) -> str:
    """三审摘要合一（063 §3.2：单文件摘编，不整目录复制）。"""
    lines = ["---", f'title: "三审摘要 · {instance}"', "artifact_type: reviews",
             f"instance: {instance}", f"generated_at: \"{_iso_now()}\"",
             f"author: athena", "---", "",
             f"# 三审摘要 · {title or instance}", ""]
    if not entries:
        lines += ["> （exchange 中未找到 factcheck / audit / value / logic 审校产物）", ""]
        return "\n".join(lines)
    for e in entries:
        text = _read_head(e["path"])
        lines += [f"## {e['label']}", f"- 来源: `{e['path'].name}`",
                  f"- 判定: **{_verdict_line(text)}**",
                  f"- 摘要: {_verdict_excerpt(text)}", ""]
    lines += ["---", "", "> 本文件由 ww_done 归档动作自动摘编（066 §8）；完整审计链见引擎 "
              "exchange/artifacts 与 exchange/verdicts。", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 归档主流程
# ---------------------------------------------------------------------------

def _res(status: str, log: str, **kw) -> dict:
    out = {"status": status, "log": log, "path": None, "date": None, "seq": None,
           "slug": None, "copied": [], "missing": [], "md5_ok": None, "slug_source": None}
    out.update(kw)
    return out


def archive_instance(instance: str, state: dict | None = None,
                     artifacts_dir: Path | str | None = None,
                     verdicts_dir: Path | str | None = None,
                     instances_dir: Path | str | None = None,
                     dry_run: bool = False) -> dict:
    """ww_done 归档动作主入口。返回结构化结果（永不抛异常给收官链路）。"""
    state = state or {}
    try:
        return _archive(instance, state, artifacts_dir, verdicts_dir, instances_dir, dry_run)
    except Exception as e:  # noqa: BLE001 — 归档失败降级不阻断收官
        return _res("failed", f"归档异常降级（不阻断收官）: {type(e).__name__}: {e}")


def _archive(instance, state, artifacts_dir, verdicts_dir, instances_dir, dry_run) -> dict:
    if not archive_enabled(instance):
        return _res("disabled", "归档已关闭（WW_ARCHIVE_ENABLED=0 或 t- 测试实例）",
                    date=datetime.now().astimezone().strftime("%Y-%m-%d"))

    root = project_root()
    a = Path(artifacts_dir) if artifacts_dir else Path("exchange/artifacts")
    v = Path(verdicts_dir) if verdicts_dir else Path("exchange/verdicts")
    inst_dir = Path(instances_dir) if instances_dir else None

    # 幂等：该实例已归档 → 跳过不覆盖，仅 log（回归样本 2026-09-13/01-jiagecike）
    # 20260921：立项预建目录（meta status: created，user 需求「立项即有正式规则命名的
    # 文件夹」）→ 复用为归档目的地，成品照常落同一目录、meta 原地更新；
    # 已完成归档（status=uploaded 等）→ 维持原幂等跳过。
    hit = find_archived(instance, root)
    precreated: Path | None = None
    if hit is not None:
        meta_txt = ""
        try:
            meta_txt = (hit / "meta.yaml").read_text(encoding="utf-8",
                                                     errors="replace")[:2000]
        except OSError:
            meta_txt = ""
        if re.search(r"^status:\s*created\s*$", meta_txt, re.MULTILINE):
            precreated = hit
        else:
            return _res("skipped_exists",
                        f"实例 {instance} 已归档于 {hit}（meta.yaml instance 反查命中），"
                        f"跳过不覆盖，仅 log",
                        path=str(hit), date=hit.parent.name,
                        seq=hit.name.split("-", 1)[0], slug=hit.name.split("-", 1)[-1])

    src = collect_sources(instance, a, v)
    raw = _read_head(src["final_md"], 10 ** 8) if src["final_md"] else ""
    art_title, article_body = split_article(raw)
    title = art_title or state.get("title") or state.get("final_title") or instance
    slug, slug_source = resolve_slug(instance, state, inst_dir, title)

    if precreated is not None:
        # 预建目录：日期/序号/slug 沿用立项时的目录名（article-4 需求实证：
        # 文件夹名立项即定，不随收官 slug 漂移）
        date = precreated.parent.name
        m = re.match(r"^(\d{2})-(.+)$", precreated.name)
        seq = int(m.group(1)) if m else 1
        slug = m.group(2) if m else slug
        slug_source = "立项预建目录名"
        dest = precreated
    else:
        # 2026-09-26 user 规则：成果区三层布局 <模板名>/<写作日期>/<NN-slug>
        template = str((state or {}).get("template") or "hot-topic-commentary")
        template = template if re.fullmatch(r"[a-z0-9][a-z0-9-]*", template) else "misc"
        tpl_root = root / template
        date = datetime.now().astimezone().strftime("%Y-%m-%d")
        seq = next_seq(date, tpl_root)
        dest = tpl_root / date / f"{seq:02d}-{slug}"

        # 不覆盖：目标目录（或当日同名 slug 目录）已存在 → 跳过，仅 log
        dup = sorted((tpl_root / date).glob(f"*-{slug}")) if (tpl_root / date).is_dir() else []
        if dest.exists() or dup:
            exist = dest if dest.exists() else dup[0]
            return _res("skipped_exists",
                        f"目标目录已存在 {exist}，跳过不覆盖（仅 log）",
                        path=str(exist), date=date, seq=exist.name.split("-", 1)[0], slug=slug,
                        slug_source=slug_source)

    if dry_run:
        return _res("dry_run",
                    f"[dry-run] 将归档 {instance} → {dest}（NN={seq:02d}，slug 来源 {slug_source}）",
                    path=str(dest), date=date, seq=f"{seq:02d}", slug=slug,
                    slug_source=slug_source)

    if precreated is None:
        dest.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    missing: list[str] = []
    md5_ok: bool | None = None

    # ① article-final.md（终稿 + 标题合并）
    if src["final_md"]:
        (dest / "article-final.md").write_text(
            build_article_final(article_body, title), encoding="utf-8")
        copied.append("article-final.md")
    else:
        missing.append("article-final.md")

    # ② formatted.html（md5 与 exchange 源一致）
    if src["html"]:
        shutil.copy2(src["html"], dest / "formatted.html")
        copied.append("formatted.html")
        md5_ok = md5(src["html"]) == md5(dest / "formatted.html")
    else:
        missing.append("formatted.html")

    # ③ preview.html（wrap_preview 等价物，生成）
    if src["html"]:
        (dest / "preview.html").write_text(
            build_preview_html(title, (dest / "formatted.html").read_text(encoding="utf-8"),
                               instance), encoding="utf-8")
        copied.append("preview.html")
    else:
        missing.append("preview.html")

    # ④ cover-900x383.png
    if src["cover"]:
        shutil.copy2(src["cover"], dest / "cover-900x383.png")
        copied.append("cover-900x383.png")
    else:
        missing.append("cover-900x383.png")

    # ⑤ imgs/
    if src["imgs"]:
        shutil.copytree(src["imgs"], dest / "imgs")
        copied.append("imgs")
    else:
        missing.append("imgs")

    # ⑥ sources.json
    if src["sources_json"]:
        shutil.copy2(src["sources_json"], dest / "sources.json")
        copied.append("sources.json")
    else:
        missing.append("sources.json")

    # ⑦ reviews.md（三审摘要摘编，生成）
    (dest / "reviews.md").write_text(
        build_reviews_md(instance, src["reviews"], title), encoding="utf-8")
    copied.append("reviews.md")

    # ⑧ meta.yaml（最后写：source_artifacts 回指所有复制来源）
    source_artifacts = []
    for key in ("final_md", "html", "cover", "sources_json"):
        if src[key]:
            # 回指 = 相对 exchange/artifacts 的可定位路径：sources.json 位于 imgs-*/
            # 子目录内，若只记 .name 则回指成裸 "sources.json"（顶层 glob 反查不到）。
            try:
                rel = src[key].relative_to(a).as_posix()
            except ValueError:  # 源不在 artifacts 树内（防御）：退回文件名
                rel = src[key].name
            if rel not in source_artifacts:
                source_artifacts.append(rel)
    if src["imgs"]:
        source_artifacts.append(f"{src['imgs'].name}/")
    for e in src["reviews"]:
        source_artifacts.append(e["path"].name)
    for up in src.get("upload_reports", []):
        if up.name not in source_artifacts:
            source_artifacts.append(up.name)
    media_id = _media_id(instance, state, src)
    meta = build_meta_yaml(instance=instance, state=state, title=title, seq=seq, date=date,
                           dest=dest, source_artifacts=source_artifacts,
                           media_id=media_id, missing=missing)
    (dest / "meta.yaml").write_text(meta, encoding="utf-8")
    copied.append("meta.yaml")

    ok_files = [f for f in STANDARD_FILES if (dest / f).exists()]
    log = (f"归档 {instance} → {dest}｜标配 {len(ok_files)}/8 "
           f"{'(全齐)' if len(ok_files) == 8 else '(缺: ' + ','.join(missing) + ')'}"
           f"｜slug 来源 {slug_source}"
           + ("" if md5_ok is None else f"｜formatted.html md5 {'一致' if md5_ok else '❌不一致'}"))
    return _res("created" if len(ok_files) == 8 else "created_incomplete", log,
                path=str(dest), date=date, seq=f"{seq:02d}", slug=slug,
                slug_source=slug_source, copied=copied, missing=missing,
                md5_ok=md5_ok, standard_present=ok_files)


# ---------------------------------------------------------------------------
# meta.yaml
# ---------------------------------------------------------------------------

def md5(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _media_id(instance: str, state: dict, src: dict | None = None) -> str | None:
    """草稿箱 media_id：state.uploads 优先，其次 upload 报告产物正则提取。"""
    up = (state.get("uploads") or {}) if isinstance(state.get("uploads"), dict) else {}
    if up.get("draft_media_id"):
        return str(up["draft_media_id"])
    cands = list((src or {}).get("upload_reports") or [])
    if not cands and (src or {}).get("final_md"):
        art = src["final_md"].parent
        cands = [p for p in art.glob("*-ww-upload*.md") if instance in p.name]
    for p in cands:
        text = _read_head(p)
        for pat in (r"draft_media_id[`\"'\s:=]+(ZIm[\w-]{10,})",
                    r"(?<!thumb_)media_id[`\"'\s:=]+(ZIm[\w-]{10,})"):
            m = re.search(pat, text)
            if m:
                return m.group(1)
    return None


def build_meta_yaml(instance: str, state: dict, title: str, seq: int, date: str, dest: Path,
                    source_artifacts: list[str], media_id: str | None,
                    missing: list[str]) -> str:
    """生成 meta.yaml（066 §8：含 archived_at + source_artifacts 回指）。"""
    now = _iso_now()
    data = {
        "title": title,
        "project": "we-write-engine",
        "artifact_type": "meta",
        "status": "uploaded" if media_id else "drafted",
        "created_at": now,
        "updated_at": now,
        "author": "athena",
        "archived_at": now,
        "instance": instance,
        "final_title": title,
        "seq": f"{seq:02d}",
        "archive_dir": str(dest).replace("\\", "/"),
        "media_id": media_id or "",
        "missing_standard_files": missing,
        "source_artifacts": source_artifacts,
    }
    head = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return ("---\n" + head + "---\n\n"
            f"# {instance} 归档 meta（由 ww_done 归档动作生成，066 §8）\n"
            f"- 成果区：{str(dest).replace(chr(92), '/')}\n"
            "- source_artifacts 为复制来源的 exchange 文件名（审计链回指）。\n")


def verify_meta(path: Path) -> dict:
    """机械复核：yaml.safe_load 可解析 + 必填键在（066 §8/063 验收2 同源校验）。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}
    m = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if not m:
        return {"ok": False, "error": "frontmatter 未找到（须以 --- 起始）"}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return {"ok": False, "error": f"yaml 解析失败: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "frontmatter 非映射"}
    need = ("title", "status", "archived_at", "source_artifacts", "instance")
    miss = [k for k in need if k not in data]
    return {"ok": not miss, "error": None if not miss else f"缺键: {miss}", "data": data}
