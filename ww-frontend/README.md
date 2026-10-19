# ww-frontend — 写作引擎看板

we-write-engine 的 Web 面板（仿 flow-engine-frontend 模式）：React+TS+Vite+Tailwind
前端 + FastAPI 包装层后端 + 桌面启动器。引擎本身只读不改，全部操作通过
`engine.py` CLI 子进程与 `instances/` 数据文件完成。

## 启动方式

```bash
cd <repo>/ww-frontend
python launcher.py              # 一键：起三服务 + 打开 Edge 桌面窗口（1280×800）
python launcher.py --debug      # 调试模式（保留控制台，Ctrl+C 停服务）
python launcher.py --no-window  # 只起服务不开窗口
```

launcher 依次拉起三个服务（端口检测防重复，已在跑的直接复用）：

| 端口 | 服务 | 说明 |
|------|------|------|
| 8660 | ww_panel | 引擎自带仪表盘（独立进程保持运行，前端「仪表盘」页 iframe 嵌入 `http://127.0.0.1:8660/`） |
| 8670 | ww-frontend 后端 API | FastAPI 包装层（`backend/api.py`，CORS 全开） |
| 5175 | ww-frontend 前端 | `vite preview` 加载 `frontend/dist` 构建产物 |

## 后端 API（8670）

| 端点 | 说明 |
|------|------|
| `GET /api/health` | 健康检查（引擎目录可达性 / 面板地址） |
| `GET /api/instances` | 列出实例（读引擎 `instances/*.json`：instance/status/step/mode/iteration） |
| `POST /api/instances` `{name}` | 新建空白实例；名只许字母数字-，`t-` 前缀拒绝（引擎测试实例保留名） |
| `GET /api/instances/{name}` | 实例详情（完整 state，含 history） |
| `POST /api/instances/{name}/advance` `{step?}` | 推进一步：subprocess 调 `engine.py --instance <名> [--step <步>]`，超时 300s，返回 stdout |
| `POST /api/instances/{name}/decision` `{decision}` | 写拍板决定（落 `instances/<名>.decision`，支持 `C\|意见` 格式） |
| `GET /api/pipeline` | 解析 `pipeline.yaml`（当前实测 20 节点；解析不了返回空列表兜底） |
| `GET /api/logs` | 内存日志（advance / decision / create 留痕，API 重启清空） |

引擎目录写边界：只写 `instances/` 下的实例 JSON 与 `.decision` 文件（引擎自身
设计的数据通道），不修改引擎任何代码文件。

## 前端结构（frontend/，端口 5175）

- 侧边栏：主 CTA「+ 新建文章」（modal 输入文章名）+ 五区导航 + 引擎状态灯（15s 轮询 `/api/health`）
- 仪表盘：iframe 嵌 ww_panel（8660）
- 管理文章：实例卡片（状态徽章 + 当前步 + 推进 + 拍板按钮，10s 轮询）
- 流水线：全部步骤展示（类型徽章：工作步/并行组/拍板门/屏障/机器校验/收官；实测 20 节点，pipeline.yaml 头注标称 19 系后续新增逻辑审步未同步）
- 日志：advance 等操作记录（5s 轮询）
- 设置：占位（引擎连接信息 + 端口表）

## 构建

依赖 node/npm（实测 node v22.23.1 / npm 10.9.8 可用）：

```bash
cd <repo>/ww-frontend/frontend
npm install      # 首次安装依赖
npm run build    # tsc -b && vite build → 产出 dist/
```

若本机无 node/npm：`npm install && npm run build` 无法执行，launcher 启动前端
会因 `dist/` 缺失而报错，需在有 node 环境的机器上完成构建后拷贝 `dist/` 目录。

## 手动起服务（不走 launcher）

```bash
# 后端（推荐 hermes venv，已装 fastapi/uvicorn/yaml）
cd <repo>/ww-frontend/backend
<hermes-venv>/Scripts/python.exe \
  -m uvicorn api:app --host 0.0.0.0 --port 8670

# ww_panel（引擎目录，只读运行）
cd <repo>/engine
python ww_panel.py --port 8660

# 前端
cd <repo>/ww-frontend/frontend
npx vite preview --port 5175 --host
```

## 环境变量（测试隔离用，日常不用设）

| 变量 | 缺省 | 说明 |
|------|------|------|
| `WW_ENGINE_DIR` | `<repo>/engine` | 引擎目录定位 |
| `WW_INSTANCES_DIR` | `<引擎目录>/instances` | 实例目录（与 engine.py 同源语义） |
