/**
 * Dashboard — 仪表盘页（iframe 嵌 ww_panel 8660 独立仪表盘）
 *
 * ww_panel 是引擎自带的老仪表盘（保持独立运行），本页只做嵌入壳：
 *   - 全高 iframe（ww_panel 自身有实例列表 / 拍板 / 预设等完整功能）
 *   - 面板离线时给重试提示（iframe 加载失败用户侧无感知，用 health 探测）
 */
import { useEffect, useState } from 'react'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { Spinner } from '../components/ui/Spinner'

const PANEL_URL = 'http://127.0.0.1:8660/'

export default function Dashboard() {
  const [panelUp, setPanelUp] = useState<boolean | null>(null)

  // 探测 ww_panel 是否在线（15s 轮询；离线时显示提示而不是白屏 iframe）
  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        await fetch(PANEL_URL, { mode: 'no-cors' })
        // no-cors 模式 fetch 成功返回（opaque）即认为端口有服务在响应
        if (!cancelled) setPanelUp(true)
      } catch {
        if (!cancelled) setPanelUp(false)
      }
    }
    void check()
    const timer = window.setInterval(check, 15_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  return (
    <div className="flex h-screen flex-col gap-3 p-4">
      {/* 页头 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-fg-1">仪表盘</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            引擎自带面板（ww_panel，端口 8660）嵌入视图，完整实例详情与无人值守设置请在此操作。
          </p>
        </div>
        <a
          href={PANEL_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs text-fg-2 transition-colors hover:bg-bg-3 hover:text-fg-1"
          title="在浏览器标签页中打开"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          独立打开
        </a>
      </div>

      {/* 面板离线提示 */}
      {panelUp === false && (
        <div className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          ww_panel（端口 8660）未响应——若未启动，请运行 launcher.py 或
          <code className="rounded bg-bg-3 px-1.5 py-0.5 font-mono-id">
            python ww_panel.py
          </code>
          ；启动后本页 15s 内自动恢复。
        </div>
      )}

      {/* iframe 嵌入区（全高） */}
      <div className="min-h-0 flex-1 overflow-hidden rounded-[10px] border border-line bg-bg-2">
        {panelUp === null ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-fg-3">
            <Spinner />
            正在连接 ww_panel…
          </div>
        ) : (
          <iframe
            src={PANEL_URL}
            title="ww_panel 仪表盘"
            className="h-full w-full border-0"
          />
        )}
      </div>
    </div>
  )
}
