/**
 * Sidebar — 240px 固定侧边栏（仿 flow-engine-frontend Sidebar 模式）
 *
 * 结构：顶部 Logo + 标题 → 主 CTA「+ 新建文章」
 *       → 5 区导航（仪表盘 / 管理文章 / 流水线 / 日志 / 设置）
 *       → 底部引擎状态灯 + 刷新按钮
 *
 * Props 由父组件传入：
 *   onRefresh      —— 触发页面 remount（key={refreshTick}）
 *   refreshing     —— 当前是否在反馈中（按钮动画）
 *   onCreateArticle —— 「+ 新建文章」点击回调（CreateArticleModal 实例由 App 管理）
 *
 * 内部状态：
 *   engineState  —— 15s 轮询 /api/health 得到的连通状态
 */
import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  FileText,
  BookMarked,
  GitBranch,
  ScrollText,
  Settings as SettingsIcon,
  Plus,
  RefreshCw,
} from 'lucide-react'
import { api, ApiError } from '../api/client'

type EngineState = 'checking' | 'ok' | 'err'

interface SidebarProps {
  onRefresh: () => void
  refreshing: boolean
  onCreateArticle: () => void
}

async function probe(): Promise<EngineState> {
  try {
    await api.health()
    return 'ok'
  } catch (e) {
    // 业务 4xx 也算"引擎在响应"
    if (e instanceof ApiError && e.status !== 0) return 'ok'
    return 'err'
  }
}

interface NavItem {
  to: string
  icon: typeof LayoutDashboard
  label: string
  end?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: '仪表盘', end: true },
  { to: '/manage', icon: FileText, label: '管理文章' },
  { to: '/templates', icon: BookMarked, label: '文章模板' },
  { to: '/pipeline', icon: GitBranch, label: '流水线' },
  { to: '/logs', icon: ScrollText, label: '日志' },
  { to: '/settings', icon: SettingsIcon, label: '设置' },
]

export function Sidebar({
  onRefresh,
  refreshing,
  onCreateArticle,
}: SidebarProps) {
  const [state_, setState] = useState<EngineState>('checking')

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      const s = await probe()
      if (!cancelled) setState(s)
    }
    void check()
    const timer = window.setInterval(check, 15_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const dotCls =
    state_ === 'ok'
      ? 'bg-success animate-pulse'
      : state_ === 'err'
        ? 'bg-error'
        : 'bg-fg-3'
  const labelCls =
    state_ === 'ok' ? 'text-success' : state_ === 'err' ? 'text-error' : 'text-fg-3'
  const label = state_ === 'ok' ? '引擎运行中' : state_ === 'err' ? '引擎离线' : '检测中…'

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-bg-2">
      {/* 顶部 Logo */}
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-sm font-bold text-white">
          WW
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-fg-1">写作引擎</p>
          <p className="truncate text-[10px] text-fg-3">we-write 控制台</p>
        </div>
      </div>

      {/* 主 CTA：+ 新建文章 */}
      <div className="flex flex-col gap-1.5 px-3 pt-3">
        <button
          type="button"
          onClick={onCreateArticle}
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/85"
        >
          <Plus className="h-4 w-4" />
          新建文章
        </button>
      </div>

      {/* 导航区 */}
      <nav className="mt-3 flex flex-col gap-0.5 px-2">
        {NAV_ITEMS.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex h-9 items-center gap-2.5 rounded-md px-3 text-sm transition-colors ${
                isActive
                  ? 'bg-bg-3 text-fg-1'
                  : 'text-fg-2 hover:bg-bg-3 hover:text-fg-1'
              }`
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* 底部：引擎状态 + 刷新 */}
      <div className="mt-auto border-t border-line p-3">
        <div className="flex items-center justify-between gap-2">
          <span className={`flex items-center gap-1.5 text-xs ${labelCls}`}>
            <span className={`h-2 w-2 rounded-full ${dotCls}`} />
            {label}
          </span>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="rounded-md p-1.5 text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1 disabled:opacity-40"
            title="刷新页面数据"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>
    </aside>
  )
}

export default Sidebar
