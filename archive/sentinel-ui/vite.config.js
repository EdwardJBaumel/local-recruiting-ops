import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Use 127.0.0.1 (not "localhost") because on Windows / Node 17+
// "localhost" resolves to ::1 (IPv6) first, but the Python backend
// in server.py binds IPv4 only. That mismatch is the #1 cause of
// the dashboard sitting in "DEMO" mode while the backend is up.
const API_TARGET = process.env.SENTINEL_API || 'http://127.0.0.1:8099'

export default defineConfig({
  plugins: [react()],
  server: {
    // Explicit IPv4 bind. Without this, Node 17+ defaults to IPv6 (::1)
    // which makes http://localhost:3000 work but http://127.0.0.1:3000
    // silently 404. That asymmetry breaks start.ps1's Test-Port3000
    // check (which probes IPv4) so the launcher never fires the
    // browser-open. Binding 127.0.0.1 on both ends keeps everything
    // symmetric and gets the auto-open working again.
    host: '127.0.0.1',
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err, req) => {
            // Surfaces the real reason in the Vite terminal instead of
            // failing silently. Helpful when debugging "DEMO" mode.
            console.error(`[proxy] ${req.method} ${req.url} -> ${err.code || err.message}`)
          })
        },
      },
    },
  },
})
