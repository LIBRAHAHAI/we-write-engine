/**
 * ManageArticles — 管理文章页（C3/20260920：卡片墙 → 表格列表）
 *
 * 列：文章项目名 | 状态 | 文章模板 | 模式 | 立项日期 | 完成日期 | 当前步 | 操作
 * 排序：立项日期/名称 表头点击正倒序切换（默认立项日期倒序）。
 * 操作：推进（POST advance）/ 拍板（DecisionModal）/ 查看（open-folder，
 *       已归档显示「成果区 ↗」，未归档置灰）/ 隐藏·显示（hide/unhide）/
 *       删除（二次确认 → 归档式软删移入 instances/.archive/）。
 * 页头「显示已隐藏」开关：include_hidden=1 拉取，隐藏行半透明。
 *
 * 数据：GET /api/instances，10s 轮询（标签页隐藏暂停；操作成功后立即重拉）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Play, Gavel, FolderOpen, Eye, EyeOff, Trash2, ArrowUp, ArrowDown, ArrowUpDown,
  RefreshCw,
} from 'lucide-react'
import { api, ApiError } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/ui/EmptyState'
import { Spinner } from '../components/ui/Spinner'
import { toast } from '../components/Toast'
import { DecisionModal } from '../components/DecisionModal'
import type { AdvanceResult, InstanceSummary } from '../types'

const REFRESH_MS = 10_000

type SortKey = 'created_at' | 'instance'
type SortDir = 'asc' | 'desc'

interface RowUi {
  advancing: boolean
  reassigning: boolean
  result: AdvanceResult | null
}

const EMPTY_UI: RowUi = { advancing: false, reassigning: false, result: null }

/** ISO → 'YYYY-MM-DD HH:mm'；空值显示 —（C3：未归档/未完成显示 —） */
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return iso.slice(0, 16).replace('T', ' ')
}

/** mode 徽章配色（test/live 两种已知值，其余兜底） */
function modeBadgeCls(mode: string | null): string {
  if (mode === 'live')
    return 'bg-error/15 text-error ring-1 ring-error/30'
  return 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30'
}

