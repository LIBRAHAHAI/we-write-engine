/**
 * Logs — 日志页（后端内存日志：advance / decision / create 留痕）
 *
 * 数据：GET /api/logs（倒序最新在前），5s 自动刷新（标签页隐藏暂停）。
 * 注意：内存日志随 API 进程重启清空（不落盘）。
 */
import { useEffect, useRef, useState } from 'react'
import { ScrollText } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { EmptyState } from '../components/ui/EmptyState'
import { Spinner } from '../components/ui/Spinner'
import type { LogEntry } from '../types'

const REFRESH_MS = 5_000

const LEVEL_CLS: Record<string, string> = {
  info: 'bg-accent/15 text-accent ring-1 ring-accent/30',
  warn: 'bg-warning/15 text-warning ring-1 ring-warning/30',
  error: 'bg-error/15 text-error ring-1 ring-error/30',
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

export default function Logs() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const load = async () => {
      setLoading(true)
      try {
        const data = await api.getLogs()
        if (!mountedRef.current) return
        setLogs(data)
        setError(null)
      } catch (e) {
        if (!mountedRef.current) return
        setError(
          e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e),
        )
      } finally {
        if (mountedRef.current) setLoading(false)
      }
    }
    void load()

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
  }, [])

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-fg-1">日志</h1>
          <p className="mt-0.5 text-xs text-fg-3">
            推进 / 拍板 / 新建操作的留痕记录（后端内存，随 API 进程重启清空）。
            每 5 秒自动刷新。
          </p>
        </div>
        {loading && <Spinner />}
      </div>

      {/* 错误 */}
      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 p-3 text-xs text-error">
          加载日志失败：{error}
        </div>
      )}

      {/* 空状态 */}
      {!loading && logs.length === 0 && !error && (
        <EmptyState
          title="暂无日志"
          hint="在「管理文章」页推进或拍板后，记录会出现在这里。"
        />
      )}

      {/* 日志列表（后端已倒序：最新在上） */}
      {logs.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line bg-bg-2">
          <table className="w-full text-left text-xs">
            <thead className="bg-bg-3 text-fg-3">
              <tr>
                <th className="w-44 px-3 py-2 font-normal">时间</th>
                <th className="w-16 px-3 py-2 font-normal">level</th>
                <th className="w-24 px-3 py-2 font-normal">来源</th>
                <th className="px-3 py-2 font-normal">消息</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log, i) => (
                <tr
                  key={`${log.timestamp}-${i}`}
                  className="border-t border-line align-top"
                >
                  <td className="px-3 py-1.5 font-mono-id text-fg-3">
                    {formatTime(log.timestamp)}
                  </td>
                  <td className="px-3 py-1.5">
                    <span
                      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        LEVEL_CLS[log.level] ?? LEVEL_CLS.info
                      }`}
                    >
                      {log.level}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 font-mono-id text-fg-2">
                    <span className="inline-flex items-center gap-1">
                      <ScrollText className="h-3 w-3" />
                      {log.source}
                    </span>
                  </td>
                  <td className="whitespace-pre-wrap break-words px-3 py-1.5 text-fg-1">
                    {log.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
