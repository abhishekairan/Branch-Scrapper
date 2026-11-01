// Which browser tiles are on screen, and therefore which ones the sidecar should stream.
//
// Rules (plan section 6): only visible tiles stream; List layout, a hidden/minimised window, or leaving the
// Browsers screen stops everything (`browser.view {indices: []}`). Grid ~2 fps at <= 480 px wide; Single
// ~12 fps at the canvas' device-pixel width (<= 1280).

import { api } from './client.ts'
import { run } from './app.svelte.ts'
import type { Frame } from './protocol.ts'

export type Layout = 'grid' | 'single' | 'list'
const LAYOUTS = ['grid', 'single', 'list'] as const

export const GRID_FPS = 2
/** frame width asked for per tile is at least this (CSS px x dpr is used when the tile is wider) ... */
export const GRID_MAX_WIDTH = 480
/** ... and at most this: Grid streams are 2 fps thumbnails, 1-column tiles get wider frames but not Single-sized ones */
export const GRID_MAX_WIDTH_CAP = 960
export const SINGLE_FPS = 12
export const SINGLE_MAX_WIDTH_CAP = 1280
const DEBOUNCE_MS = 200

// ---------------------------------------------------------------- grid options (per viewer, localStorage)

export type GridColumns = 0 | 1 | 2 | 3 | 4 // 0 = auto (responsive)
export type GridDensity = 'compact' | 'comfortable' | 'large'
export interface GridPrefs {
  columns: GridColumns
  density: GridDensity
  showUrl: boolean
  showStatus: boolean
  showRam: boolean
}
export const DEFAULT_GRID_PREFS: GridPrefs = {
  columns: 0,
  density: 'comfortable',
  showUrl: true,
  showStatus: true,
  showRam: true,
}
const GRID_KEY = 'gs.grid'

/** Validate whatever came out of storage; unknown or malformed fields fall back to the defaults. */
export function parseGridPrefs(raw: string | null): GridPrefs {
  const out: GridPrefs = { ...DEFAULT_GRID_PREFS }
  if (!raw) return out
  try {
    const o = JSON.parse(raw) as Record<string, unknown>
    if (o && typeof o === 'object') {
      if (o.columns === 0 || o.columns === 1 || o.columns === 2 || o.columns === 3 || o.columns === 4) out.columns = o.columns
      if (o.density === 'compact' || o.density === 'comfortable' || o.density === 'large') out.density = o.density
      if (typeof o.showUrl === 'boolean') out.showUrl = o.showUrl
      if (typeof o.showStatus === 'boolean') out.showStatus = o.showStatus
      if (typeof o.showRam === 'boolean') out.showRam = o.showRam
    }
  } catch {
    /* corrupt value: defaults */
  }
  return out
}

function loadGridPrefs(): GridPrefs {
  try {
    return parseGridPrefs(localStorage.getItem(GRID_KEY))
  } catch {
    return { ...DEFAULT_GRID_PREFS }
  }
}

export const gridPrefs = $state<GridPrefs>(loadGridPrefs())

export function setGridPrefs(patch: Partial<GridPrefs>): void {
  Object.assign(gridPrefs, patch)
  try {
    localStorage.setItem(GRID_KEY, JSON.stringify({ ...gridPrefs }))
  } catch {
    /* storage unavailable: the choice still applies for this session */
  }
}

export function resetGridPrefs(): void {
  setGridPrefs({ ...DEFAULT_GRID_PREFS })
}

function loadLayout(): Layout {
  try {
    const v = localStorage.getItem('gs.layout')
    if (v && (LAYOUTS as readonly string[]).includes(v)) return v as Layout
  } catch {
    /* storage unavailable */
  }
  return 'grid'
}

export const view = $state({
  layout: loadLayout(),
  /** worker shown in Single layout */
  selected: 0,
  /** tile index -> currently intersecting the viewport (reported by IntersectionObservers) */
  visible: {} as Record<number, boolean>,
  /** CSS width of the Single canvas box, for choosing max_width */
  singleCssWidth: 0,
  /** tile index -> CSS width of its drawn preview (Grid), for choosing max_width */
  tileCssWidth: {} as Record<number, number>,
  /** the Browsers screen is mounted */
  screenActive: false,
  docVisible: typeof document === 'undefined' ? true : document.visibilityState === 'visible',
  /** last answer of browser.view */
  streaming: [] as number[],
  waiting: [] as number[],
})

