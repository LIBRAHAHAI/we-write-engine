/**
 * Templates — 文章模板页（005 需求1）
 *
 * 数据：GET /api/templates（engine/config/templates/ 目录扫描；默认模板置顶）。
 * 每张卡片：标题 / id（等宽）/ 状态徽章（默认 · 完整 / 骨架）/ 步骤数 /
 * 描述全文。骨架模板标注「未开放运行」（新建文章下拉中禁选）。
 */
import { useEffect, useState } from 'react'
import { BookMarked, Hammer } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { Spinner } from '../components/ui/Spinner'

export default function Templates() {
  const [templates, setTemplates] = useState<
    Awaited<ReturnType<typeof api.listTemplates>>
  >([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await api.listTemplates()
        if (!cancelled) {
          setTemplates(data)
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
  }, [])

  const complete = templates.filter((t) => t.runnable)
  const skeletons = templates.filter((t) => !t.runnable)

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-fg-1">文章模板</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            共 {templates.length} 个模板（完整 {complete.length} · 骨架 {skeletons.length}）。
            新建文章时选择模板，实例绑定后定终身；目录
            <span className="font-mono-id"> engine/config/templates/</span>。
          </p>
        </div>
        {loading && <Spinner />}
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载模板失败：{error}
        </div>
      )}

      {/* 空态 */}
      {!loading && !error && templates.length === 0 && (
        <EmptyState
          title="模板目录为空"
          hint="engine/config/templates/ 下没有模板 YAML；请检查引擎目录挂载。"
        />
      )}

      {/* 完整模板 */}
      {complete.length > 0 && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-medium text-fg-2">可运行模板</p>
          {complete.map((t) => (
            <Card key={t.id} className="flex flex-col gap-2 p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <BookMarked className="h-4 w-4 shrink-0 text-accent" />
                  <span className="truncate text-sm font-semibold text-fg-1">{t.title}</span>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {t.is_default && (
                    <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent ring-1 ring-accent/30">
                      默认
                    </span>
                  )}
                  <span className="rounded bg-success/15 px-1.5 py-0.5 text-[10px] text-success ring-1 ring-success/25">
                    完整
                  </span>
                </div>
              </div>
              <p className="font-mono-id text-[11px] text-fg-3">{t.id}.yaml</p>
              <p className="text-xs leading-relaxed text-fg-2">{t.description || '—'}</p>
              <p className="text-[10px] text-fg-3">{t.step_count ?? '?'} 步 · 新建文章可选</p>
            </Card>
          ))}
        </div>
      )}

      {/* 骨架模板 */}
      {skeletons.length > 0 && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-medium text-fg-2">骨架模板（占位，未开放运行）</p>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {skeletons.map((t) => (
              <Card key={t.id} className="flex flex-col gap-2 p-4 opacity-75">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <Hammer className="h-4 w-4 shrink-0 text-fg-3" />
                    <span className="truncate text-sm font-semibold text-fg-2">{t.title}</span>
                  </div>
                  <span className="shrink-0 rounded bg-fg-3/15 px-1.5 py-0.5 text-[10px] text-fg-2 ring-1 ring-fg-3/30">
                    骨架
                  </span>
                </div>
                <p className="font-mono-id text-[11px] text-fg-3">{t.id}.yaml</p>
                <p className="line-clamp-4 text-xs leading-relaxed text-fg-3">
                  {t.description || '—'}
                </p>
                <p className="text-[10px] text-fg-3">
                  {t.step_count ?? '?'} 步草案 · 完善后开放（新建下拉暂禁选）
                </p>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
