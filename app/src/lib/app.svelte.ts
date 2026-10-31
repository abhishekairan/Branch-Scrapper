// Global reactive state and actions (Svelte 5 runes in a module). Components import objects from here and
// read/mutate their properties; nothing here re-assigns an exported binding.
//
// Sections: navigation, connection, run/workers/pool, logs, config draft + live validation, browser control.

import { api } from './client.ts'
import { RPC, RpcError } from './protocol.ts'
import type {
  BrowserStatus,
  Config,
  ConfigMeta,
  FieldError,
  FilterMeta,
  FilterValue,
  LogLevel,
  PoolStats,
  PreviewTotals,
  RepoSaved,
  RunFinished,
  RunKind,
  RunProgress,
  ValidateResult,
  WorkerState,
} from './protocol.ts'
import { listenShell, restartSidecar } from './tauri.ts'
import type { ConnState } from './api.ts'

// ================================================================ small helpers

function load<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    if (v && (allowed as readonly string[]).includes(v)) return v as T
  } catch {
    /* storage unavailable */
  }
  return fallback
}

function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable */
  }
}

export function errMessage(e: unknown): string {
  if (e instanceof RpcError) return e.message
  return e instanceof Error ? e.message : String(e)
}

// ================================================================ navigation

export type Route = 'run' | 'browsers' | 'data'
const ROUTES = ['run', 'browsers', 'data'] as const

/** `nav.route` decides which screen App.svelte mounts. Add a screen = add it here and in App.svelte. */
export const nav = $state({ route: load<Route>('gs.route', ROUTES, 'run') })

export function go(route: Route): void {
  nav.route = route
  save('gs.route', route)
}

/** 1 Hz clock for elapsed-time displays. */
export const clock = $state({ now: Date.now() })

// ================================================================ connection

export const conn = $state({
  state: 'connecting' as ConnState,
  /** why the last attempt failed (empty when fine) */
  error: '',
  /** the shell reported that the sidecar process is gone / failed to start */
  sidecarDown: false,
  restarting: false,
  version: '',
  testMode: false,
  dataDir: '',
  maxBrowsers: 5,
  /** installed browsers reported by app.info (null until the first app.info); drives the first-run banner */
  browsersFound: null as BrowserStatus['found'] | null,
})

/** Ask the backend again which browsers are installed (after the user installed one). */
export async function recheckBrowsers(): Promise<void> {
  try {
    conn.browsersFound = (await api.call('app.info')).browser.found
  } catch (e) {
    console.warn('browser re-check failed', e)
  }
}

export async function restartBackend(): Promise<void> {
  conn.restarting = true
  try {
    await restartSidecar()
    conn.sidecarDown = false
    conn.error = ''
    api.retryNow()
  } catch (e) {
    conn.error = errMessage(e)
  } finally {
    conn.restarting = false
  }
}

// ================================================================ run / workers / pool

export interface WorkerView {
  index: number
  text: string
  state: WorkerState
  /** the user asked for this worker to be paused (it may still be finishing its page) */
  paused: boolean
  url: string | null
  pages: number | null
  /** bytes (USS if known, else RSS) of this worker's share of its Chrome; null if unknown */
  ram: number | null
}

export const run = $state({
  active: false,
  kind: null as RunKind | null,
  runId: null as number | null,
  stopping: false,
  /** Date.now() at which the active run started (for a live elapsed counter) */
  startedAt: 0,
  workers: [] as WorkerView[],
  progress: null as RunProgress | null,
  finished: null as RunFinished | null,
  preview: null as PreviewTotals | null,
  pool: null as PoolStats | null,
  saved: 0,
  busy: null as 'start' | 'preview' | 'stop' | null,
  error: '',
})

/** Newest saved repos of the current run (capped). */
class RecentStore {
  // $state.raw on a class field: reactive when the field is re-assigned (not deeply proxied)
  items = $state.raw<RepoSaved[]>([])
}
export const recent = new RecentStore()

function newWorker(index: number): WorkerView {
  return { index, text: 'starting…', state: 'starting', paused: false, url: null, pages: null, ram: null }
}

function repoUrl(text: string, state: WorkerState): string | null {
  if (state !== 'repo') return null
  const m = /^repo\s+(\S+\/\S+)/i.exec(text)
  return m ? `https://github.com/${m[1]}` : null
}

function ensureWorker(index: number): WorkerView {
  while (run.workers.length <= index) run.workers.push(newWorker(run.workers.length))
  return run.workers[index]
}

/** Chrome (all processes) RAM in bytes for the status bar; null when unknown. */
export function totalRam(): number | null {
  const p = run.pool
  if (!p) return null
  return p.total_uss ?? p.total_rss
}

