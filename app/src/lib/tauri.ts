// The only module that talks to the Tauri shell. Everything else uses lib/client.ts and lib/app.svelte.ts.

import { Channel, invoke } from '@tauri-apps/api/core'
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
  throw new Error('not running inside the Branch Scrapper shell')
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
  if (!inTauri) return Promise.reject(new Error('not running inside the Branch Scrapper shell'))
  return invoke<SidecarInfo>('restart_sidecar')
}

// ------------------------------------------------------------------ in-app updates (src-tauri/src/updates.rs)

/** An update found by the shell. `body` is the release notes: untrusted text, render it as text only. */
export interface UpdateInfo {
  version: string
  currentVersion: string
  date?: string | null
  body?: string | null
}

/** `downloading` -> `stopping` (backend shut down) -> `installing` (installer starts, the app exits). */
export interface InstallProgress {
  phase: 'downloading' | 'stopping' | 'installing'
  downloaded: number
  total: number | null
}

/** Tauri rejects an invoke with the command's error value (a string here), not with an Error. */
function shellError(e: unknown): Error {
  return e instanceof Error ? e : new Error(String(e))
}

/** Version of the running shell (tauri.conf.json). */
export async function shellVersion(): Promise<string> {
  if (!inTauri) return ''
  return invoke<string>('app_version')
}

/** Ask the update endpoint for a newer version. null = up to date. Rejects with a readable sentence. */
export async function checkForUpdate(): Promise<UpdateInfo | null> {
  if (!inTauri) throw new Error('Updates are only available in the installed app.')
  try {
    return await invoke<UpdateInfo | null>('check_for_update')
  } catch (e) {
    throw shellError(e)
  }
}

/**
 * Download the update found by the last `checkForUpdate`, verify its signature, stop the backend, run the
 * installer. On success the app exits (the installer relaunches it) so this promise never resolves.
 */
export async function installUpdate(onProgress: (p: InstallProgress) => void): Promise<void> {
  const channel = new Channel<InstallProgress>()
  channel.onmessage = onProgress
  try {
    await invoke('install_update', { onEvent: channel })
  } catch (e) {
    throw shellError(e)
  }
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
 * Native "choose a folder" dialog. Returns the absolute path or null if cancelled. Uses the same
 * `dialog:allow-open` permission as pickOpenPaths. In a plain browser (dev harness) there is no dialog: the
 * test driver may provide `window.__pickFolder`.
 */
export async function pickFolder(opts: { title?: string; defaultPath?: string }): Promise<string | null> {
  if (!inTauri) {
    const hook = import.meta.env.DEV ? (window as { __pickFolder?: () => string | null }).__pickFolder : undefined
    return hook ? hook() : null
  }
  const { open } = await import('@tauri-apps/plugin-dialog')
  const r = await open({ ...opts, multiple: false, directory: true })
  return typeof r === 'string' ? r : null
}

/** Show a file in the system file manager (selected in its folder). Needs `opener:allow-reveal-item-in-dir`. */
export async function revealInFolder(path: string): Promise<boolean> {
  if (!inTauri) return false
  const { revealItemInDir } = await import('@tauri-apps/plugin-opener')
  await revealItemInDir(path)
  return true
}

/**
 * Make the native window chrome (title bar) follow the app theme; null = follow the system. Needs
 * `core:window:allow-set-theme`. Best effort: never throws, does nothing outside the shell.
 */
export async function setWindowTheme(mode: 'light' | 'dark' | 'system'): Promise<void> {
  if (!inTauri) return
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    await getCurrentWindow().setTheme(mode === 'system' ? null : mode)
  } catch (e) {
    console.warn('could not set the window theme', e)
  }
}

/**
 * Zoom the app's own page (1 = 100%), like browser zoom: layout, text and pointer coordinates all scale together.
 * Needs `core:webview:allow-set-webview-zoom`. Returns false outside the shell or when the call failed.
 */
export async function setWebviewZoom(factor: number): Promise<boolean> {
  if (!inTauri) return false
  try {
    const { getCurrentWebview } = await import('@tauri-apps/api/webview')
    await getCurrentWebview().setZoom(factor)
    return true
  } catch (e) {
    console.warn('could not set the zoom level', e)
    return false
  }
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
