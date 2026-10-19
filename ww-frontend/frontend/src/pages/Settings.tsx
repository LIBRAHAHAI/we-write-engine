/**
 * Settings — 设置页（占位）
 *
 * 当前内容：引擎连接信息（/api/health 实测）+ 端口表。
 * 预留：API 地址切换、轮询间隔等运行时配置项后续迭代接入。
 */
import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import { getApiBase } from '../api/config'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import type { HealthInfo } from '../types'

/** 端口表（三服务分工） */
const PORTS = [
  { port: 8660, name: 'ww_panel', desc: '引擎自带仪表盘（本面板仪表盘页 iframe 嵌入）' },
  { port: 8670, name: 'ww-frontend 后端 API', desc: 'FastAPI 包装层（实例/推进/拍板/流水线/日志）' },
  { port: 5175, name: 'ww-frontend 前端', desc: 'React+Vite 面板（vite preview 加载 dist）' },
]

export default function Settings() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    api
      .health()
      .then((h) => !cancelled && setHealth(h))
      .catch((e: unknown) => {
        if (cancelled) return
        setHealth(null)
        if (e instanceof ApiError || e instanceof Error)
          console.error('health 探测失败:', e.message)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 p-6">
      <div>
        <h1 className="text-lg font-semibold text-fg-1">设置</h1>
        <p className="mt-0.5 text-xs text-fg-3">
          设置项开发中（占位）。以下为引擎连接与端口信息。
        </p>
      </div>

      {/* 引擎连接 */}
      <Card className="p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-fg-1">引擎连接</h2>
          {loading && <Spinner className="h-4 w-4" />}
        </div>
        <dl className="mt-3 grid grid-cols-[100px_1fr] gap-y-2 text-xs">
          <dt className="text-fg-3">API 地址</dt>
          <dd className="font-mono-id text-fg-1">{getApiBase()}</dd>
          <dt className="text-fg-3">引擎目录</dt>
          <dd className="font-mono-id text-fg-1">
            {health?.engine_dir ?? '（不可达）'}
          </dd>
          <dt className="text-fg-3">engine.py</dt>
          <dd className={health?.engine_py_exists ? 'text-success' : 'text-error'}>
            {health ? (health.engine_py_exists ? '存在 ✓' : '缺失 ✕') : '—'}
          </dd>
          <dt className="text-fg-3">pipeline.yaml</dt>
          <dd className={health?.pipeline_exists ? 'text-success' : 'text-error'}>
            {health ? (health.pipeline_exists ? '存在 ✓' : '缺失 ✕') : '—'}
          </dd>
          <dt className="text-fg-3">ww_panel</dt>
          <dd className="font-mono-id text-fg-1">
            {health?.panel_url ?? 'http://127.0.0.1:8660/'}
          </dd>
        </dl>
      </Card>

      {/* 端口表 */}
      <Card className="overflow-hidden">
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold text-fg-1">
          端口一览
        </h2>
        <table className="w-full text-left text-xs">
          <thead className="bg-bg-3 text-fg-3">
            <tr>
              <th className="w-16 px-4 py-2 font-normal">端口</th>
              <th className="w-40 px-4 py-2 font-normal">服务</th>
              <th className="px-4 py-2 font-normal">说明</th>
            </tr>
          </thead>
          <tbody>
            {PORTS.map((p) => (
              <tr key={p.port} className="border-t border-line">
                <td className="px-4 py-2 font-mono-id text-fg-1">{p.port}</td>
                <td className="px-4 py-2 text-fg-1">{p.name}</td>
                <td className="px-4 py-2 text-fg-2">{p.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* 关于 */}
      <p className="text-center text-[11px] text-fg-3">
        ww-frontend v1.0 — we-write-engine 包装层前端（React 19 + Vite 6 + Tailwind 4）
      </p>
    </div>
  )
}