export async function startRun(): Promise<void> {
  const overrides = buildOverrides()
  if (!overrides || run.busy) return
  run.busy = 'start'
  run.error = ''
  cfg.serverErrors = []
  try {
    const r = await api.call('run.start', { overrides })
    run.runId ??= r.run_id
  } catch (e) {
    if (e instanceof RpcError && e.code === RPC.INVALID_PARAMS && e.fieldErrors.length) {
      cfg.serverErrors = e.fieldErrors
      run.error = 'Fix the highlighted settings first.'
    } else run.error = errMessage(e)
  } finally {
    run.busy = null
  }
}

export async function previewCounts(): Promise<void> {
  const overrides = buildOverrides()
  if (!overrides || run.busy) return
  run.busy = 'preview'
  run.error = ''
  run.preview = null
  cfg.serverErrors = []
  try {
    run.preview = await api.call('run.preview_counts', { overrides })
  } catch (e) {
    if (e instanceof RpcError && e.code === RPC.INVALID_PARAMS && e.fieldErrors.length) {
      cfg.serverErrors = e.fieldErrors
      run.error = 'Fix the highlighted settings first.'
    } else run.error = errMessage(e)
  } finally {
    run.busy = null
  }
}

export async function stopRun(): Promise<void> {
  if (!run.active) return
  run.stopping = true
  try {
    await api.call('run.stop')
  } catch (e) {
    run.stopping = false
    run.error = errMessage(e)
  }
}

/** Rebuild run state from `run.status` (after connecting or reconnecting). */
async function syncRunStatus(): Promise<void> {
  try {
    const s = await api.call('run.status')
    if (!s.active) {
      run.active = false
      run.stopping = false
      // a restarted sidecar has no "last run": do not keep showing the previous process's result
      run.finished = s.last
      if (!s.last) run.workers = []
      for (const w of run.workers) w.paused = false
      return
    }
    run.active = true
    run.kind = s.kind
    run.runId = s.run_id
    run.stopping = s.stopping
    run.startedAt = Date.now() - s.elapsed * 1000
    run.progress = s.progress
    run.finished = null
    run.workers = s.workers.map((w) => ({
      ...newWorker(w.index),
      text: w.text,
      state: w.state,
      paused: w.paused,
      url: repoUrl(w.text, w.state),
    }))
  } catch (e) {
    console.warn('run.status failed', e)
  }
}

// ================================================================ logs

export interface LogLine {
  id: number
  t: number
  level: LogLevel
  text: string
}

export const LOG_CAP = 1500
/** Capped, replaced (not mutated) so the list renders cheaply. */
class LogStore {
  lines = $state.raw<LogLine[]>([])
}
export const logs = new LogStore()
let logSeq = 0
let logPending: LogLine[] = []
let logTimer: ReturnType<typeof setTimeout> | null = null

export function pushLog(level: LogLevel, text: string): void {
  logPending.push({ id: ++logSeq, t: Date.now(), level, text })
  if (logTimer === null) {
    logTimer = setTimeout(() => {
      logTimer = null
      const merged = logs.lines.concat(logPending)
      logPending = []
      logs.lines = merged.length > LOG_CAP ? merged.slice(merged.length - LOG_CAP) : merged
    }, 80)
  }
}

export function clearLogs(): void {
  logPending = []
  logs.lines = []
}

/** Per-worker activity feed (status changes), newest last. Used by the Single view. */
export interface ActivityItem {
  t: number
  level: LogLevel
  text: string
}
class ActivityStore {
  byWorker = $state.raw<ActivityItem[][]>([])
}
export const activity = new ActivityStore()

function pushActivity(index: number, level: LogLevel, text: string): void {
  const all = activity.byWorker.slice()
  while (all.length <= index) all.push([])
  const list = all[index].concat({ t: Date.now(), level, text })
  all[index] = list.length > 200 ? list.slice(list.length - 200) : list
  activity.byWorker = all
}

// ================================================================ config draft + live validation

export const cfg = $state({
  loaded: false,
  meta: null as ConfigMeta | null,
  defaults: null as Config | null,
  /** every config field; `queries` is edited as text (below) and filters hold raw form values */
  draft: null as Config | null,
  queriesText: '',
  validation: null as ValidateResult | null,
  validating: false,
  validationError: '',
  /** errors from the last rejected run.start / preview (cleared on the next edit) */
  serverErrors: [] as FieldError[],
  saveError: '',
})

