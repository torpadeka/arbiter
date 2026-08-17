import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs separately (uvicorn on 8000). Proxying keeps the browser on one
// origin, so there is no CORS surprise during a demo.
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly: on Windows "localhost" can resolve to ::1 only,
    // which makes 127.0.0.1 refuse connections.
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
