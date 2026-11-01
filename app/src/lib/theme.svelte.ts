// Theme: System (default) / Light / Dark. The mode lives in three places that are kept in step:
//   * html[data-theme]  - what the CSS reads (absent = follow the system). public/theme-init.js sets it before the
//                         first paint from localStorage, so there is no flash.
//   * localStorage      - 'bs.theme', the fast copy for the next start.
//   * the sidecar config - `ui_theme`, so the choice survives a reset of the WebView profile (see app.svelte.ts).
// This module owns the first two plus the native title bar; it knows nothing about the sidecar.

import type { ThemeMode } from './protocol.ts'
import { setWindowTheme } from './tauri.ts'

export type { ThemeMode }
export const THEMES: readonly ThemeMode[] = ['system', 'light', 'dark']
const KEY = 'bs.theme'

export function isTheme(v: unknown): v is ThemeMode {
  return typeof v === 'string' && (THEMES as readonly string[]).includes(v)
}

/** What this WebView remembers; null when nothing is stored (first start, or the profile was reset). */
export function readStoredTheme(): ThemeMode | null {
  try {
    const v = localStorage.getItem(KEY)
    return isTheme(v) ? v : null
  } catch {
    return null
  }
}

export const theme = $state({ mode: readStoredTheme() ?? ('system' as ThemeMode) })

function setAttribute(mode: ThemeMode): void {
  const root = document.documentElement
  if (mode === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', mode)
}

/** Apply `mode` to the page, remember it locally and make the title bar follow. */
export function applyTheme(mode: ThemeMode): void {
  theme.mode = mode
  setAttribute(mode)
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* storage unavailable: the sidecar copy still has it */
  }
  void setWindowTheme(mode)
}

/**
 * Call once at startup. The page is normally already themed by theme-init.js; this repeats it (so a blocked or
 * failed script cannot leave the wrong theme) and syncs the native title bar.
 */
export function initTheme(): void {
  setAttribute(theme.mode)
  void setWindowTheme(theme.mode)
}