export default function ManageArticles() {
  const [instances, setInstances] = useState<InstanceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ui, setUi] = useState<Record<string, RowUi>>({})
  const [showHidden, setShowHidden] = useState(false)
  // C3：排序（默认立项日期倒序）
  const [sortKey, setSortKey] = useState<SortKey>('created_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  // 005 需求4：拍板对话框目标（null = 关闭）
  const [decisionTarget, setDecisionTarget] = useState<InstanceSummary | null>(null)
  // C3：删除二次确认目标
  const [deleteTarget, setDeleteTarget] = useState<InstanceSummary | null>(null)
  const [deleting, setDeleting] = useState(false)
  const mountedRef = useRef(true)

  const patchUi = useCallback((name: string, patch: Partial<RowUi>) => {
    setUi((prev) => ({ ...prev, [name]: { ...(prev[name] ?? EMPTY_UI), ...patch } }))
  }, [])

  const load = useCallback(async () => {
    try {
      const data = await api.listInstances(showHidden)
      if (!mountedRef.current) return
      setInstances(data)
      setError(null)
    } catch (e) {
      if (!mountedRef.current) return
      setError(
        e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e),
      )
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [showHidden])

  // 首拉 + 10s 轮询（标签页隐藏暂停）
  useEffect(() => {
    mountedRef.current = true
    void load()
    let timer: ReturnType<typeof setTimeout> | null = null
    const schedule = () => {
      timer = setTimeout(() => {
        if (document.visibilityState === 'hidden') {
          schedule()
          return
        }
        void load().finally(schedule)
      }, REFRESH_MS)
    }
    schedule()
    return () => {
      mountedRef.current = false
      if (timer) clearTimeout(timer)
    }
  }, [load])

  /** 排序：立项日期/名称正倒序（默认立项日期倒序） */
  const sorted = [...instances].sort((a, b) => {
    let cmp: number
    if (sortKey === 'instance') {
      cmp = a.instance.localeCompare(b.instance)
    } else {
      const av = a.created_at ?? ''
      const bv = b.created_at ?? ''
      cmp = av.localeCompare(bv)
    }
    return sortDir === 'asc' ? cmp : -cmp
  })

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'created_at' ? 'desc' : 'asc')
    }
  }

  const sortIcon = (key: SortKey) => {
    if (sortKey !== key) return <ArrowUpDown className="h-3 w-3 opacity-40" />
    return sortDir === 'asc'
      ? <ArrowUp className="h-3 w-3" />
      : <ArrowDown className="h-3 w-3" />
  }

  /** 推进一步：阻塞至引擎返回（≤300s），展示 stdout */
  const handleAdvance = async (name: string) => {
    patchUi(name, { advancing: true, result: null })
    try {
      const r = await api.advance(name)
      patchUi(name, { advancing: false, result: r })
      if (r.returncode === 0) {
        toast.ok(`${name} 推进完成（${r.duration_ms}ms）`)
      } else {
        toast.err(`${name} 推进异常 rc=${r.returncode}，详见引擎输出`)
      }
    } catch (e) {
      patchUi(name, { advancing: false })
      toast.err(`推进失败：${e instanceof Error ? e.message : String(e)}`)
    }
    void load() // 立即重拉状态（step/status 可能已变）
  }

  /** 016 #10：blocked 卡手动重派（ww_panel 门禁：wweng 键空间 + status=blocked） */
  const handleReassign = async (inst: InstanceSummary) => {
    if (!inst.step) {
      toast.err('缺当前步 id，无法重派')
      return
    }
    patchUi(inst.instance, { reassigning: true })
    try {
      const r = await api.reassign(inst.instance, inst.step)
      toast.ok(`${r.instance} 卡 ${r.card} 已重派（unblock + dispatch）`)
    } catch (e) {
      toast.err(`重派失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      patchUi(inst.instance, { reassigning: false })
    }
    void load()
  }

  /** C3：查看 → 打开成果目录（未归档置灰不可点） */
  const handleOpenFolder = async (inst: InstanceSummary) => {
    try {
      const r = await api.openFolder(inst.instance)
      toast.ok(`已打开：${r.opened}`)
    } catch (e) {
      toast.err(e instanceof ApiError ? e.message : String(e))
    }
  }

  /** C3：隐藏 / 显示 */
  const handleToggleHidden = async (inst: InstanceSummary) => {
    try {
      if (inst.hidden) {
        await api.unhideInstance(inst.instance)
        toast.ok(`${inst.instance} 已取消隐藏`)
      } else {
        await api.hideInstance(inst.instance)
        toast.ok(`${inst.instance} 已隐藏（页头开关可显示已隐藏）`)
      }
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e))
    }
    void load()
  }

  /** C3：删除（归档式软删） */
  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const r = await api.deleteInstance(deleteTarget.instance)
      toast.ok(`${r.instance} 已软删除（移入 instances/.archive/，可在文件系统找回）`)
      setDeleteTarget(null)
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleting(false)
    }
    void load()
  }

  const thCls = 'px-3 py-2.5 text-left text-[11px] font-medium text-fg-3 whitespace-nowrap'
  const tdCls = 'px-3 py-2.5 text-xs text-fg-2 align-middle'

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-fg-1">管理文章</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            全部写作实例（{instances.length} 篇）。每 10 秒自动刷新；「推进」执行引擎
            engine.py 单步，「拍板」打开对话框，「成果区 ↗」打开归档目录，
            删除为软删除（移入 instances/.archive/，可找回）。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {/* C3：显示已隐藏开关 */}
          <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-fg-2">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => setShowHidden(e.target.checked)}
              className="h-3.5 w-3.5 accent-[var(--color-accent)]"
            />
            显示已隐藏
          </label>
          {loading && <Spinner />}
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载实例失败：{error}
        </div>
      )}

      {/* 空状态 */}
      {!loading && !error && sorted.length === 0 && (
        <EmptyState
          title={showHidden ? '没有实例（含隐藏）' : '还没有文章实例'}
          hint="点左侧「+ 新建文章」创建第一篇（或直接推进引擎已有实例）。"
        />
      )}

      {/* 表格列表 */}
      {sorted.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-line bg-bg-2">
          <table className="w-full min-w-[860px] border-collapse">
            <thead>
              <tr className="border-b border-line bg-bg-3/50">
                <th className={thCls}>
                  <button
                    type="button"
                    onClick={() => toggleSort('instance')}
                    className="inline-flex items-center gap-1 hover:text-fg-1"
                  >
                    文章项目名 {sortIcon('instance')}
                  </button>
                </th>
                <th className={thCls}>当前状态</th>
                <th className={thCls}>文章模板</th>
                <th className={thCls}>模式</th>
                <th className={thCls}>
                  <button
                    type="button"
                    onClick={() => toggleSort('created_at')}
                    className="inline-flex items-center gap-1 hover:text-fg-1"
                  >
                    立项日期 {sortIcon('created_at')}
                  </button>
                </th>
                <th className={thCls}>完成日期</th>
                <th className={thCls}>当前步</th>
                <th className={thCls}>操作</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((inst) => {
                const u = ui[inst.instance] ?? EMPTY_UI
                const archived = Boolean(inst.archived_dir)
                const blocked = Boolean(inst.blocked)
                return (
                  <tr
                    key={inst.instance}
                    className={`border-b border-line/60 last:border-b-0 hover:bg-bg-3/30 ${
                      inst.hidden ? 'opacity-50' : ''
                    }`}
                  >
                    {/* 项目名 */}
                    <td className={`${tdCls} font-mono-id font-semibold text-fg-1`}>
                      <span className="inline-flex items-center gap-1.5">
                        {inst.hidden && <EyeOff className="h-3 w-3 text-fg-3" />}
                        {inst.instance}
                      </span>
                    </td>
                    {/* 状态 */}
                    <td className={tdCls}>
                      <StatusBadge status={inst.status} />
                    </td>
                    {/* 模板 */}
                    <td className={tdCls}>
                      {inst.template ? (
                        <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent ring-1 ring-accent/25">
                          {inst.template}
                        </span>
                      ) : '—'}
                    </td>
                    {/* 模式 */}
                    <td className={tdCls}>
                      <span
                        className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${modeBadgeCls(inst.mode)}`}
                      >
                        {inst.mode ?? '—'}
                      </span>
                    </td>
                    {/* 立项日期 */}
                    <td className={`${tdCls} whitespace-nowrap font-mono-id`}>
                      {fmtDate(inst.created_at)}
                    </td>
                    {/* 完成日期（未归档/未完成 = —） */}
                    <td className={`${tdCls} whitespace-nowrap font-mono-id`}>
                      {fmtDate(inst.completed_at)}
                    </td>
                    {/* 当前步 */}
                    <td className={`${tdCls} font-mono-id`}>
                      <span className="block max-w-[140px] truncate" title={inst.step ?? ''}>
                        {inst.step ?? '—'}
                      </span>
                    </td>
                    {/* 操作 */}
                    <td className={tdCls}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => void handleAdvance(inst.instance)}
                          disabled={u.advancing || inst.status === 'completed' || blocked}
                          className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
                          title={blocked
                            ? '卡已阻塞，请先重派'
                            : '推进一步（engine.py --instance）'}
                        >
                          {u.advancing ? (
                            <Spinner className="h-3 w-3 text-white" />
                          ) : (
                            <Play className="h-3 w-3" />
                          )}
                          {u.advancing ? '推进中…' : '推进'}
                        </button>
                        {/* 016 #10：blocked 卡显示「重派」按钮（unblock + dispatch） */}
                        {blocked && (
                          <button
                            type="button"
                            onClick={() => void handleReassign(inst)}
                            disabled={u.reassigning}
                            className="inline-flex items-center gap-1 rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning transition-colors hover:bg-warning/20 disabled:cursor-not-allowed disabled:opacity-40"
                            title={`重派阻塞卡（unblock + dispatch；卡 ${inst.blocked_card ?? '—'}）`}
                          >
                            {u.reassigning ? (
                              <Spinner className="h-3 w-3" />
                            ) : (
                              <RefreshCw className="h-3 w-3" />
                            )}
                            {u.reassigning ? '重派中…' : '重派'}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setDecisionTarget(inst)}
                          className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[11px] text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
                          title="打开拍板对话框（选项全文渲染；C|意见可附材料）"
                        >
                          <Gavel className="h-3 w-3" />
                          拍板
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleOpenFolder(inst)}
                          disabled={!archived}
                          className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[11px] text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:cursor-not-allowed disabled:opacity-40"
                          title={archived
                            ? `打开成果目录：${inst.archived_dir}`
                            : '无成果目录'}
                        >
                          <FolderOpen className="h-3 w-3" />
                          {archived ? '成果区 ↗' : '查看'}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleToggleHidden(inst)}
                          className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[11px] text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
                          title={inst.hidden ? '取消隐藏' : '隐藏（列表默认不显示）'}
                        >
                          {inst.hidden ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
                          {inst.hidden ? '显示' : '隐藏'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(inst)}
                          className="inline-flex items-center gap-1 rounded-md border border-error/30 px-2.5 py-1 text-[11px] text-error transition-colors hover:bg-error/10 disabled:cursor-not-allowed disabled:opacity-40"
                          title="软删除：移入 instances/.archive/，可在文件系统找回"
                        >
                          <Trash2 className="h-3 w-3" />
                          删除
                        </button>
                      </div>
                      {/* 016 #10：blocked 提示（推进置灰原因显式化，消除「点了没反应」困惑） */}
                      {blocked && !u.reassigning && (
                        <p className="mt-1 text-[10px] text-warning">
                          卡已阻塞，请先重派（点「重派」unblock 后引擎重新派工）
                        </p>
                      )}
                      {/* 最近一次推进输出（表内折叠） */}
                      {u.result && (
                        <pre className="mt-1.5 max-h-24 max-w-[420px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-bg-1 p-2 font-mono-id text-[10px] leading-relaxed text-fg-3">
                          {u.result.stdout || u.result.stderr || '(无输出)'}
                        </pre>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 005 需求4：拍板对话框（选项全文 + C|意见带附件） */}
      <DecisionModal
        open={decisionTarget !== null}
        onClose={() => setDecisionTarget(null)}
        target={decisionTarget}
        onDecided={() => void load()}
      />

      {/* C3：删除二次确认对话框（归档式软删文案） */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
          onClick={() => !deleting && setDeleteTarget(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-line bg-bg-2 p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="flex items-center gap-2 text-sm font-semibold text-fg-1">
              <Trash2 className="h-4 w-4 text-error" />
              确认删除「{deleteTarget.instance}」？
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-fg-2">
              这是软删除：实例文件将移入{' '}
              <code className="rounded bg-bg-3 px-1 py-0.5 font-mono-id text-[10px]">
                instances/.archive/
              </code>
              ，不会物理删除，可在文件系统找回。隐藏标记、拍板文件等附属文件一并移入。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                className="rounded-md border border-line px-3 py-1.5 text-xs text-fg-2 hover:bg-bg-3 disabled:opacity-40"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={deleting}
                className="inline-flex items-center gap-1.5 rounded-md bg-error px-3 py-1.5 text-xs font-medium text-white hover:bg-error/85 disabled:opacity-40"
              >
                {deleting && <Spinner className="h-3 w-3 text-white" />}
                确认软删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
