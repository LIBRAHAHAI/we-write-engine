#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""git_snapshot.py — we-write-engine git 版本兜底/快照工具（007 任务卡交付物，2026-09-15）。

四子命令（纯标准库，Python 3.11，Windows git-bash 可用）：

  pre     --label <名>                  派活前基线快照：工作区脏则先 commit（消息含 label），
                                        干净则跳过 commit；随后打 tag snap/pre-<名>，输出 hash。
  post    --label <名>                  交付后快照：commit 当前状态 + tag snap/post-<名>，
                                        输出 hash + 相对 snap/pre-<名> 的 diff stat 摘要。
  rollback --label <名> [--to pre|post] 硬回滚：git reset --hard <tag>；
                                        回滚前必留两道后路（stash -u + .bak 保险落盘）；
                                        tag 缺失明确报错 exit 3，禁止猜测。
  verify  --label <名>                  验收：跑 python -m pytest engine/tests/ -q；
                                        PASS → VERIFY_OK；FAIL → 自动回滚到 snap/pre-<名>，
                                        输出 VERIFY_FAIL_ROLLED_BACK + 失败摘要。

安全铁律（007 硬要求）：
  1. 只允许操作 we-write-engine 仓库：启动即校验 `git rev-parse --show-toplevel`
     恰等于项目根（本脚本所在仓库），否则拒跑 exit 2。
  2. 唯一豁免：--repo 显式指向**系统临时目录下**的仓库根（测试沙箱，tmp 仓库用例依赖）；
     其余任何路径——仓库外目标一律拒绝绝。
  3. 禁止任何远程操作是结构性的：本工具不存在 push/fetch/remote 代码路径。
  4. 破坏性操作（reset --hard）前必留两道后路：
     后路1 `git stash push -u`（WIP 原样进 stash，git stash pop 可找回）；
     后路2 <repo>/backups/gitsnap-*.bak-<sha>（回滚前 HEAD 指针 + 恢复口令落盘）。
     backups/ 由工具写入 <repo>/.git/info/exclude（仅本地生效）——保险文件既不会
     误入提交，也不会被后续回滚的 stash push -u 卷走。
  5. 幂等：同 label 重复 pre/post 不炸——tag 已存在则复用不移动并提示。
  6. 输出可审计：每条 git 命令与其结果原样上屏；关键结论以 KEY=value 形态输出。

退出码：0 成功；2 安全门拒跑；3 tag 缺失；4 git 操作失败；
        5 verify FAIL（已自动回滚）；6 label/参数非法。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent   # <repo>/scripts/ → 仓库根
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
AUDIT_MAX_LINES = 40       # 单条 git 命令输出上屏上限（超出折叠）
VERIFY_TAIL = 60           # verify 时 pytest 输出上屏尾部行数

EXIT_OK = 0
EXIT_GUARD = 2
EXIT_NO_TAG = 3
EXIT_GIT = 4
EXIT_VERIFY_FAIL = 5
EXIT_ARGS = 6


