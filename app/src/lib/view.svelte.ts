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
export const GRID_MAX_WIDTH = 480
export const SINGLE_FPS = 12
export const SINGLE_MAX_WIDTH_CAP = 1280
const DEBOUNCE_MS = 200

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
    return { indices, fps: GRID_FPS, maxWidth: GRID_MAX_WIDTH }
  }
  // single
  const i = view.selected
  if (i >= count || !view.visible[i]) return none
  const dpr = typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1
  const px = Math.ceil((view.singleCssWidth * dpr) / 40) * 40 // coarse steps: avoid re-sending on every pixel
  return { indices: [i], fps: SINGLE_FPS, maxWidth: Math.max(160, Math.min(SINGLE_MAX_WIDTH_CAP, px || 640)) }
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
