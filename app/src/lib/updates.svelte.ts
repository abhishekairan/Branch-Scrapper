// In-app updates: state and actions (Svelte 5 runes in a module). The work is done by the shell
// (src-tauri/src/updates.rs, see lib/tauri.ts); this module decides *when* to check and keeps what the UI shows.
//
// * Automatic check: once per app start, after the backend is connected, at most once per 24 h, unless turned off
//   (Settings > Updates). The time of the last successful check and the switch live in localStorage; the switch is
//   also stored in the sidecar config (`check_updates`) so it survives a reset of the WebView profile
//   (see `setCheckUpdates` / `reconcileCheckUpdates` in app.svelte.ts).
// * A failed check never interrupts the user: an automatic one is silent, a manual one shows the message in
//   Settings. "Check now" retries.

import { checkForUpdate, inTauri, installUpdate, shellVersion } from './tauri.ts'
import type { UpdateInfo } from './tauri.ts'

const AUTO_KEY = 'bs.checkUpdates' // '1' | '0'
const LAST_KEY = 'bs.updateChecked' // ms since epoch of the last successful check
const DAY_MS = 24 * 60 * 60 * 1000

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable: the setting just does not persist in this WebView */
  }
}

/** The switch as stored in this WebView; null when it was never set (first start, WebView profile reset). */
export function storedCheckUpdates(): boolean | null {
  const v = read(AUTO_KEY)
  return v === '1' ? true : v === '0' ? false : null
}

function storedLastChecked(): number {
  const n = Number(read(LAST_KEY))
  return Number.isFinite(n) && n > 0 ? n : 0
}

export type InstallPhase = 'idle' | 'downloading' | 'stopping' | 'installing'

export const upd = $state({
  /** only the installed app (the Tauri shell) can update itself */
  supported: inTauri,
  /** version of the running app (the shell); '' until known */
  currentVersion: '',
  /** "Automatically check for updates" */
  auto: storedCheckUpdates() ?? true,
  checking: false,
  /** ms since epoch of the last successful check (0 = never) */
  lastChecked: storedLastChecked(),
  /** outcome of the last check: '' (none yet), 'current', 'available' or 'error' */
  result: '' as '' | 'current' | 'available' | 'error',
  error: '',
  available: null as UpdateInfo | null,
  /** the user pressed "Later" (until the next manual check or app start) */
  dismissed: false,
  notesOpen: false,
  phase: 'idle' as InstallPhase,
  downloaded: 0,
  total: null as number | null,
  installError: '',
})

/** Store the switch locally (the sidecar copy is written by `setCheckUpdates` in app.svelte.ts). */
export function applyCheckUpdates(on: boolean): void {
  upd.auto = on
  write(AUTO_KEY, on ? '1' : '0')
}

function due(): boolean {
  const now = Date.now()
  return upd.lastChecked === 0 || upd.lastChecked > now || now - upd.lastChecked >= DAY_MS
}

let startupDone = false

/** Called once the backend is connected and the config is loaded. Silent: failures are not shown. */
export function startupCheck(): void {
  if (startupDone || !upd.supported) return
  startupDone = true
  // the version the updater compares against is the shell's (tauri.conf.json), not the sidecar's
  void shellVersion()
    .then((v) => (upd.currentVersion = v))
    .catch(() => {})
  if (upd.auto && due()) void checkNow(false)
}

export async function checkNow(manual: boolean): Promise<void> {
  if (!upd.supported || upd.checking || upd.phase !== 'idle') return
  upd.checking = true
  try {
    const found = await checkForUpdate()
    upd.lastChecked = Date.now()
    write(LAST_KEY, String(upd.lastChecked))
    upd.error = ''
    if (manual) upd.installError = '' // a manual re-check starts afresh
    upd.available = found
    upd.result = found ? 'available' : 'current'
    if (found && manual) upd.dismissed = false
  } catch (e) {
    upd.result = 'error'
    upd.error = e instanceof Error ? e.message : String(e)
  } finally {
    upd.checking = false
  }
}

export function dismissUpdate(): void {
  upd.dismissed = true
  upd.notesOpen = false
  upd.installError = '' // "Dismiss" on a failed install clears the message too
}

/**
 * Download, verify, stop the backend, install. The caller makes sure no run is active. On success the app
 * exits and the installer relaunches it, so this only returns after a failure (shown in `installError`).
 */
export async function installNow(): Promise<void> {
  if (!upd.available || upd.phase !== 'idle') return
  upd.installError = ''
  upd.downloaded = 0
  upd.total = null
  upd.phase = 'downloading'
  try {
    await installUpdate((p) => {
      upd.phase = p.phase
      upd.downloaded = p.downloaded
      if (p.total !== null) upd.total = p.total
    })
    upd.installError = 'The installer did not start.'
  } catch (e) {
    upd.installError = e instanceof Error ? e.message : String(e)
  }
  upd.phase = 'idle'
}
