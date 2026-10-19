/**
 * CreateArticleModal — 新建文章模态框（005 需求2/5 改版）
 *
 * 除「编号+名称」外新增：
 *   ① 模板选择（下拉，列 templates/ 目录，默认热门话题评论；骨架模板标注未开放）
 *   ② 材料区（MaterialsInput：打字输入 + 加号添加网址/文件/文件夹路径，
 *      随实例落盘，引擎首次派选题卡时写卡 comment）
 *   ③ mode 默认 **live**（勾选框显式切 test；首跑定终身，创建后不可改）
 *
 * 实例名规则（后端最终门禁）：字母/数字/连字符；t- 前缀保留名拒绝。
 *
 * Props：
 *   open      —— 是否显示
 *   onClose   —— 关闭回调（ESC / backdrop / ✕ / 成功后 1s 自动）
 *   onCreated —— 创建成功回调（父组件触发 refetch）
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { X, AlertTriangle } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { toast } from './Toast'
import { Spinner } from './ui/Spinner'
import { MaterialsInput } from './MaterialsInput'
import type { InstanceSummary, MaterialItem, TemplateInfo } from '../types'

// 与后端 _validate_name 同款规则
const NAME_RE = /^[A-Za-z0-9-]+$/

interface CreateArticleModalProps {
  open: boolean
  onClose: () => void
  onCreated?: (inst: InstanceSummary) => void
}

/** 本地即时校验：返回错误文案；通过返回 null */
function validate(name: string): string | null {
  if (!name) return '请输入文章实例名'
  if (!NAME_RE.test(name)) return '只允许字母、数字、连字符（-）'
  if (name.toLowerCase().startsWith('t-')) return 't- 前缀是引擎测试实例保留名，禁止使用'
  return null
}

export function CreateArticleModal({
  open,
  onClose,
  onCreated,
}: CreateArticleModalProps) {
  const [name, setName] = useState('')
  const [template, setTemplate] = useState('')
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [materials, setMaterials] = useState<MaterialItem[]>([])
  const [testMode, setTestMode] = useState(false) // 005 需求5：缺省 live
  const [creating, setCreating] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开时复位 + 拉模板列表 + 聚焦
  useEffect(() => {
    if (open) {
      setName('')
      setMaterials([])
      setTestMode(false)
      let cancelled = false
      void api
        .listTemplates()
        .then((ts) => {
          if (cancelled) return
          setTemplates(ts)
          // 默认选中：默认模板（加载失败兜底字面量，与后端 DEFAULT_TEMPLATE 同源）
          setTemplate((cur) => cur || ts.find((t) => t.is_default)?.id || 'hot-topic-commentary')
        })
        .catch(() => {
          if (!cancelled) setTemplate('hot-topic-commentary')
        })
      const id = window.setTimeout(() => inputRef.current?.focus(), 50)
      return () => {
        cancelled = true
        window.clearTimeout(id)
      }
    }
  }, [open])

  // ESC 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !creating) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, creating, onClose])

  const handleCreate = useCallback(async () => {
    const n = name.trim()
    const localErr = validate(n)
    if (localErr) {
      toast.warn(localErr)
      return
    }
    setCreating(true)
    try {
      const inst = await api.createInstance({
        name: n,
        template: template || undefined,
        mode: testMode ? 'test' : 'live',
        materials,
      })
      toast.ok(
        `已创建文章实例 ${inst.instance}（模板 ${inst.template ?? ''}，` +
          `mode=${inst.mode}，等待首次推进）`,
      )
      onCreated?.(inst)
      window.setTimeout(onClose, 1000)
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e)
      toast.err(`创建失败：${msg}`)
    } finally {
      setCreating(false)
    }
  }, [name, template, testMode, materials, onCreated, onClose])

  if (!open) return null

  const err = validate(name.trim())
  const canSubmit = !err && !creating && !!template

  const selTemplate = templates.find((t) => t.id === template)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !creating) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-article-modal-title"
    >
      <div className="flex max-h-[88vh] w-full max-w-lg flex-col rounded-lg border border-line bg-bg-2 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2
            id="create-article-modal-title"
            className="text-sm font-semibold text-fg-1"
          >
            新建文章
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={creating}
            className="rounded-md p-1 text-fg-3 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body（可滚动） */}
        <div className="flex flex-col gap-4 overflow-y-auto p-4">
          {/* 名称 */}
          <div>
            <label htmlFor="create-article-name" className="mb-1.5 block text-xs font-medium text-fg-2">
              文章实例名（字母/数字/连字符，如 article-003）
            </label>
            <input
              ref={inputRef}
              id="create-article-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && canSubmit) void handleCreate()
              }}
              placeholder="例：article-003"
              disabled={creating}
              className="w-full rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
            />
            {name.trim() && err && <p className="mt-1.5 text-xs text-error">{err}</p>}
          </div>

          {/* 模板选择 */}
          <div>
            <label htmlFor="create-article-template" className="mb-1.5 block text-xs font-medium text-fg-2">
              文章模板
            </label>
            <select
              id="create-article-template"
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              disabled={creating || templates.length === 0}
              className="w-full rounded-md border border-line bg-bg-3 px-3 py-2 text-sm text-fg-1 focus:border-accent focus:outline-none disabled:opacity-50"
            >
              {templates.length === 0 && <option value="hot-topic-commentary">热门话题评论（默认）</option>}
              {templates.map((t) => (
                <option key={t.id} value={t.id} disabled={!t.runnable}>
                  {t.is_default ? '★ ' : ''}
                  {t.title}
                  {t.runnable ? '' : '（骨架，未开放）'}
                </option>
              ))}
            </select>
            {selTemplate && (
              <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-fg-3">
                {selTemplate.description || '—'}（{selTemplate.step_count ?? '?'} 步）
              </p>
            )}
          </div>

          {/* 材料区 */}
          <div>
            <p className="mb-1.5 text-xs font-medium text-fg-2">
              写作材料<span className="ml-1 font-normal text-fg-3">（可选；随选题卡 comment 注入引擎）</span>
            </p>
            <MaterialsInput
              materials={materials}
              onChange={setMaterials}
              disabled={creating}
            />
          </div>

          {/* 模式（005 需求5：默认 live，勾选切 test） */}
          <div className="rounded-md border border-line bg-bg-1 p-3">
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                checked={testMode}
                onChange={(e) => setTestMode(e.target.checked)}
                disabled={creating}
                className="mt-0.5 h-3.5 w-3.5 accent-accent"
              />
              <span className="text-xs text-fg-2">
                test 模式（不触真草稿箱，上传步走 mock）
              </span>
            </label>
            <p className="mt-1.5 flex items-start gap-1 text-[11px] leading-relaxed text-warning">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              当前模式：<strong className="font-semibold">{testMode ? 'test' : 'live'}</strong>
              （默认 live，真实入草稿箱）。首跑定终身：创建后模式不可更改，引擎显式冲突会拒跑。
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={creating}
            className="rounded-md border border-line px-4 py-1.5 text-xs text-fg-1 transition-colors hover:bg-bg-3 disabled:opacity-40"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={!canSubmit}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {creating && <Spinner className="h-3.5 w-3.5" />}
            {creating ? '创建中…' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default CreateArticleModal