/** Filter values as shown in inputs: lists as "a, b" text, match as string[], flags as boolean. */
function toFormFilters(meta: ConfigMeta, filters: Record<string, FilterValue>): Record<string, FilterValue> {
  const out: Record<string, FilterValue> = {}
  const byKey = new Map(meta.filters.map((f) => [f.key, f]))
  for (const [k, v] of Object.entries(filters ?? {})) {
    const spec = byKey.get(k)
    if (!spec) continue
    out[k] = Array.isArray(v) && spec.kind !== 'match' ? v.join(', ') : v
  }
  return out
}

function isEmpty(v: unknown): boolean {
  return v === undefined || v === null || v === '' || v === false || (Array.isArray(v) && v.length === 0)
}

/** The full config the form represents, in the shape of `overrides`. null until config.get answered. */
export function buildOverrides(): Config | null {
  const d = cfg.draft
  if (!d) return null
  const snap = $state.snapshot(d) as Config
  const filters: Record<string, FilterValue> = {}
  for (const [k, v] of Object.entries(snap.filters)) {
    if (isEmpty(v)) continue
    filters[k] = typeof v === 'string' ? v.trim() : v
  }
  const queries = cfg.queriesText
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)
  return { ...snap, queries, filters }
}

export function filterMeta(key: string): FilterMeta | undefined {
  return cfg.meta?.filters.find((f) => f.key === key)
}

/** All current errors (live validation + last server rejection) for `field` ('filters.stars', 'queries', ...). */
export function errorsFor(field: string): string[] {
  const all = [...(cfg.validation?.errors ?? []), ...cfg.serverErrors]
  return [...new Set(all.filter((e) => e.field === field).map((e) => e.message))]
}

export function warningsFor(field: string): string[] {
  return (cfg.validation?.warnings ?? []).filter((w) => w.field === field).map((w) => w.message)
}

export function resetToDefaults(): void {
  if (!cfg.defaults || !cfg.meta) return
  const d = structuredClone($state.snapshot(cfg.defaults)) as Config
  cfg.queriesText = d.queries.join('\n')
  d.filters = toFormFilters(cfg.meta, d.filters)
  cfg.draft = d
}

async function loadConfig(): Promise<void> {
  const r = await api.call('config.get')
  conn.maxBrowsers = r.meta.max_browsers
  cfg.meta = r.meta
  cfg.defaults = r.defaults
  if (!cfg.draft) {
    const d = structuredClone(r.config)
    cfg.queriesText = d.queries.join('\n')
    d.filters = toFormFilters(r.meta, d.filters)
    cfg.draft = d
    lastSavedSig = JSON.stringify(buildOverrides())
  }
  cfg.loaded = true
}

let validateSeq = 0
let lastSavedSig = ''

