/**
 * MaterialsInput — 写作材料输入区（005 需求2/4 复用组件）
 *
 * 对话式输入：
 *   - 打字 + 回车/「添加」→ 追加一条 text 材料
 *   - 「+」按钮展开添加行：选类型（网址/文件/文件夹）+ 粘贴路径或 URL → 添加
 *   - 已添加材料以 chips 呈现（类型徽章 + 内容摘要 + 删除）
 *
 * 材料去向（新建文章 / 拍板驳回共用）：
 *   - 新建：随实例 state.materials 落盘，引擎首次派选题卡时写卡 comment
 *   - 驳回（C|意见）：拼进决策文本，引擎打回返工卡时写卡 comment
 *
 * Props：
 *   materials —— 当前材料清单（受控）
 *   onChange  —— 清单变更回调
 *   disabled  —— 提交中禁用
 *   hint      —— 输入框占位提示（默认面向新材料场景）
 */
import { useState } from 'react'
import { Plus, Globe, File, Folder, Type, X } from 'lucide-react'
import type { MaterialItem } from '../types'

interface MaterialsInputProps {
  materials: MaterialItem[]
  onChange: (m: MaterialItem[]) => void
  disabled?: boolean
  hint?: string
}

/** 加号添加行的可选类型（打字输入固定走 text，不在此列） */
const ADD_TYPES: Array<{ value: MaterialItem['type']; label: string; icon: typeof Globe }> = [
  { value: 'url', label: '网址', icon: Globe },
  { value: 'file', label: '文件', icon: File },
  { value: 'folder', label: '文件夹', icon: Folder },
]

const TYPE_META: Record<MaterialItem['type'], { label: string; icon: typeof Globe; cls: string }> = {
  text: { label: '文字', icon: Type, cls: 'bg-accent/15 text-accent' },
  url: { label: '网址', icon: Globe, cls: 'bg-success/15 text-success' },
  file: { label: '文件', icon: File, cls: 'bg-warning/15 text-warning' },
  folder: { label: '文件夹', icon: Folder, cls: 'bg-error/15 text-error' },
}

export function MaterialsInput({
  materials,
  onChange,
  disabled = false,
  hint = '输入材料内容，回车添加（选题时一并参考）',
}: MaterialsInputProps) {
  const [draft, setDraft] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [addType, setAddType] = useState<MaterialItem['type']>('url')
  const [addValue, setAddValue] = useState('')

  const add = (m: MaterialItem) => {
    if (!m.content.trim()) return
    onChange([...materials, m])
    setDraft('')
    setAddValue('')
  }

  const remove = (idx: number) => {
    onChange(materials.filter((_, i) => i !== idx))
  }

  return (
    <div className="flex flex-col gap-2">
      {/* 对话式输入行：打字输入 + 加号 */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && draft.trim() && !disabled) {
              e.preventDefault()
              add({ type: 'text', content: draft })
            }
          }}
          placeholder={hint}
          disabled={disabled}
          className="min-w-0 flex-1 rounded-md border border-line bg-bg-3 px-2.5 py-1.5 text-xs text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
        />
        <button
          type="button"
          onClick={() => {
            setAddOpen(!addOpen)
            setAddValue('')
          }}
          disabled={disabled}
          className={`grid h-7 w-7 shrink-0 place-items-center rounded-md border transition-colors disabled:opacity-40 ${
            addOpen
              ? 'border-accent bg-accent/15 text-accent'
              : 'border-line text-fg-2 hover:bg-bg-3 hover:text-fg-1'
          }`}
          title="添加网址 / 文件 / 文件夹（本地路径字符串即可）"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* 加号展开行：类型选择 + 路径/URL 输入 */}
      {addOpen && (
        <div className="flex flex-col gap-1.5 rounded-md border border-dashed border-line bg-bg-1 p-2">
          <div className="flex items-center gap-1">
            {ADD_TYPES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                onClick={() => setAddType(value)}
                disabled={disabled}
                className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] transition-colors ${
                  addType === value
                    ? 'bg-accent/20 text-accent ring-1 ring-accent/40'
                    : 'text-fg-3 hover:bg-bg-3 hover:text-fg-2'
                }`}
              >
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
            <span className="ml-1 text-[10px] text-fg-3">粘贴 URL 或本地路径</span>
          </div>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              value={addValue}
              onChange={(e) => setAddValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && addValue.trim() && !disabled) {
                  e.preventDefault()
                  add({ type: addType, content: addValue })
                }
              }}
              placeholder={
                addType === 'url'
                  ? 'https://…'
                  : addType === 'file'
                    ? 'D:/materials/notes.md'
                    : 'D:/materials/cases'
              }
              disabled={disabled}
              className="min-w-0 flex-1 rounded-md border border-line bg-bg-3 px-2.5 py-1.5 text-xs text-fg-1 placeholder:text-fg-3 focus:border-accent focus:outline-none disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => add({ type: addType, content: addValue })}
              disabled={disabled || !addValue.trim()}
              className="shrink-0 rounded-md border border-accent/50 bg-accent/10 px-2.5 py-1.5 text-[10px] font-medium text-accent transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              添加
            </button>
          </div>
        </div>
      )}

      {/* 已添加材料 chips */}
      {materials.length > 0 && (
        <div className="flex flex-col gap-1">
          {materials.map((m, i) => {
            const meta = TYPE_META[m.type] ?? TYPE_META.text
            const Icon = meta.icon
            return (
              <div
                key={`${m.type}-${i}`}
                className="flex items-center gap-2 rounded-md border border-line bg-bg-1 px-2 py-1.5"
              >
                <span
                  className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${meta.cls}`}
                >
                  <Icon className="h-3 w-3" />
                  {meta.label}
                </span>
                <span
                  className="min-w-0 flex-1 truncate font-mono-id text-[11px] text-fg-2"
                  title={m.content}
                >
                  {m.content}
                </span>
                <button
                  type="button"
                  onClick={() => remove(i)}
                  disabled={disabled}
                  className="shrink-0 rounded p-0.5 text-fg-3 transition-colors hover:bg-bg-3 hover:text-error disabled:opacity-40"
                  title="移除"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )
          })}
          <p className="text-[10px] text-fg-3">
            共 {materials.length} 条材料，将随引擎派卡注入选题卡 comment
          </p>
        </div>
      )}
    </div>
  )
}

export default MaterialsInput
