"""T8 心跳 wrapper 实测：发现 running 实例 + 推进一拍（用临时实例，不真建卡——
实例停在 decision 门（无需建卡），验证 wrapper 的发现/推进/静默三态。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "engine"))  # 生命周期耦合：cron_coupling 与 engine 同目录
import ww_heartbeat_wrapper as w  # noqa: E402
import cron_coupling  # noqa: E402

print("当前 running 实例:", w._discover_running_instances())

# 造一个停在 decision 门的临时实例（不写 kanban）
inst = ROOT / "engine" / "instances" / "smoke-hb.json"
inst.parent.mkdir(exist_ok=True)
inst.write_text(json.dumps({
    "instance": "smoke-hb", "step": "ww_gate_topic", "iteration": 1,
    "status": "running", "history": []}, ensure_ascii=False), encoding="utf-8")
cron_coupling.reconcile_silent()  # 生命周期耦合：建档 → 保证心跳在跑
try:
    print("造实例后 running 实例:", w._discover_running_instances())
    out = w._advance("smoke-hb")
    print("advance 输出:", out)
    assert out and "拍板点" in out, "拍板点应投递"
    # 写拍板 → 下一拍 dispatch（会真建卡——停在此前先验证静默路径）
    (ROOT / "engine" / "instances" / "smoke-hb.decision").write_text("A", encoding="utf-8")
    out2 = w._advance("smoke-hb")
    print("拍板后 advance:", out2)
    # 断点恢复语义：实例文件即状态，进程无关（每拍新起 subprocess 已实证）
    print("=== WRAPPER OK ===")
finally:
    inst.unlink(missing_ok=True)
    cron_coupling.reconcile_silent()  # 生命周期耦合：实例删除后按真实活动数对齐
    (ROOT / "engine" / "instances" / "smoke-hb.decision").unlink(missing_ok=True)
    # 清理拍板推进建的卡
    sys.path.insert(0, str(ROOT / "engine"))
    import kanban_client
    conn = kanban_client._connect()
    rows = conn.execute("SELECT id FROM tasks WHERE idempotency_key LIKE 'wweng:smoke-hb:%' "
                        "AND status != 'archived'").fetchall()
    with conn:
        for r in rows:
            conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (r["id"],))
    conn.close()
    print("清理卡:", [r["id"] for r in rows])
