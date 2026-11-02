// UI size (Settings > Interface size, Ctrl + = / Ctrl + - / Ctrl + 0). Uses the WebView's own zoom, so text,
// spacing, the live browser canvases and mouse coordinates all scale together; nothing in the CSS needs to know.
//
// Remembered per computer in localStorage ('bs.zoom'). WebView zoom is not kept across restarts by the shell, so
// initZoom() re-applies it at startup (the first frame can show at 100% for a moment).
// Outside the Tauri shell (plain-browser development) this does nothing; use the browser's own zoom there.

import { inTauri, setWebviewZoom } from './tauri.ts'

// capped at 175%: the window's minimum width (1000 px) leaves ~570 CSS px of layout there, about the least the
// toolbars can live with
export const ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75] as const
export const DEFAULT_ZOOM = 1
const KEY = 'bs.zoom'

export const zoom = $state({ factor: readStoredZoom(), supported: inTauri })

/** The nearest allowed step; anything unreadable is 100%. */
export function normaliseZoom(v: unknown): number {
  const n = typeof v === 'string' ? Number(v) : v
  if (typeof n !== 'number' || !Number.isFinite(n)) return DEFAULT_ZOOM
  let best: number = DEFAULT_ZOOM
  for (const s of ZOOM_STEPS) if (Math.abs(s - n) < Math.abs(best - n)) best = s
  return best
}

function readStoredZoom(): number {
  try {
    return normaliseZoom(localStorage.getItem(KEY) ?? DEFAULT_ZOOM)
  } catch {
    return DEFAULT_ZOOM
  }
}

export function formatZoom(f: number): string {
  return `${Math.round(f * 100)}%`
}

export async function setZoom(factor: number): Promise<void> {
  const f = normaliseZoom(factor)
  zoom.factor = f
  try {
    localStorage.setItem(KEY, String(f))
  } catch {
    /* storage unavailable: applies for this session only */
  }
  await setWebviewZoom(f)
}

/** One step bigger (+1) or smaller (-1). */
export function stepZoom(dir: 1 | -1): Promise<void> {
  const i = ZOOM_STEPS.indexOf(normaliseZoom(zoom.factor) as (typeof ZOOM_STEPS)[number])
  const j = Math.max(0, Math.min(ZOOM_STEPS.length - 1, i + dir))
  return setZoom(ZOOM_STEPS[j])
}

let started = false

/** Call once at startup: re-apply the saved size and listen for the keyboard shortcuts. */
export function initZoom(): void {
  if (started || !inTauri) return
  started = true
  if (zoom.factor !== DEFAULT_ZOOM) void setWebviewZoom(zoom.factor)
  window.addEventListener('keydown', (e) => {
    // a browser canvas under "Take control" forwards keys to the remote page and marks them handled
    if (e.defaultPrevented || !(e.ctrlKey || e.metaKey) || e.altKey) return
    if (e.key === '=' || e.key === '+') void stepZoom(1)
    else if (e.key === '-' || e.key === '_') void stepZoom(-1)
    else if (e.key === '0') void setZoom(DEFAULT_ZOOM)
    else return
    e.preventDefault()
  })
}