class GitSnapError(Exception):
    """带退出码的工具级错误（main 统一捕获后上屏并返回该码）。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _out(msg: str = "") -> None:
    print(msg, flush=True)


# ── git 执行层（每条命令原样上屏 = 审计留痕）────────────────────────────

def run_git(repo: Path, *args: str, check: bool = True) -> tuple[int, str, str]:
    """在 repo 内跑一条本地 git 命令；命令与结果上屏。禁止接入任何远程子命令。"""
    _out(f"$ git {' '.join(args)}    (cwd={repo})")
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    for text in (proc.stdout, proc.stderr):
        if not text.strip():
            continue
        lines = text.rstrip().splitlines()
        for line in lines[:AUDIT_MAX_LINES]:
            _out(f"  | {line}")
        if len(lines) > AUDIT_MAX_LINES:
            _out(f"  | …（其余 {len(lines) - AUDIT_MAX_LINES} 行略）")
    if check and proc.returncode != 0:
        raise GitSnapError(EXIT_GIT, f"git {' '.join(args)} 失败 (rc={proc.returncode})")
    return proc.returncode, proc.stdout, proc.stderr


def sha_of(repo: Path, rev: str) -> str | None:
    """rev 可解析则返回其 sha（tag 缺失等情形返回 None，不抛）。"""
    rc, out, _ = run_git(repo, "rev-parse", "-q", "--verify", rev, check=False)
    return out.strip() or None if rc == 0 else None


def head_sha(repo: Path) -> str:
    sha = sha_of(repo, "HEAD")
    if not sha:
        raise GitSnapError(EXIT_GIT, "HEAD 不存在（仓库零提交，先做一次提交再快照）")
    return sha


# ── 启动安全门（007 硬要求 1）─────────────────────────────────────────

def resolve_repo(repo_arg: str | None) -> Path:
    """校验目标仓库：toplevel 必须恰等于仓库根本身，且要么是本工具所在仓库，
    要么是系统临时目录下的测试沙箱。其余拒跑。"""
    repo = Path(repo_arg).resolve() if repo_arg else DEFAULT_ROOT
    if not repo.is_dir():
        _out(f"[gitsnap] ❌ 安全门：路径不存在: {repo}，拒跑。")
        raise GitSnapError(EXIT_GUARD, f"路径不存在: {repo}")
    rc, out, _ = run_git(repo, "rev-parse", "--show-toplevel", check=False)
    if rc != 0:
        _out(f"[gitsnap] ❌ 安全门：{repo} 不是 git 仓库（rev-parse rc={rc}），拒跑。")
        raise GitSnapError(EXIT_GUARD, f"不是 git 仓库: {repo}")
    toplevel = Path(out.strip()).resolve()
    sandbox = Path(tempfile.gettempdir()).resolve()
    allowed = (
        toplevel == repo
        and (repo == DEFAULT_ROOT or repo.is_relative_to(sandbox))
    )
    if not allowed:
        _out(f"[gitsnap] ❌ 安全门拒跑：toplevel={toplevel} ≠ 授权仓库 {DEFAULT_ROOT}")
        _out("[gitsnap]    本工具只允许操作 we-write-engine 仓库；")
        _out("[gitsnap]    --repo 仅豁免系统临时目录下的仓库根（测试沙箱），跨仓库一律禁止。")
        raise GitSnapError(EXIT_GUARD, f"仓库安全门未过: {toplevel}")
    _out(f"[gitsnap] ✅ 安全门放行：repo={repo}  toplevel={toplevel}")
    _ensure_backups_excluded(repo)
    return repo


def _ensure_backups_excluded(repo: Path) -> None:
    """把 backups/ 写入 <repo>/.git/info/exclude（本地排除，不改 .gitignore）：
    保险文件不随后续 stash push -u 被卷走，也不会误入提交。失败仅告警不阻断。"""
    exclude = repo / ".git" / "info" / "exclude"
    try:
        text = exclude.read_text(encoding="utf-8", errors="replace") if exclude.exists() else ""
        if "backups/" not in text:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8") as f:
                if text and not text.endswith("\n"):
                    f.write("\n")
                f.write("# gitsnap: 回滚保险目录（本地排除，勿删）\nbackups/\n")
            _out(f"[gitsnap] backups/ 已写入本地排除: {exclude}")
    except OSError as e:
        _out(f"[gitsnap] ⚠ 写 info/exclude 失败（不影响主流程，保险文件仍会落盘）: {e}")


# ── 公共动作 ─────────────────────────────────────────────────────────

def porcelain(repo: Path) -> list[str]:
    _, out, _ = run_git(repo, "status", "--porcelain")
    return [ln for ln in out.splitlines() if ln.strip()]


def commit_all_if_dirty(repo: Path, message: str) -> tuple[str, str]:
    """工作区脏则 add -A + commit（消息含 label），干净则跳过。返回 (动作, HEAD sha)。"""
    dirty = porcelain(repo)
    if not dirty:
        _out("[gitsnap] 工作区干净 → 跳过 commit")
        return "clean", head_sha(repo)
    _out(f"[gitsnap] 工作区脏（{len(dirty)} 项）→ git add -A + commit")
    run_git(repo, "add", "-A")
    if not porcelain(repo):   # 只有被忽略文件的变动等场景
        _out("[gitsnap] add 后无可提交内容（仅忽略文件变动）→ 跳过 commit")
        return "clean", head_sha(repo)
    run_git(repo, "commit", "-m", message)
    return "committed", head_sha(repo)


def ensure_tag(repo: Path, tag: str) -> tuple[str, bool]:
    """打 tag；已存在则复用不移动（幂等，007 硬要求 5）。返回 (tag 指向 sha, 是否新建)。"""
    existing = sha_of(repo, f"refs/tags/{tag}")
    if existing:
        _out(f"[gitsnap] ℹ tag 已存在，复用不移动: {tag} -> {existing}")
        return existing, False
    run_git(repo, "tag", tag)
    sha = sha_of(repo, f"refs/tags/{tag}") or ""
    _out(f"[gitsnap] tag 新建: {tag} -> {sha}")
    return sha, True


def write_bak_insurance(repo: Path, label: str, to: str, old_sha: str) -> Path:
    """后路2：破坏性操作前把回滚前 HEAD 指针 + 恢复口令落盘（007 用户偏好：留后路）。"""
    now = datetime.now()
    bak_dir = repo / "backups"
    bak_dir.mkdir(exist_ok=True)
    fp = bak_dir / f"gitsnap-{to}-{label}-{now:%Y%m%d-%H%M%S-%f}.bak-{old_sha[:7]}"
    fp.write_text("\n".join([
        "# gitsnap 回滚保险（破坏性操作前落盘）",
        f"created_at: {now.astimezone().isoformat(timespec='seconds')}",
        f"repo: {repo}",
        f"label: {label}",
        f"rolled_back_to: refs/tags/snap/{to}-{label}",
        f"HEAD_before_rollback: {old_sha}",
        f"恢复口令: git reset --hard {old_sha}    # 或 git stash pop 找回 WIP",
    ]) + "\n", encoding="utf-8")
    return fp


def do_rollback(repo: Path, label: str, to: str) -> tuple[str, str, Path]:
    """reset --hard 到 snap/<to>-<label>；前置两道后路。返回 (旧 HEAD, 新 HEAD, .bak 路径)。"""
    tag = f"snap/{to}-{label}"
    target = sha_of(repo, f"refs/tags/{tag}")
    if not target:
        raise GitSnapError(EXIT_NO_TAG, f"未找到 tag {tag}，拒绝回滚（禁止猜测）")
    old = head_sha(repo)
    ts = f"{datetime.now():%Y%m%d-%H%M%S}"
    _out("[gitsnap] 后路1：git stash push -u（WIP 原样进 stash，git stash pop 可找回）")
    run_git(repo, "stash", "push", "-u", "-m", f"gitsnap-rollback-{label}-{ts}")
    bak = write_bak_insurance(repo, label, to, old)
    _out(f"[gitsnap] 后路2：保险落盘 {bak}（回滚前 HEAD={old}）")
    run_git(repo, "reset", "--hard", tag)
    new = head_sha(repo)
    _out(f"[gitsnap] 回滚完成: {old} → {new}（= {tag} -> {target}）")
    return old, new, bak


# ── 四子命令 ─────────────────────────────────────────────────────────

def cmd_pre(repo: Path, label: str) -> int:
    action, commit = commit_all_if_dirty(repo, f"pre({label}): 派活前快照")
    tag = f"snap/pre-{label}"
    tag_sha, created = ensure_tag(repo, tag)
    if not created and commit != tag_sha:
        _out(f"[gitsnap] ⚠ 复用的 tag 指向旧基线 {tag_sha}，当前 HEAD={commit}"
             f"（tag 不随重复 pre 移动，需要新基线请换 label）")
    _out(f"PRE_COMMIT={commit}")
    _out(f"PRE_TAG={tag}")
    _out(f"PRE_TAG_SHA={tag_sha}")
    _out(f"PRE_ACTION={action}")
    return EXIT_OK


def cmd_post(repo: Path, label: str) -> int:
    action, commit = commit_all_if_dirty(repo, f"post({label}): 交付后快照")
    tag = f"snap/post-{label}"
    tag_sha, _created = ensure_tag(repo, tag)
    _out(f"POST_COMMIT={commit}")
    _out(f"POST_TAG={tag} -> {tag_sha}")
    _out(f"POST_ACTION={action}")
    pre_tag = f"snap/pre-{label}"
    if sha_of(repo, f"refs/tags/{pre_tag}"):
        _out(f"[gitsnap] 相对 {pre_tag} 的 diff stat 摘要:")
        run_git(repo, "diff", "--stat", f"{pre_tag}..HEAD")
    else:
        _out(f"DIFF_STAT=(pre tag 缺失: {pre_tag}，跳过 diff stat)")
    return EXIT_OK


def cmd_rollback(repo: Path, label: str, to: str) -> int:
    old, new, bak = do_rollback(repo, label, to)
    _out(f"ROLLBACK_FROM={old}")
    _out(f"ROLLBACK_TO={new}")
    _out(f"ROLLBACK_TAG=snap/{to}-{label}")
    _out(f"ROLLBACK_BAK={bak}")
    return EXIT_OK


def cmd_verify(repo: Path, label: str) -> int:
    pre_tag = f"snap/pre-{label}"
    if not sha_of(repo, f"refs/tags/{pre_tag}"):
        raise GitSnapError(
            EXIT_NO_TAG,
            f"缺少 {pre_tag}，verify FAIL 时将无处回滚 → 拒跑（先执行 pre --label {label}）")
    _out(f"[gitsnap] $ {Path(sys.executable).name} -m pytest engine/tests/ -q    (cwd={repo})")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "engine/tests/", "-q"],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = ((proc.stdout or "") + (proc.stderr or "")).rstrip().splitlines()
    for line in lines[-VERIFY_TAIL:]:
        _out(f"  | {line}")
    if proc.returncode == 0:
        _out("VERIFY_OK")
        return EXIT_OK
    failed = [ln for ln in lines if ln.startswith("FAILED")]
    _out(f"[gitsnap] pytest FAIL (rc={proc.returncode})，失败摘要:")
    for ln in (failed or lines[-10:]):
        _out(f"  FAIL> {ln}")
    old, new, bak = do_rollback(repo, label, "pre")
    _out("VERIFY_FAIL_ROLLED_BACK")
    _out(f"VERIFY_FROM={old}")
    _out(f"VERIFY_TO={new}")
    _out(f"VERIFY_BAK={bak}")
    return EXIT_VERIFY_FAIL


# ── 入口 ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="git_snapshot.py",
        description="we-write-engine git 版本兜底/快照工具（pre/post/rollback/verify，仅限本仓库，禁止远程）")
    ap.add_argument("--repo", default=None,
                    help="目标仓库根（缺省=本脚本所在 we-write-engine 仓库；"
                         "仅系统临时目录下的仓库根可用此参数指定，供测试沙箱）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pre", "post", "rollback", "verify"):
        p = sub.add_parser(name, help=f"007 任务卡子命令: {name}")
        p.add_argument("--label", required=True, help="快照标签（[A-Za-z0-9][A-Za-z0-9._-]*）")
        if name == "rollback":
            p.add_argument("--to", choices=["pre", "post"], default="pre",
                           help="回滚目标 tag 前缀（默认 pre）")
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    _out(f"[gitsnap] === {args.cmd} label={args.label} ===")
    try:
        if not LABEL_RE.match(args.label):
            raise GitSnapError(
                EXIT_ARGS,
                f"label 非法: {args.label!r}（需匹配 [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}）")
        repo = resolve_repo(args.repo)
        if args.cmd == "pre":
            return cmd_pre(repo, args.label)
        if args.cmd == "post":
            return cmd_post(repo, args.label)
        if args.cmd == "rollback":
            return cmd_rollback(repo, args.label, args.to)
        return cmd_verify(repo, args.label)
    except GitSnapError as e:
        _out(f"[gitsnap] ❌ {e}")
        return e.code


if __name__ == "__main__":
    sys.exit(main())
