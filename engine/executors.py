"""executors.py — we-write-engine 步骤执行器框架（T3/T4/T7）。

职责边界（002 §三 T3）：执行器不替 worker 干活——dispatch 类步骤的「执行」
就是建卡派发（逻辑由卡 worker 完成），执行器框架负责：
  1. 统一的卡 body 组装（description + skill 注入 + 交付路径 + 职责边界），
     委托 engine.WeWriteEngine._step_goal 单点组装（T6 收敛后路由提示同源）；
  2. 产物路径约定：每步骤声明产物 glob，供组屏障 wait 判齐与验收 glob 依赖；
  3. 组屏障：dispatch_group 成员全齐才放行（engine._do_wait 已实现，此处
     提供声明式产物表，防 schema 与屏障漂移——test_pipeline_schema 校验对齐）；
  4. 抽库评估结论（T7）：见 EXECUTOR_LIB_EVAL。

执行器按步骤 type 分派，engine.run() 主循环通过 run_step() 调用。
"""
from __future__ import annotations

# T7 抽库评估（002 §三 T7 要求，结论落代码 + 报告双处）：
# flow-engine 与本引擎公共逻辑：kanban_client（DB 直连/重开/归档）与 exwrite 调用。
# 结论：**暂不抽库**。理由：
#   1. 两引擎 kanban 键空间已隔离（engine: vs wweng:），kanban_client 语义
#      （前缀、ORPHAN_STATUSES）与 flow 名强耦合，抽公共库需参数化 flow 前缀，
#      改动面覆盖 flow-engine 稳定代码，违背 charter「不牵连」约束；
#   2. we-write 处于 M1 阶段，接口未定型，过早抽象=过早锁定；
#   3. 复制量小（kanban_client.py 293 行可独立维护）。
# 触发重评估条件：M4 E2E 后若出现第 3 个引擎复用需求，或 kanban_client 出现
# 双头修复 >2 次，再立「公共库抽取」专项卡。
EXECUTOR_LIB_EVAL = {
    "decision": "no-extract",
    "reason": "键空间耦合+接口未定型+复制量小；M4 后三引擎复用或双头修复>2次再评估",
}


def artifact_globs(exchange_dir: str, slugs: list[str]) -> list[str]:
    """按产物 slug 约定生成屏障 artifacts glob 表（单源，防 schema 漂移）。"""
    return [f"{exchange_dir}/artifacts/*-{slug}*.md" for slug in slugs]


def run_step(eng, step: dict, state: dict) -> dict:
    """执行器分派入口：engine.run() 类型分派的薄壳（保持主循环无业务分支）。

    返回 {"next": str|None, "log": str}，语义与 engine 内部处理器一致。
    """
    t = step["type"]
    if t == "dispatch":
        return eng._do_dispatch(step, state)
    if t == "dispatch_group":
        return eng._do_dispatch_group(step, state)
    if t == "wait":
        return eng._do_wait(step, state)
    if t == "decision":
        return eng._do_decision(step, state)
    if t == "done":
        return eng._do_done(step, state)
    raise ValueError(f"未知步骤类型: {t}")