async function validateNow(sig: string): Promise<void> {
  const overrides = buildOverrides()
  if (!overrides || !api.isOpen) return
  const my = ++validateSeq
  cfg.validating = true
  try {
    const r = await api.call('config.validate', { overrides })
    if (my !== validateSeq) return // a newer edit superseded this one
    cfg.validation = r
    cfg.validationError = ''
    void autosave(sig, overrides, r)
  } catch (e) {
    if (my !== validateSeq) return
    if (e instanceof RpcError && e.code === RPC.INVALID_PARAMS && e.fieldErrors.length) {
      cfg.validation = {
        ok: false,
        errors: e.fieldErrors,
        warnings: [],
        queries: [],
        filters_described: {},
        estimate: null,
      }
      cfg.validationError = ''
    } else cfg.validationError = errMessage(e)
  } finally {
    if (my === validateSeq) cfg.validating = false
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null

/** Persist the draft to the sidecar's config.json shortly after the user stops editing (valid configs only). */
async function autosave(sig: string, overrides: Config, v: ValidateResult): Promise<void> {
  if (saveTimer) clearTimeout(saveTimer)
  // an empty query list is a valid draft for config.update but not "runnable"; any other error blocks saving
  const blocking = v.errors.filter((e) => e.field !== 'queries')
  if (blocking.length || sig === lastSavedSig) return
  saveTimer = setTimeout(async () => {
    try {
      await api.call('config.update', { values: overrides })
      lastSavedSig = sig
      cfg.saveError = ''
    } catch (e) {
      cfg.saveError = errMessage(e)
    }
  }, 1000)
}

/** Debounced live validation of the whole form. Called once from init(). */
function watchDraft(): void {
  let timer: ReturnType<typeof setTimeout> | null = null
  let prev = ''
  $effect.root(() => {
    $effect(() => {
      const o = buildOverrides()
      void conn.state // re-run once connected
      if (!o) return
      const sig = JSON.stringify(o)
      if (sig === prev && cfg.validation) return
      const edited = sig !== prev
      prev = sig
      if (edited) cfg.serverErrors = []
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => void validateNow(sig), 350)
    })
  })
}

// ================================================================ browser control (take control / resume)

export type ControlState = 'off' | 'pausing' | 'active'

/** Derived from the user's pause flag and the worker's reported state (events may arrive in any order). */
export function controlState(index: number): ControlState {
  const w = run.workers[index]
  if (!w || !w.paused || !run.active) return 'off'
  return w.state === 'paused' ? 'active' : 'pausing'
}

export const controlError = $state({ text: '' })

export async function takeControl(index: number): Promise<void> {
  const w = run.workers[index]
  if (!w || !run.active) return
  controlError.text = ''
  w.paused = true // optimistic: the worker.status "paused" event may even arrive before the reply
  try {
    await api.call('worker.pause', { index })
  } catch (e) {
    w.paused = false
    controlError.text = errMessage(e)
  }
}

export async function resumeScraping(index: number): Promise<void> {
  const w = run.workers[index]
  if (!w) return
  controlError.text = ''
  const was = w.paused
  w.paused = false
  try {
    await api.call('worker.resume', { index })
  } catch (e) {
    w.paused = was
    controlError.text = errMessage(e)
  }
}

// ================================================================ wiring

let started = false

/** Connect to the sidecar and wire every event into the stores above. Call once at startup. */
export function init(): void {
  if (started) return
  started = true

  setInterval(() => {
    if (run.active) clock.now = Date.now()
  }, 1000)

  api.onState((s) => {
    conn.state = s
    conn.error = api.lastError
    if (s === 'open') {
      conn.sidecarDown = false
      void onOpen()
    }
  })

  api.on('auth.ok', (a) => {
    conn.version = a.version
  })

  api.on('run.started', (e) => {
    run.active = true
    run.kind = e.kind
    run.runId = e.run_id
    run.stopping = false
    run.startedAt = Date.now()
    clock.now = Date.now()
    run.progress = null
    run.finished = null
    run.pool = null
    run.saved = 0
    recent.items = []
    activity.byWorker = []
    if (e.kind === 'run') run.preview = null
    run.workers = Array.from({ length: e.workers }, (_, i) => newWorker(i))
    pushLog('info', e.kind === 'preview' ? 'Preview started' : `Run ${e.run_id ?? ''} started with ${e.workers} worker(s)`)
  })

  api.on('run.progress', (p) => {
    run.progress = p
  })

  api.on('run.finished', (e) => {
    run.active = false
    run.stopping = false
    run.finished = e
    run.pool = null
    for (const w of run.workers) {
      w.paused = false
      w.ram = null
    }
    const c = e.counts
    pushLog(
      e.status === 'failed' ? 'error' : e.status === 'stopped' ? 'warning' : 'info',
      `${e.kind === 'preview' ? 'Preview' : 'Run'} ${e.status}${e.error ? `: ${e.error}` : ''} - ${c.repos} repos, ${c.errors} errors`,
    )
  })

  api.on('worker.status', (e) => {
    const w = ensureWorker(e.index)
    w.text = e.text
    w.state = e.state
    w.url = e.url ?? repoUrl(e.text, e.state)
    if (e.pages !== undefined) w.pages = e.pages
    pushActivity(e.index, e.state === 'failed' ? 'error' : e.state === 'recovering' ? 'warning' : 'info', e.text)
  })

  api.on('repo.saved', (e) => {
    run.saved += 1
    const items = recent.items.concat(e)
    recent.items = items.length > 12 ? items.slice(items.length - 12) : items
  })

  api.on('log', (e) => {
    pushLog(e.level, e.text)
    // lines tagged with a worker label, e.g. "[B2] ..." also show in that worker's activity feed
    const m = /^\[B(\d+)\]\s*(.*)$/s.exec(e.text)
    if (m) pushActivity(Number(m[1]) - 1, e.level, m[2])
  })

  api.on('pool.stats', (s) => {
    run.pool = s
    for (const slot of s.slots) {
      const w = run.workers[slot.index]
      if (w) w.ram = slot.uss ?? slot.rss ?? null
    }
  })

  void listenShell({
    onExited: (e) => {
      conn.sidecarDown = true
      conn.error = e.message
    },
    onReady: () => {
      conn.sidecarDown = false
      api.retryNow()
    },
  })

  watchDraft()
  api.start()
}

async function onOpen(): Promise<void> {
  try {
    const info = await api.call('app.info')
    conn.testMode = info.test_mode
    conn.dataDir = info.data_dir
    conn.maxBrowsers = info.max_browsers
    conn.version = info.version
    conn.browsersFound = info.browser.found
    await loadConfig()
    await syncRunStatus()
  } catch (e) {
    console.warn('initial sync failed', e)
  }
}
