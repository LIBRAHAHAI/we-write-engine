/**
 * DecisionModal — 拍板对话框（005 需求4，复用 MaterialsInput 组件）
 *
 * 打开时拉实例当前步定义（按实例绑定模板取 /api/pipeline?template=）：
 *   - decision 步：选项**全文渲染**（key + label 完整内容，点击即拍板），
 *     不再只显示 A/B/C/D 代号
 *   - C 选项（带修改意见修改）/ B 选项（驳回重做，016 批次三 #11）：
 *     切换对话框式输入——正文写意见 + 加号添加文件/文件夹/网址附件，
 *     提交拼 `<key>|意见\n[补充材料]…` 决策文本（C=带意见返工 / B=驳回重做
 *     说明，标题区分），引擎打回返工卡时意见+材料一并写入卡 comment
 *   - 非拍板步 / 拉取失败：降级为自由文本输入（旧行为兜底）
 *
 * Props：
 *   open / onClose —— 显示控制
 *   target         —— 拍板目标（实例摘要，含当前 step 与绑定 template）
 *   onDecided      —— 拍板提交成功回调（父组件刷新）
 */
import { useEffect, useState } from 'react'
import { Gavel, X, AlertTriangle } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { toast } from './Toast'
import { Spinner } from './ui/Spinner'
import { MaterialsInput } from './MaterialsInput'
import type { InstanceSummary, MaterialItem, PipelineStep } from '../types'

interface DecisionModalProps {
  open: boolean
  onClose: () => void
  target: InstanceSummary | null
  onDecided?: (name: string) => void
}

/** 拍板附件材料的类型集（文件/文件夹/网址；纯文字材料走意见正文） */
const ATTACH_TYPES: MaterialItem['type'][] = ['url', 'file', 'folder']

/** 走对话框式意见输入的选项 key：C=带意见返工 / B=驳回重做（016 批次三 #11） */
const REWORK_KEYS = ['C', 'B'] as const
type ReworkKey = (typeof REWORK_KEYS)[number]

