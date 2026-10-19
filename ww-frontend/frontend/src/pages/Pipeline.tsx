/**
 * Pipeline — 流水线页（C4/20260920：模板筛选 + 分页 + 骨架空态）
 *
 * 页头模板下拉（数据源 GET /api/templates，默认选中默认模板 hot-topic-commentary），
 * 切换后按 GET /api/pipeline?template=<id> 拉取。模板 status != complete（skeleton 等）
 * → 空态「流程定义后续补充，敬请期待」。步骤列表每页 8 步，底部分页栏；
 * 切模板后回到第 1 页。每步展示：序号 / 步骤 id（等宽）/ 类型徽章 / 执行者 /
 * 描述（拍板门显示 question）/ next 指向。
 */
import { useEffect, useState } from 'react'
import { ArrowRight, GitBranch, ChevronLeft, ChevronRight } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { StepTypeBadge } from '../components/StepTypeBadge'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { Spinner } from '../components/ui/Spinner'
import type { PipelineInfo, TemplateInfo } from '../types'

const DEFAULT_TEMPLATE_ID = 'hot-topic-commentary'
const PAGE_SIZE = 8

// 图例用的类型中文名（与 StepTypeBadge 同口径）
const TYPE_LABEL: Record<string, string> = {
  dispatch: '工作步',
  dispatch_group: '并行组',
  decision: '拍板门',
  wait: '屏障',
  check: '机器校验',
  done: '收官',
}

export default function Pipeline() {
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [templateId, setTemplateId] = useState<string>(DEFAULT_TEMPLATE_ID)
  const [info, setInfo] = useState<PipelineInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  // 模板列表：默认模板置顶（后端同口径），默认选中默认模板
  useEffect(() => {
    let cancelled = false
    void api
      .listTemplates()
      .then((list) => {
        if (cancelled) return
        setTemplates(list)
        const def = list.find((t) => t.is_default)
        if (def) setTemplateId(def.id)
      })
      .catch(() => {
        /* 模板列表失败不阻塞：下拉退化为仅默认模板选项 */
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await api.getPipeline(templateId)
        if (!cancelled) {
          setInfo(data)
          setError(null)
        }
      } catch (e) {
        if (!cancelled)
          setError(
            e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e),
          )
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [templateId])

  const selectedMeta = templates.find((t) => t.id === templateId)
  const skeleton = selectedMeta != null && selectedMeta.status !== 'complete'
  const steps = info?.steps ?? []
  const totalPages = Math.max(1, Math.ceil(steps.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const pageSteps = steps.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  /** 切模板：拉取对应流水线并回到第 1 页 */
  const switchTemplate = (tid: string) => {
    setTemplateId(tid)
    setPage(1)
    setLoading(true)
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-fg-1">流水线</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            {skeleton
              ? `${selectedMeta?.title ?? templateId}`
              : info?.title || 'we-write 流水线定义'}
            {!skeleton && steps.length > 0 && (
              <> · 共 {steps.length} 步 · 迭代上限{' '}
                {info?.max_iterations ?? '—'} 轮</>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {/* C4：模板下拉选择器（切模板/筛选后回到第 1 页） */}
          <select
            value={templateId}
            onChange={(e) => switchTemplate(e.target.value)}
            className="max-w-[240px] rounded-md border border-line bg-bg-2 px-2.5 py-1.5 text-xs text-fg-1 outline-none focus:ring-1 focus:ring-accent/50"
            title="选择文章模板流程"
          >
            {templates.length === 0 && (
              <option value={DEFAULT_TEMPLATE_ID}>{DEFAULT_TEMPLATE_ID}</option>
            )}
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
                {t.status !== 'complete' ? '（后续补充）' : ''}
                {t.is_default ? ' · 默认' : ''}
              </option>
            ))}
          </select>
          {loading && <Spinner />}
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载流水线失败：{error}
        </div>
      )}

      {/* C4：骨架/未开放模板空态 */}
      {!loading && !error && skeleton && (
        <EmptyState
          title={`「${selectedMeta?.title ?? templateId}」流程定义后续补充，敬请期待`}
          hint="该模板尚未开放（骨架占位）；切换到默认模板可查看完整步骤链。"
        />
      )}

      {/* 空态（后端解析失败兜底返回空列表） */}
      {!loading && !error && !skeleton && (!info || steps.length === 0) && (
        <EmptyState
          title="流水线为空或解析失败"
          hint="后端解析模板失败时会返回空列表兜底；请检查 engine/config/templates/ 目录是否可读。"
        />
      )}

      {/* 步骤时间线（当前页切片） */}
      {!loading && !error && !skeleton && steps.length > 0 && (
        <>
          <div className="flex flex-col gap-2">
            {pageSteps.map((s, i) => (
              <Card key={s.id} className="flex items-start gap-3 p-3.5">
                {/* 序号（跨页连续） */}
                <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-bg-3 font-mono-id text-[11px] text-fg-3">
                  {(safePage - 1) * PAGE_SIZE + i + 1}
                </span>

                <div className="min-w-0 flex-1">
                  {/* 行1：id + 类型徽章 + 执行者 */}
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono-id text-sm font-semibold text-fg-1">
                      {s.id}
                    </span>
                    <StepTypeBadge type={s.type} />
                    {s.assignee && (
                      <span className="rounded bg-bg-3 px-1.5 py-0.5 text-[10px] text-fg-2">
                        @{s.assignee}
                      </span>
                    )}
                    {s.members != null && s.members > 0 && (
                      <span className="rounded bg-bg-3 px-1.5 py-0.5 text-[10px] text-fg-2">
                        {s.members} 路并行
                      </span>
                    )}
                  </div>

                  {/* 行2：描述 / 拍板问题 */}
                  {(s.description || s.question) && (
                    <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-fg-2">
                      {s.question ? `问：${s.question}` : s.description}
                    </p>
                  )}

                  {/* 行3：next 指向 */}
                  {s.next && (
                    <p className="mt-1.5 flex items-center gap-1 text-[10px] text-fg-3">
                      <ArrowRight className="h-3 w-3" />
                      <span className="font-mono-id">{s.next}</span>
                    </p>
                  )}
                </div>
              </Card>
            ))}
          </div>

          {/* C4：分页栏（上一页 / 页码 / 下一页） */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-1.5">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage <= 1}
                className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1.5 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                上一页
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setPage(n)}
                  className={`h-7 w-7 rounded-md text-xs font-medium transition-colors ${
                    n === safePage
                      ? 'bg-accent text-white'
                      : 'border border-line text-fg-2 hover:bg-bg-3 hover:text-fg-1'
                  }`}
                >
                  {n}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage >= totalPages}
                className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1.5 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:cursor-not-allowed disabled:opacity-40"
              >
                下一页
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* 图例（按类型动态统计——以模板实测条目为准） */}
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-bg-2 px-4 py-3 text-[11px] text-fg-3">
            <GitBranch className="h-3.5 w-3.5" />
            结构口径（实测 {steps.length} 节点）：
            {Object.entries(
              steps.reduce<Record<string, number>>((acc, s) => {
                acc[s.type] = (acc[s.type] ?? 0) + 1
                return acc
              }, {}),
            )
              .map(([t, n]) => `${TYPE_LABEL[t] ?? t} ${n}`)
              .join(' · ')}
          </div>
        </>
      )}
    </div>
  )
}
