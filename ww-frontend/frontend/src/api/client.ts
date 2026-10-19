/**
 * 原生 fetch 封装 —— ww-frontend 端点（与 backend/api.py 一一对应）
 *
 *   health / listTemplates / listInstances / getInstance / createInstance
 *   advance / decide / getPipeline / getLogs
 *
 * HTTP 状态码契约处理：404 资源不存在 / 其余非 2xx 取 detail 友好提示 /
 * 网络层异常归一为 ApiError(status=0)。
 */
import type {
  HealthInfo,
  InstanceSummary,
  InstanceDetail,
  AdvanceResult,
  PipelineInfo,
  LogEntry,
  TemplateInfo,
  CreateArticlePayload,
} from '../types'
import { getApiBase } from './config'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getApiBase()
  let res: Response
  try {
    res = await fetch(`${base}/api${path}`, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    })
  } catch (e) {
    throw new ApiError(0, `网络不可达（${base}）：${e instanceof Error ? e.message : String(e)}`)
  }

  if (res.status === 404) {
    throw new ApiError(404, '资源不存在（404）')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      /* 非 JSON 响应体 */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export const api = {
  /** 健康检查（侧栏引擎状态灯数据源） */
  health: () => request<HealthInfo>('/health'),

  /** 实例列表（instances/*.json 摘要；C2：includeHidden=true 拉取含隐藏实例） */
  listInstances: (includeHidden?: boolean) =>
    request<InstanceSummary[]>(
      `/instances${includeHidden ? '?include_hidden=1' : ''}`,
    ),

  /** C2：隐藏实例（列表默认不显示） */
  hideInstance: (name: string) =>
    request<{ instance: string; hidden: boolean }>(
      `/instances/${encodeURIComponent(name)}/hide`,
      { method: 'POST' },
    ),

  /** C2：取消隐藏 */
  unhideInstance: (name: string) =>
    request<{ instance: string; hidden: boolean }>(
      `/instances/${encodeURIComponent(name)}/unhide`,
      { method: 'POST' },
    ),

  /** C2：删除实例（归档式软删，移入 instances/.archive/，可在文件系统找回） */
  deleteInstance: (name: string) =>
    request<{ instance: string; archived_to: string; moved: string[] }>(
      `/instances/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    ),

  /** C2：打开成果目录（已归档→成果区；未归档→exchange/artifacts；404=无目录） */
  openFolder: (name: string) =>
    request<{ instance: string; opened: string }>(
      `/instances/${encodeURIComponent(name)}/open-folder`,
      { method: 'POST' },
    ),

  /** 实例详情（完整 state，含 history） */
  getInstance: (name: string) =>
    request<InstanceDetail>(`/instances/${encodeURIComponent(name)}`),

  /** 005 需求2/5：新建文章（模板 + 材料清单 + 模式，mode 缺省 live） */
  createInstance: (payload: CreateArticlePayload) =>
    request<InstanceSummary>('/instances', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** 推进一步（step 可选：从指定步续跑；超时 300s，返回引擎 stdout） */
  advance: (name: string, step?: string) =>
    request<AdvanceResult>(`/instances/${encodeURIComponent(name)}/advance`, {
      method: 'POST',
      body: JSON.stringify(step ? { step } : {}),
    }),

  /** 写拍板决定（落 .decision 文件，引擎下一拍消费；支持 `C|意见`/`B|说明` 格式） */
  decide: (name: string, decision: string) =>
    request<{ instance: string; decision: string; file: string }>(
      `/instances/${encodeURIComponent(name)}/decision`,
      { method: 'POST', body: JSON.stringify({ decision }) },
    ),

  /** 016 #10：blocked 卡手动重派（ww_panel 门禁：wweng 键空间 + status=blocked） */
  reassign: (name: string, step: string) =>
    request<{ ok: boolean; instance: string; step: string; card: string }>(
      `/instances/${encodeURIComponent(name)}/reassign`,
      { method: 'POST', body: JSON.stringify({ step }) },
    ),

  /** 流水线定义（?template= 缺省默认模板；解析失败返回空列表兜底） */
  getPipeline: (template?: string) =>
    request<PipelineInfo>(
      `/pipeline${template ? `?template=${encodeURIComponent(template)}` : ''}`,
    ),

  /** 005 需求1：文章模板列表（默认置顶，骨架标注 runnable=false） */
  listTemplates: () => request<TemplateInfo[]>('/templates'),

  /** 内存日志（倒序：最新在前） */
  getLogs: () => request<LogEntry[]>('/logs'),
}
