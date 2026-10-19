"""e2e CLI 冒烟（路径自包含版）。

⚠️ 跑法（务必先读，017 verdict §三.2）：本脚本按设计会**真建卡**（wweng:smoke-e2e: 键，
一轮 7 张，跑完自归档）。本机 hermes dispatcher 常驻，建卡→归档之间存在 **spawn 窗口**
——010/011/012 三次误派工正是从这个窗口漏出去的。故强烈建议对**一次性快照库**跑，
不要对活跃库跑：

    # 1) 拷一份活跃库当快照（只读副本，dispatcher 看不见它）
    cp "%LOCALAPPDATA%/hermes/kanban/boards/multi-agents/kanban.db"  <快照路径>/kanban.db
    # 2) 重定向后跑（engine/kanban_client.resolve_db_path() 第 1 优先级就是这个 env）
    HERMES_KANBAN_DB=<快照路径>/kanban.db  python engine/tests/smoke_e2e.py

已实证 HERMES_KANBAN_DB 重定向真实生效（017 复验即以快照跑法取证，活跃库零新增）。
若确需对活跃库跑，跑完必须直查 task_events 确认无 dispatcher claim/spawn 痕迹。

2026-09-11 B2 修复配套：dispatch / dispatch_group 建卡后**留本步**，卡 status=done
才推进。故冒烟链每过一个 dispatch 步都要先 mark_done 模拟 worker 交付，再跑一拍
验证推进；同时反向验证"卡未 done 时引擎不得空跑下游"（B2 验收点）。

注：本脚本文件名不匹配 pytest 的 test_*.py，不会被 `pytest` 收集（须手工跑）；
这也是它能放心做实弹冒烟的前提。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "engine.py"
ENGINE_DIR = ENGINE.parent
INSTANCE = "smoke-e2e"
MAT_MEMBERS = ["ww_mat_news", "ww_mat_history", "ww_mat_classic",
               "ww_mat_law", "ww_mat_econ"]
sys.path.insert(0, str(ENGINE_DIR))
import kanban_client  # noqa: E402


def run(args):
    r = subprocess.run([sys.executable, str(ENGINE)] + args,
                       capture_output=True, text=True, timeout=180,
                       cwd=str(ENGINE_DIR))
    print(f"$ engine.py {' '.join(args)}")
    print(f"  rc={r.returncode} out={r.stdout.strip()[:200]!r}")
    if r.returncode != 0:
        print("  ERR:", r.stderr[-300:])
    return r


def read_state() -> dict:
    return json.loads((ENGINE_DIR / "instances" / f"{INSTANCE}.json")
                      .read_text(encoding="utf-8"))


def mark_done(step_id: str) -> int:
    """把本实例某步的卡置 done（模拟 worker 交付），返回该键下 done 卡数。

    边界：只动 wweng:smoke-e2e: 键（flow-engine 的 engine: 键空间绝不触碰）；
    不清 claim_lock/worker_pid——真 dispatcher 若已认领该卡，抢锁会诱发重复 spawn。
    返回 0 = 幂等键未命中（建卡失败/键漂移），冒烟无法继续推进。
    """
    key = f"wweng:{INSTANCE}:{step_id}"
    conn = kanban_client._connect()
    with conn:
        cur = conn.execute(
            "UPDATE tasks SET status='done', completed_at=? "
            "WHERE idempotency_key=? AND status NOT IN ('archived','done')",
            (int(time.time()), key))
        n_done = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE idempotency_key=? AND status='done'",
            (key,)).fetchone()["n"]
    conn.close()
    print(f"  [mock worker] {key} → done（本次改 {cur.rowcount} 行，现 done 卡 {n_done} 张）")
    return n_done


def main() -> int:
    fail = 0
    run(["--instance", INSTANCE, "--reset"])

    # ---- S0 选题：建卡后必须留本步（B2 核心：建卡即推进就是缺陷）----
    r = run(["--instance", INSTANCE])
    if "建卡" not in r.stdout or "卡在途，留本步等待" not in r.stdout:
        print(f"FAIL: 首拍未建卡/未留步: {r.stdout!r}"); fail += 1
    if read_state()["step"] != "ww_topic":
        print(f"FAIL: B2 建卡后应留本步 ww_topic，实际 {read_state()['step']}"); fail += 1

    # ---- 卡 done → 才推进门1 ----
    if mark_done("ww_topic") == 0:
        print("FAIL: ww_topic 卡未置 done（幂等键未命中）"); fail += 1
    r = run(["--instance", INSTANCE])
    if "ww_gate_topic" not in r.stdout:
        print(f"FAIL: 卡 done 后未推进门1: {r.stdout!r}"); fail += 1
    r = run(["--instance", INSTANCE, "--status"])
    if "ww_gate_topic" not in r.stdout:
        print("FAIL: status 未反映当前步"); fail += 1

    # ---- 门1 拍板 ----
    run(["--instance", INSTANCE, "--decision", "A"])
    r = run(["--instance", INSTANCE])
    if "拍板" not in r.stdout:
        print("FAIL: 拍板未消费"); fail += 1
    if read_state()["step"] != "ww_outline":
        print(f"FAIL: 拍板后 step={read_state()['step']} != ww_outline"); fail += 1

    # ---- S1 大纲：建卡留步 → 卡 done → 推进门2 ----
    r = run(["--instance", INSTANCE])
    if "建卡" not in r.stdout:
        print(f"FAIL: 未建 ww_outline 卡: {r.stdout!r}"); fail += 1
    if read_state()["step"] != "ww_outline":
        print(f"FAIL: B2 建卡后应留本步 ww_outline，实际 {read_state()['step']}"); fail += 1
    mark_done("ww_outline")
    r = run(["--instance", INSTANCE])
    if read_state()["step"] != "ww_gate_outline":
        print(f"FAIL: ww_outline 卡 done 后未推进门2: {r.stdout!r}"); fail += 1
    r = run(["--instance", INSTANCE])  # 门2等拍板
    if "拍板点" not in r.stdout:
        print(f"FAIL: 门2未等待拍板: {r.stdout!r}"); fail += 1

    # ---- 门2 拍板 → S2 五路并行组 ----
    run(["--instance", INSTANCE, "--decision", "A"])
    r = run(["--instance", INSTANCE])  # 消费门2 → step=ww_mat_group
    r = run(["--instance", INSTANCE])  # 执行并行组 → 真 5 卡，留本步
    if "dispatch_group" not in r.stdout:
        print(f"FAIL: 门2后未进并行组: {r.stdout!r}"); fail += 1
    if read_state()["step"] != "ww_mat_group":
        print(f"FAIL: B2 组卡未 done 应留本步，实际 {read_state()['step']}"); fail += 1
    for mid in MAT_MEMBERS:
        if mark_done(mid) == 0:
            print(f"FAIL: 成员卡 {mid} 未置 done（幂等键未命中）"); fail += 1
    r = run(["--instance", INSTANCE])  # 成员卡全 done → 放行落屏障
    if read_state()["step"] != "ww_mat_barrier":
        print(f"FAIL: 组卡全 done 后未落屏障: {r.stdout!r}"); fail += 1
    r = run(["--instance", INSTANCE])  # 屏障等产物
    if "等待" not in r.stdout and "产物" not in r.stdout:
        print(f"FAIL: 并行组后未落屏障: {r.stdout!r}"); fail += 1
    r = run(["--instance", INSTANCE, "--step", "ww_mat_barrier"])
    print("断点恢复输出:", r.stdout.strip()[:120])

    # 实例级收尾归档（wweng:smoke-e2e: 键，不碰 flow-engine 键空间）
    prefix = f"wweng:{INSTANCE}:"
    conn = kanban_client._connect()
    rows = conn.execute(
        "SELECT id, status FROM tasks WHERE idempotency_key LIKE ? AND status != 'archived'",
        (prefix + "%",)).fetchall()
    with conn:
        for r2 in rows:
            conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (r2["id"],))
    conn.close()
    print("归档本实例卡:", [c["id"] for c in rows])
    run(["--instance", INSTANCE, "--reset"])
    print("=== SMOKE", "FAIL" if fail else "ALL_OK", "===")
    return 1 if fail else 0


if __name__ == "__main__":
    # 冒烟纪律守卫（031 §四.4）：本脚本按设计真建卡（wweng:smoke-e2e:，一轮 7 张），
    # 禁止对活跃库直接跑——HERMES_KANBAN_DB 未显式指向快照即拒绝执行（exit 2）。
    if not os.environ.get("HERMES_KANBAN_DB", "").strip():
        print("[smoke_e2e] 拒跑：HERMES_KANBAN_DB 未重定向。"
              "本脚本会真建卡（一轮 7 张），请先拷快照库并重定向 env（见文件头跑法）。")
        sys.exit(2)
    sys.exit(main())
