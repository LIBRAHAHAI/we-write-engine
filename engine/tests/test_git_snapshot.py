"""test_git_snapshot.py — scripts/git_snapshot.py 四子命令 + 安全门用例（007 任务卡）。

全程在系统临时目录建一次性 git 仓库（--repo 沙箱豁免路径）驱动脚本子进程，
零活跃仓库接触、零远程操作；不触 dispatch/kanban（符合 conftest 实弹纪律）。
覆盖：pre 脏/干净/幂等、post diff stat、rollback 无 tag 报错（异常路径①）/
回滚还原 + 两道后路、--to post、verify PASS / FAIL 自动回滚（异常路径②）、
安全门拒跑（子目录 / 非仓库）、label 非法拒绝。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git_snapshot.py"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=True)
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一次性临时 git 仓库：main 分支 + 单个初始提交（含 user 身份，隔离全局配置）。"""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init")
    _git(r, "config", "user.email", "gitsnap-test@example.com")
    _git(r, "config", "user.name", "gitsnap-test")
    (r / "hello.txt").write_text("v1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    return r


def run_tool(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """以 --repo=<临时沙箱> 驱动脚本子进程（绝不省略 --repo，绝不指向真实仓库）。"""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=600,
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _tag_sha(repo: Path, tag: str) -> str:
    return _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").strip()


# ── pre ──────────────────────────────────────────────────────────────

def test_pre_dirty_commits_and_tags(repo):
    (repo / "hello.txt").write_text("v2 dirty\n", encoding="utf-8")
    (repo / "extra.txt").write_text("new\n", encoding="utf-8")
    p = run_tool(repo, "pre", "--label", "t1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "安全门放行" in p.stdout
    assert "PRE_COMMIT=" in p.stdout
    assert _git(repo, "log", "-1", "--format=%s").strip() == "pre(t1): 派活前快照"
    assert _tag_sha(repo, "snap/pre-t1") == _head(repo)
    assert _head(repo) in p.stdout


def test_pre_clean_repo_skips_commit_only_tags(repo):
    before = _head(repo)
    count_before = int(_git(repo, "rev-list", "--count", "HEAD").strip())
    p = run_tool(repo, "pre", "--label", "t2")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "跳过 commit" in p.stdout
    assert int(_git(repo, "rev-list", "--count", "HEAD").strip()) == count_before  # 零新增提交
    assert _tag_sha(repo, "snap/pre-t2") == before


def test_pre_idempotent_reuses_existing_tag(repo):
    p1 = run_tool(repo, "pre", "--label", "t3")
    assert p1.returncode == 0, p1.stdout + p1.stderr
    tag_sha = _tag_sha(repo, "snap/pre-t3")
    # 复跑时带脏改动：工作必须被 commit 保住，但 tag 复用不移动
    (repo / "hello.txt").write_text("v3 after first pre\n", encoding="utf-8")
    p2 = run_tool(repo, "pre", "--label", "t3")
    assert p2.returncode == 0, p2.stdout + p2.stderr
    assert "复用" in p2.stdout and "不移动" in p2.stdout
    assert _tag_sha(repo, "snap/pre-t3") == tag_sha
    assert _head(repo) != tag_sha          # 脏改动产生了新提交
    assert "v3 after first pre" in (repo / "hello.txt").read_text(encoding="utf-8")


# ── post ─────────────────────────────────────────────────────────────

def test_post_commits_tags_and_diffstat_relative_to_pre(repo):
    run_tool(repo, "pre", "--label", "t4")
    (repo / "hello.txt").write_text("changed for post\n", encoding="utf-8")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    p = run_tool(repo, "post", "--label", "t4")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "POST_COMMIT=" in p.stdout
    assert _tag_sha(repo, "snap/post-t4") == _head(repo)
    assert "snap/post-t4" in p.stdout
    # diff stat 摘要（相对 pre）含改动文件名
    assert "hello.txt" in p.stdout and "added.txt" in p.stdout


# ── rollback ─────────────────────────────────────────────────────────

def test_rollback_missing_tag_fails_explicit(repo):
    """异常路径①：无 tag 明确报错退出非零，禁止猜测，且仓库未被碰。"""
    before = _head(repo)
    p = run_tool(repo, "rollback", "--label", "nosuch")
    assert p.returncode == 3, p.stdout + p.stderr
    assert "未找到 tag snap/pre-nosuch" in p.stdout
    assert _head(repo) == before
    assert list((repo / "backups").glob("gitsnap-*")) == []   # 未落任何保险文件


def test_rollback_restores_pre_state_and_leaves_two_fallbacks(repo):
    run_tool(repo, "pre", "--label", "t6")
    old_head = _head(repo)
    (repo / "hello.txt").write_text("wip change\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("wip untracked\n", encoding="utf-8")
    p = run_tool(repo, "rollback", "--label", "t6")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "v1\n"   # 已还原
    assert not (repo / "untracked.txt").exists()                         # WIP 已让位
    assert "gitsnap-rollback-t6" in _git(repo, "stash", "list")          # 后路1: stash
    baks = list((repo / "backups").glob("gitsnap-pre-t6-*.bak-*"))       # 后路2: .bak
    assert len(baks) == 1
    assert old_head in baks[0].read_text(encoding="utf-8")
    assert _head(repo) == old_head
    assert f"ROLLBACK_TO={old_head}" in p.stdout


def test_rollback_insurance_files_survive_next_rollback(repo):
    """上一轮回滚的 .bak 保险不被下一轮回滚的 stash push -u 卷走（info/exclude 免疫）。"""
    run_tool(repo, "pre", "--label", "t10")
    (repo / "hello.txt").write_text("wip A\n", encoding="utf-8")
    p1 = run_tool(repo, "rollback", "--label", "t10")
    assert p1.returncode == 0, p1.stdout + p1.stderr
    assert len(list((repo / "backups").glob("gitsnap-*.bak-*"))) == 1
    (repo / "hello.txt").write_text("wip B\n", encoding="utf-8")
    p2 = run_tool(repo, "rollback", "--label", "t10")
    assert p2.returncode == 0, p2.stdout + p2.stderr
    assert len(list((repo / "backups").glob("gitsnap-*.bak-*"))) == 2  # 两份保险都在盘上
    assert "backups" not in _git(repo, "stash", "list")                 # 保险未进 stash


def test_rollback_to_post_tag(repo):
    run_tool(repo, "pre", "--label", "t7")
    (repo / "hello.txt").write_text("post state\n", encoding="utf-8")
    run_tool(repo, "post", "--label", "t7")
    post_head = _head(repo)
    (repo / "hello.txt").write_text("regression after post\n", encoding="utf-8")
    p = run_tool(repo, "rollback", "--label", "t7", "--to", "post")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "post state\n"
    assert _head(repo) == post_head


# ── verify ───────────────────────────────────────────────────────────

def test_verify_requires_pre_tag(repo):
    p = run_tool(repo, "verify", "--label", "nopre")
    assert p.returncode == 3, p.stdout + p.stderr
    assert "缺少 snap/pre-nopre" in p.stdout


def test_verify_pass(repo):
    run_tool(repo, "pre", "--label", "t8")
    tests_dir = repo / "engine" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    p = run_tool(repo, "verify", "--label", "t8")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "VERIFY_OK" in p.stdout


def test_verify_fail_auto_rolls_back(repo):
    """异常路径②：pytest FAIL → 自动回滚到 snap/pre-<label>，工作树还原。"""
    run_tool(repo, "pre", "--label", "t9")
    pre_head = _head(repo)
    tests_dir = repo / "engine" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    p = run_tool(repo, "verify", "--label", "t9")
    assert p.returncode == 5, p.stdout + p.stderr
    assert "VERIFY_FAIL_ROLLED_BACK" in p.stdout
    assert "test_bad" in p.stdout                     # 失败摘要含用例名
    assert not (tests_dir / "test_bad.py").exists()   # 已自动回滚（test_bad.py 随 WIP 进 stash）
    assert _head(repo) == pre_head


# ── 安全门 / 参数 ─────────────────────────────────────────────────────

def test_guard_refuses_repo_subdirectory(repo):
    sub = repo / "sub"
    sub.mkdir()
    p = run_tool(sub, "pre", "--label", "x")
    assert p.returncode == 2, p.stdout + p.stderr
    assert "安全门" in p.stdout
    assert "拒跑" in p.stdout


def test_guard_refuses_non_repo_path(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    p = run_tool(plain, "pre", "--label", "x")
    assert p.returncode == 2, p.stdout + p.stderr
    assert "不是 git 仓库" in p.stdout


def test_invalid_label_rejected(repo):
    p = run_tool(repo, "pre", "--label", "bad label!")
    assert p.returncode == 6, p.stdout + p.stderr
    assert "label 非法" in p.stdout