export function setLayout(layout: Layout): void {
  view.layout = layout
  try {
    localStorage.setItem('gs.layout', layout)
  } catch {
    /* storage unavailable */
  }
}

export function reportVisible(index: number, on: boolean): void {
  if (view.visible[index] !== on) view.visible[index] = on
}

export function reportTileWidth(index: number, cssWidth: number): void {
  const w = Math.round(cssWidth)
  if (view.tileCssWidth[index] !== w) view.tileCssWidth[index] = w
}

/** CSS px -> frame width to request: device pixels, in coarse steps so a resize does not re-send on every pixel. */
export function frameWidthFor(cssWidth: number, min: number, max: number, fallback: number): number {
  const dpr = typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1
  const px = Math.ceil((cssWidth * dpr) / 40) * 40
  return Math.max(min, Math.min(max, px || fallback))
}

export interface ViewRequest {
  indices: number[]
  fps: number
  maxWidth: number
}

/** The view set the current UI state calls for. Exported for tests/diagnostics. */
export function desiredView(): ViewRequest {
  const none: ViewRequest = { indices: [], fps: GRID_FPS, maxWidth: GRID_MAX_WIDTH }
  if (!view.screenActive || !view.docVisible || view.layout === 'list') return none
  const count = run.workers.length
  if (view.layout === 'grid') {
    const indices = Object.entries(view.visible)
      .filter(([i, on]) => on && Number(i) < count)
      .map(([i]) => Number(i))
      .sort((a, b) => a - b)
    // one width for all tiles (they share a column width): the widest visible one wins
    let w = 0
    for (const i of indices) w = Math.max(w, view.tileCssWidth[i] ?? 0)
    return { indices, fps: GRID_FPS, maxWidth: frameWidthFor(w, GRID_MAX_WIDTH, GRID_MAX_WIDTH_CAP, GRID_MAX_WIDTH) }
  }
  // single
  const i = view.selected
  if (i >= count || !view.visible[i]) return none
  return { indices: [i], fps: SINGLE_FPS, maxWidth: frameWidthFor(view.singleCssWidth, 160, SINGLE_MAX_WIDTH_CAP, 640) }
}

// ---------------------------------------------------------------- frame fan-out

const sinks = new Map<number, Set<(f: Frame) => void>>()

/** Receive frames of one slot. Returns the unsubscribe function. */
export function onSlotFrame(slot: number, fn: (f: Frame) => void): () => void {
  let set = sinks.get(slot)
  if (!set) sinks.set(slot, (set = new Set()))
  set.add(fn)
  return () => set.delete(fn)
}

// ---------------------------------------------------------------- wiring

let started = false

let lastWanted: number[] = []

export function initView(): void {
  if (started) return
  started = true

  api.onFrame((f) => {
    // A slot that was "pending" when browser.view replied starts streaming later without a reply;
    // the first frame is the proof, so promote it here (otherwise the toolbar says nothing streams).
    if (!view.streaming.includes(f.slot) && lastWanted.includes(f.slot)) {
      view.streaming = [...view.streaming, f.slot].sort((a, b) => a - b)
      view.waiting = view.waiting.filter((i) => i !== f.slot)
    }
    const set = sinks.get(f.slot)
    if (set) for (const fn of set) fn(f)
  })

  document.addEventListener('visibilitychange', () => {
    view.docVisible = document.visibilityState === 'visible'
  })

  let timer: ReturnType<typeof setTimeout> | null = null
  let lastSent = JSON.stringify({ indices: [] })

  $effect.root(() => {
    $effect(() => {
      const want = desiredView()
      const sig = JSON.stringify(want)
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        if (sig === lastSent) return
        lastSent = sig
        lastWanted = want.indices
        api
          .setView(want.indices, want.fps, want.maxWidth)
          .then((r) => {
            if (r) {
              view.streaming = r.active
              view.waiting = r.pending
            }
          })
          .catch((e) => console.warn('browser.view failed', e))
        if (want.indices.length === 0) {
          view.streaming = []
          view.waiting = []
        }
      }, DEBOUNCE_MS)
    })
  })
}
