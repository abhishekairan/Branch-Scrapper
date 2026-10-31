import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

// The sidecar only accepts the Origin `http://localhost:<port>` from a dev server (never 127.0.0.1),
// and tauri.conf.json's devUrl is http://localhost:1420, so the host and port are fixed here.
export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: { host: 'localhost', port: 1420, strictPort: true },
  build: { target: 'es2022', chunkSizeWarningLimit: 600 },
})
