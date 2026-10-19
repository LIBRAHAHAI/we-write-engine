import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    // 后端 API 在 8670，dev 时让浏览器直连（后端已配 CORS 全开）
  },
  preview: {
    port: 5175,
  },
})
