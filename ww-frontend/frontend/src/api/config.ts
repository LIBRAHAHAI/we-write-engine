// 运行时配置（API 地址）—— Settings 页可改，持久化到 localStorage

const KEY_API_BASE = 'ww-frontend:api-base'

export const DEFAULT_API_BASE = 'http://localhost:8670'

export function getApiBase(): string {
  try {
    const v = localStorage.getItem(KEY_API_BASE)
    if (v) return v.replace(/\/+$/, '')
  } catch {
    /* localStorage 不可用时忽略 */
  }
  return DEFAULT_API_BASE
}

export function setApiBase(url: string): void {
  try {
    localStorage.setItem(KEY_API_BASE, url.replace(/\/+$/, ''))
  } catch {
    /* ignore */
  }
}
