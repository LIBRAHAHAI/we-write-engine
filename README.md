# We-Write Engine — 流程驱动的 AI 内容生产流水线

**把"选题 → 素材 → 写作 → 标题 → 润色 → 审核 → 排版 → 归档"的全流程交给状态机驱动，AI 角色只负责写作本身。**

本项目是 [flow-engine](https://github.com/LIBRAHAHAI/flow-engine)（流程驱动的多智能体项目引擎）在**内容生产**领域的特化实现：一个自媒体内容流水线，由多个 AI 角色分工协作（选题侦察、写作、润色、事实核查、排版、归档），引擎心跳自动推进每个环节，人只在关键拍板点介入。

## 核心能力

- **7 种内容模板**：热点评论 / 历史叙事 / 财经分析 / 书影评论 / 短篇 / 长篇连载 / 剧本（`engine/config/templates/`）
- **写作纪律引擎**（`discipline.py` + `discipline_rules.yaml`）：把写作规范（标题纪律、吸引力检查、渲染检查）做成可执行规则，不达标自动打回
- **模型路由**（`model_router.py`）：不同环节按需路由到不同模型（选题要快、成稿要好）
- **渲染检查 / 归档**（`render_check.py` / `archive.py`）：成品与过程产物分离归档，成果区按日期组织
- **拍板面板**（`ww_panel.py` + React 前端）：流水线状态可视化 + 一键拍板
- **试点派发与巡检**（`spawn_pilots.py` / `ww_pilot_monitor.py`）：多试点并行 + 自动监察

## 仓库结构

```
engine/        写作流水线引擎（状态机 + 纪律规则 + 模型路由 + 归档）
  config/templates/   7 种内容类型流程模板
  config/skill_pool.json   角色-技能池注册表
  tests/        pytest 测试基线
ww-frontend/   拍板面板（FastAPI + React/Vite）
scripts/       心跳 wrapper / 试点派发 / 巡检 / 快照回滚
docker/        容器化运行（可选）
```

## 快速开始

依赖：Python 3.11+，一个能被脚本拉起的 CLI agent 执行环境（角色 = agent profile，见 `config/skill_pool.json`，可换成你自己的角色名）。

```bash
git clone https://github.com/LIBRAHAHAI/we-write-engine.git
cd we-write-engine

# 1. 测试基线
python -m pytest engine/tests/ -q

# 2. 启动拍板面板
python ww-frontend/launcher.py

# 3. 心跳推进流水线
python scripts/ww_heartbeat_wrapper.py
```

日常使用 = 心跳常驻（cron 每 5 分钟），成稿在拍板点等你确认后自动归档到成果区。

## 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WW_EXCHANGE_DIR` | `./exchange` | 审计链目录（产物/回执落盘处） |
| `WW_WECHAT_PROJECT_DIR` | `Wechat-project` | 成果归档根目录（按日期/项目组织） |
| `WW_ENGINE_DIR` | `<repo>/engine` | 引擎目录 |
| `WW_AGENTS_HOME` | `./agents-home` | 各角色技能/记忆根目录 |
| `HERMES_HOME` | 平台默认 | 执行者 profile 配置根 |

## 设计原则（继承自 flow-engine）

1. 流程推进、验收、打回、归档全部代码化，模型只做内容创作
2. 写作纪律做成规则引擎，"写得好不好"由可执行检查判定，不靠自评
3. 每篇成品保留完整审计链：选题依据 → 素材来源 → 各稿版本 → 审核结论
4. 成品与过程产物分离归档，publish 区永远干净

## License

MIT（见 LICENSE）。
