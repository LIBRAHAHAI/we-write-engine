/**
 * App — 路由壳 + 侧边栏布局（仿 flow-engine-frontend App 模式）
 *
 * 路由：
 *   /          → Dashboard（iframe 嵌 ww_panel 8660 仪表盘）
 *   /manage    → ManageArticles（实例卡片：状态徽章 + 当前进 + 推进 + 拍板对话框）
 *   /templates → Templates（005 需求1：文章模板列表，默认置顶 + 骨架标注）
 *   /pipeline  → Pipeline（默认模板步骤链展示）
 *   /logs      → Logs（后端内存日志：advance / decision / create 留痕）
 *   /settings  → Settings（引擎连接 + 端口表，占位）
 *   *          → 回到 Dashboard
 *
 * 布局：左 240px 固定 Sidebar + 右主内容区。
 * 「+ 新建文章」的 CreateArticleModal 实例由 App 顶层管理一处。
 *
 * 刷新机制：Sidebar 底部 ↻ 按钮触发 refreshTick++，页面通过 key={refreshTick}
 * 强制 remount 重新拉数据（比 location.reload() 轻）。
 */
import { useCallback, useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { ToastMount } from './components/Toast'
import { CreateArticleModal } from './components/CreateArticleModal'
import Dashboard from './pages/Dashboard'
import ManageArticles from './pages/ManageArticles'
import Templates from './pages/Templates'
import Pipeline from './pages/Pipeline'
import Logs from './pages/Logs'
import Settings from './pages/Settings'

export default function App() {
  const [refreshTick, setRefreshTick] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)

  const handleRefresh = useCallback(() => {
    setRefreshing(true)
    setRefreshTick((t) => t + 1)
    window.setTimeout(() => setRefreshing(false), 600)
  }, [])

  const openCreate = useCallback(() => setCreateOpen(true), [])
  const closeCreate = useCallback(() => setCreateOpen(false), [])
  const handleCreated = useCallback(() => {
    // 触发 ManageArticles 等页面重新拉取
    setRefreshTick((t) => t + 1)
  }, [])

  return (
    <div className="flex min-h-screen bg-bg-1">
      <Sidebar
        onRefresh={handleRefresh}
        refreshing={refreshing}
        onCreateArticle={openCreate}
      />
      <main className="flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Dashboard key={`dash-${refreshTick}`} />} />
          <Route
            path="/manage"
            element={<ManageArticles key={`manage-${refreshTick}`} />}
          />
          <Route path="/templates" element={<Templates key={`tpl-${refreshTick}`} />} />
          <Route path="/pipeline" element={<Pipeline key={`pipe-${refreshTick}`} />} />
          <Route path="/logs" element={<Logs key={`logs-${refreshTick}`} />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <ToastMount />
      <CreateArticleModal
        open={createOpen}
        onClose={closeCreate}
        onCreated={handleCreated}
      />
    </div>
  )
}
