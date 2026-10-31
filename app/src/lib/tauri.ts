// The only module that talks to the Tauri shell. Everything else uses lib/client.ts and lib/app.svelte.ts.

import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type { ConnectInfo } from './api.ts'

export interface SidecarInfo extends ConnectInfo {
  version: string
}

export const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

/**
 * Connection details of the sidecar. The Rust side waits (up to 25 s) while the sidecar is starting and
 * rejects with a readable message if it failed or exited.
 *
 * Plain-browser development (`npm run dev` opened in a normal browser, no shell): pass
 * `?port=<n>&token=<secret>` of a sidecar you started by hand. Dev builds only.
 */
export async function getSidecarInfo(): Promise<SidecarInfo> {
  if (inTauri) return invoke<SidecarInfo>('sidecar_info')
  if (import.meta.env.DEV) {
    const q = new URLSearchParams(location.search)
    const port = Number(q.get('port'))
    const token = q.get('token')
    if (port > 0 && token) return { port, token, version: 'dev' }
  }
  throw new Error('not running inside the Ghost Scraper shell')
}

/**
 * Open an https URL in the system browser. Only https is allowed (also enforced by the capability scope),
 * since URLs come from scraped data. Returns false outside the shell or for non-https URLs.
 */
export async function openExternal(url: string): Promise<boolean> {
  if (!/^https:\/\//i.test(url)) return false
  if (!inTauri) {
    window.open(url, '_blank', 'noopener')
    return true
  }
  const { openUrl } = await import('@tauri-apps/plugin-opener')
  await openUrl(url)
  return true
}

/** Ask the shell to restart the sidecar (fresh process, fresh token). */
export function restartSidecar(): Promise<SidecarInfo> {
  if (!inTauri) return Promise.reject(new Error('not running inside the Ghost Scraper shell'))
  return invoke<SidecarInfo>('restart_sidecar')
}

export interface ShellHandlers {
  onStarting?: () => void
  onReady?: (info: SidecarInfo) => void
  onExited?: (info: { code: number | null; message: string }) => void
}

/** Subscribe to the shell's sidecar lifecycle events. Returns a function that unsubscribes. */
export async function listenShell(h: ShellHandlers): Promise<() => void> {
  if (!inTauri) return () => {}
  const offs: UnlistenFn[] = await Promise.all([
    listen('sidecar-starting', () => h.onStarting?.()),
    listen<SidecarInfo>('sidecar-ready', (e) => h.onReady?.(e.payload)),
    listen<{ code: number | null; message: string }>('sidecar-exited', (e) => h.onExited?.(e.payload)),
  ])
  return () => offs.forEach((f) => f())
}

/**
 * Native "save as" dialog. Returns the chosen absolute path or null if cancelled. (Used by the Data
 * screen's export; `export.write` needs an absolute path with a matching extension.)
 */
export async function pickSavePath(opts: {
  defaultPath?: string
  filters?: { name: string; extensions: string[] }[]
  title?: string
}): Promise<string | null> {
  const { save } = await import('@tauri-apps/plugin-dialog')
  return save(opts)
}

/**
 * Native "open file" dialog (multi-select). Returns absolute paths, [] if cancelled. Needs the
 * `dialog:allow-open` permission in src-tauri/capabilities; without it the shell rejects the call.
 */
export async function pickOpenPaths(opts: {
  title?: string
  filters?: { name: string; extensions: string[] }[]
}): Promise<string[]> {
  const { open } = await import('@tauri-apps/plugin-dialog')
  const r = await open({ ...opts, multiple: true, directory: false })
  if (!r) return []
  return Array.isArray(r) ? r : [r]
}