export function DecisionModal({ open, onClose, target, onDecided }: DecisionModalProps) {
  const [loading, setLoading] = useState(false)
  const [step, setStep] = useState<PipelineStep | null>(null)
  const [freeText, setFreeText] = useState('')
  // B/C 路径（带意见返工 / 驳回重做说明）：对话框式输入
  const [reworkOpen, setReworkOpen] = useState(false)
  const [reworkKey, setReworkKey] = useState<ReworkKey>('C')
  const [opinion, setOpinion] = useState('')
  const [attachments, setAttachments] = useState<MaterialItem[]>([])
  const [submitting, setSubmitting] = useState(false)

  // 打开时复位 + 拉当前步定义
  useEffect(() => {
    if (!open || !target) return
    setFreeText('')
    setReworkOpen(false)
    setReworkKey('C')
    setOpinion('')
    setAttachments([])
    setStep(null)
    let cancelled = false
    setLoading(true)
    void api
      .getPipeline(target.template ?? undefined)
      .then((info) => {
        if (cancelled) return
        const cur = info.steps.find((s) => s.id === target.step) ?? null
        setStep(cur && cur.type === 'decision' ? cur : null)
      })
      .catch(() => {
        if (!cancelled) setStep(null) // 降级自由文本
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, target])

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, submitting, onClose])

  if (!open || !target) return null

  const isDecisionStep = step?.type === 'decision'
  const options = step?.options ?? []

  /** 提交拍板（option key 或自由文本或 C|意见 拼串） */
  const submit = async (decision: string) => {
    if (!decision.trim()) {
      toast.warn('拍板内容不能为空')
      return
    }
    setSubmitting(true)
    try {
      await api.decide(target.instance, decision)
      toast.ok(`${target.instance} 拍板已写入：${decision.slice(0, 60)}`)
      onDecided?.(target.instance)
      window.setTimeout(onClose, 600)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
      toast.err(`拍板失败：${msg}`)
    } finally {
      setSubmitting(false)
    }
  }

  /** B/C 路径提交：意见 + 附件材料拼决策文本（引擎 _do_decision `<key>|…` 契约，
   * 016 批次三 #11：B 同样支持带说明，引擎侧 B 分支落 pending_rework_note） */
  const submitRework = async () => {
    const op = opinion.trim()
    if (!op) {
      toast.warn('请填写意见正文')
      return
    }
    const atts = attachments.filter((m) => ATTACH_TYPES.includes(m.type))
    const parts = [op]
    if (atts.length > 0) {
      parts.push(
        '[补充材料（随返工卡 comment 注入）]\n' +
          atts.map((m) => `- [${m.type}] ${m.content}`).join('\n'),
      )
    }
    await submit(`${reworkKey}|${parts.join('\n\n')}`)
  }

  /** 对话框标题/副文案按 B/C 区分（016 批次三 #11） */
  const reworkTitle = reworkKey === 'B' ? '驳回重做说明（B）' : '带意见返工（C）'
  const reworkHint = reworkKey === 'B'
    ? '驳回说明与附件将注入重做卡 comment，重做完成后回本门再确认。'
    : '意见与附件将注入返工卡 comment，返工完成后回本门再确认。'
  const isReworkKey = (k: string) => (REWORK_KEYS as readonly string[]).includes(k.toUpperCase())

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="decision-modal-title"
    >
      <div className="flex max-h-[88vh] w-full max-w-lg flex-col rounded-lg border border-line bg-bg-2 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 id="decision-modal-title" className="flex items-center gap-2 text-sm font-semibold text-fg-1">
            <Gavel className="h-4 w-4 text-warning" />
            拍板 · <span className="font-mono-id">{target.instance}</span>
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-md p-1 text-fg-3 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-3 overflow-y-auto p-4">
          {loading && (
            <div className="flex items-center gap-2 text-xs text-fg-3">
              <Spinner className="h-3.5 w-3.5" />
              正在拉取当前拍板步定义…
            </div>
          )}

          {/* 拍板步：question + 选项全文渲染 */}
          {isDecisionStep && !reworkOpen && (
            <>
              <div className="rounded-md border border-line bg-bg-1 p-3">
                <p className="text-[10px] text-fg-3">
                  当前步 <span className="font-mono-id">{step!.id}</span> · 选项全文如下，点击即拍板
                </p>
                <p className="mt-1 text-xs leading-relaxed text-fg-1">{step!.question}</p>
              </div>
              <div className="flex flex-col gap-1.5">
                {options.map((o) => (
                  <button
                    key={String(o.key)}
                    type="button"
                    disabled={submitting}
                    onClick={() => {
                      if (isReworkKey(String(o.key))) {
                        // B/C 路径走对话框式输入（016 #11：B 驳回也带说明）
                        setReworkKey(String(o.key).toUpperCase() as ReworkKey)
                        setReworkOpen(true)
                        return
                      }
                      void submit(String(o.key))
                    }}
                    className={`flex items-start gap-2.5 rounded-md border p-2.5 text-left transition-colors disabled:opacity-40 ${
                      isReworkKey(String(o.key))
                        ? 'border-warning/40 bg-warning/5 hover:bg-warning/10'
                        : 'border-line bg-bg-1 hover:border-accent/50 hover:bg-bg-3'
                    }`}
                  >
                    <span
                      className={`grid h-5 min-w-5 shrink-0 place-items-center rounded px-1 font-mono-id text-[11px] font-bold ${
                        isReworkKey(String(o.key))
                          ? 'bg-warning/20 text-warning'
                          : 'bg-accent/15 text-accent'
                      }`}
                    >
                      {String(o.key)}
                    </span>
                    <span className="min-w-0 flex-1 text-xs leading-relaxed text-fg-2">
                      {o.label || '（无说明）'}
                      {isReworkKey(String(o.key)) && (
                        <span className="mt-0.5 block text-[10px] text-warning">
                          点击展开意见输入框（可附文件/文件夹/网址）
                        </span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}

          {/* B/C 路径：对话框式意见输入 + 附件（标题区分，016 #11） */}
          {isDecisionStep && reworkOpen && (
            <>
              <div className="rounded-md border border-warning/40 bg-warning/5 p-3">
                <p className="text-xs font-medium text-warning">{reworkTitle}</p>
                <p className="mt-1 text-[11px] leading-relaxed text-fg-2">
                  {reworkHint}
                </p>
              </div>
              <div>
                <label htmlFor="decision-opinion" className="mb-1.5 block text-xs font-medium text-fg-2">
                  {reworkKey === 'B' ? '驳回说明正文' : '修改意见正文'}
                </label>
                <textarea
                  id="decision-opinion"
                  value={opinion}
                  onChange={(e) => setOpinion(e.target.value)}
                  disabled={submitting}
                  rows={4}
                  placeholder="例：开头钩子太弱，前 100 字必须抓人；第二节论据与论点错位…"
                  className="w-full resize-y rounded-md border border-line bg-bg-3 px-3 py-2 text-xs leading-relaxed text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
                />
                <p className="mt-1 text-right text-[10px] text-fg-3">{opinion.length} / 2000</p>
              </div>
              <div>
                <p className="mb-1.5 text-xs font-medium text-fg-2">
                  补充材料<span className="ml-1 font-normal text-fg-3">（可选：网址/文件/文件夹）</span>
                </p>
                <MaterialsInput
                  materials={attachments}
                  onChange={setAttachments}
                  disabled={submitting}
                  hint="粘贴网址或本地路径，回车添加"
                />
              </div>
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  onClick={() => setReworkOpen(false)}
                  disabled={submitting}
                  className="rounded-md border border-line px-3 py-1.5 text-xs text-fg-2 transition-colors hover:bg-bg-3 disabled:opacity-40"
                >
                  ← 返回选项
                </button>
                <button
                  type="button"
                  onClick={() => void submitRework()}
                  disabled={submitting || !opinion.trim()}
                  className="inline-flex items-center gap-2 rounded-md bg-warning px-4 py-1.5 text-xs font-medium text-bg-1 transition-colors hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {submitting && <Spinner className="h-3.5 w-3.5" />}
                  {submitting ? '提交中…' : reworkKey === 'B' ? '提交驳回说明' : '提交修改意见'}
                </button>
              </div>
            </>
          )}

          {/* 降级：非拍板步 / 定义拉取失败 → 自由文本（旧兜底） */}
          {!isDecisionStep && (
            <>
              <div className="flex items-start gap-2 rounded-md border border-line bg-bg-1 p-3 text-[11px] leading-relaxed text-fg-3">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                {loading
                  ? '正在识别当前步骤…'
                  : `当前步 ${target.step ?? '—'} 不是拍板门（或定义拉取失败），降级为自由文本拍板。`}
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !submitting && freeText.trim()) void submit(freeText)
                  }}
                  placeholder="A / 1-5 / 同意 / C|修改意见…"
                  disabled={submitting}
                  className="min-w-0 flex-1 rounded-md border border-line bg-bg-3 px-2.5 py-1.5 text-xs text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => void submit(freeText)}
                  disabled={submitting || !freeText.trim()}
                  className="shrink-0 rounded-md bg-warning px-3 py-1.5 text-xs font-medium text-bg-1 transition-colors hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {submitting ? '写入中…' : '提交拍板'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default DecisionModal
