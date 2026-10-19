/**
 * 类型契约 —— 与 backend/api.py 端点返回一一对应
 */

/** 实例状态（引擎 state.status；未知状态由徽章兜底显示原文） */
export type InstanceStatus = 'idle' | 'running' | 'completed' | 'failed' | string

/** 实例列表卡片字段（GET /api/instances） */
export interface InstanceSummary {
  instance: string
  status: InstanceStatus
  step: string | null
  mode: string | null
  iteration: number | null
  /** 005：绑定的文章模板 id（旧实例回填默认模板） */
  template?: string | null
  /** C2：立项时间（ISO；存量实例后端回退实例文件创建时间） */
  created_at?: string | null
  /** C2：完成时间（ISO；未完成/未归档为 null → 前端显示 —） */
  completed_at?: string | null
  /** C2：成果区目录（20260921 起立项即预建 <日期>/<NN>-<slug>/，收官后原地落成品） */
  archived_dir?: string | null
  /** C2：隐藏标记（列表默认不显示） */
  hidden?: boolean
  /** 016 #10：当前步 kanban 卡 blocked（推进置灰 + 显示「重派」按钮） */
  blocked?: boolean
  /** 016 #10：blocked 卡 id（重派请求透传给 ww_panel 门禁复验） */
  blocked_card?: string | null
}

/** history 条目（引擎 state.history，字段按 action 而异，全可选） */
export interface HistoryEntry {
  step?: string
  action?: string
  log?: string
  decision?: string
  iteration?: number
  detail?: string
}

/** 实例详情（GET /api/instances/{name}，完整 state） */
export interface InstanceDetail extends InstanceSummary {
  history?: HistoryEntry[]
  dispatched?: Record<string, number>
  uploads?: Record<string, unknown>
  /** 005：写作材料清单（引擎注入选题卡 comment） */
  materials?: MaterialItem[]
}

/** 推进结果（POST /api/instances/{name}/advance） */
export interface AdvanceResult {
  instance: string
  step: string | null
  returncode: number
  stdout: string
  stderr: string
  duration_ms: number
}

/** 拍板选项（decision 步序列化；005 需求4：全文渲染） */
export interface DecisionOption {
  key: string
  label?: string | null
  next?: string | null
}

/** 流水线步骤（GET /api/pipeline） */
export interface PipelineStep {
  id: string
  type: string
  assignee?: string | null
  description?: string | null
  question?: string | null
  next?: string | null
  members?: number | null
  options?: DecisionOption[] | null
}

/** 流水线信息（GET /api/pipeline?template=） */
export interface PipelineInfo {
  name: string
  title: string
  template?: string
  max_iterations: number | null
  steps: PipelineStep[]
  error?: string
}

/** 日志条目（GET /api/logs，后端内存日志） */
export interface LogEntry {
  timestamp: string
  level: string
  source: string
  message: string
}

/** 健康检查（GET /api/health） */
export interface HealthInfo {
  status: string
  engine_dir: string
  engine_py_exists: boolean
  pipeline_exists: boolean
  panel_url: string
  time: string
}

/** 005 需求1：文章模板（GET /api/templates） */
export interface TemplateInfo {
  id: string
  title: string
  description: string
  /** complete | skeleton（骨架占位未开放） */
  status: string
  runnable: boolean
  is_default: boolean
  step_count: number | null
}

/** 005 需求2：写作材料条目（打字文本 / 网址 / 文件路径 / 文件夹路径） */
export interface MaterialItem {
  type: 'text' | 'url' | 'file' | 'folder'
  content: string
}

/** 005 需求2/5：新建文章参数 */
export interface CreateArticlePayload {
  name: string
  template?: string
  mode?: 'live' | 'test'
  materials?: MaterialItem[]
}
