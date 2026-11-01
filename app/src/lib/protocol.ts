// Wire types and pure helpers for the sidecar protocol (docs/sidecar-protocol.md).
//
// This module is deliberately framework-free (no Svelte, no DOM, no Tauri) so that it can be imported
// unchanged by Node scripts (app/scripts/smoke.mjs runs it with --experimental-strip-types). Keep it
// to erasable TypeScript: no enums, no namespaces, no constructor parameter properties.

// ---------------------------------------------------------------- errors

export const RPC = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
  RUN_ACTIVE: -32001,
  NO_RUN: -32002,
  NOT_PAUSED: -32003,
  NOT_FOUND: -32004,
  EXPORT_FAILED: -32005,
  BAD_WORKER: -32006,
  BROWSER_ERROR: -32007,
  STORAGE_FAILED: -32008,
  // client-side codes (never sent by the server)
  NOT_CONNECTED: -1001,
  TIMEOUT: -1002,
  CONNECTION_CLOSED: -1003,
} as const

export interface FieldError {
  field: string
  message: string
}

export class RpcError extends Error {
  code: number
  data: unknown
  constructor(code: number, message: string, data?: unknown) {
    super(message)
    this.name = 'RpcError'
    this.code = code
    this.data = data
  }
  /** `data.errors` of an invalid-params reply for configs. */
  get fieldErrors(): FieldError[] {
    const d = this.data as { errors?: FieldError[] } | null | undefined
    return Array.isArray(d?.errors) ? d.errors : []
  }
}

// ---------------------------------------------------------------- config

export type FilterValue = string | string[] | boolean
export type BrowserKind = 'chrome' | 'msedge' | 'brave'

export interface Config {
  queries: string[]
  max_results: number
  sort: string
  order: 'asc' | 'desc'
  filters: Record<string, FilterValue>
  deep_scrape: boolean
  fetch_manifests: boolean
  browsers: number
  workers_per_chrome: number
  isolate_workers: boolean
  block_resources: boolean
  browser: BrowserKind
  browser_path: string
  headless: boolean
  human_search: boolean
  min_delay: number
  max_delay: number
  typing_delay: number
  max_retries: number
  proxy: string
  user_data_dir: string
  page_timeout: number
  output_dir: string
  formats: string[]
  /** desktop app appearance (Settings > Theme) */
  ui_theme: ThemeMode
  /** desktop app: look for a new version at startup (Settings > Updates) */
  check_updates: boolean
}

export type ThemeMode = 'system' | 'light' | 'dark'

export type FilterKind = 'list' | 'match' | 'range' | 'date' | 'choice' | 'flag' | 'raw'

export interface FilterMeta {
  key: string
  label: string
  kind: FilterKind
  qualifier: string
  help: string
  group: string
  /** [stored value, qualifier] pairs for kind "choice" */
  choices: [string, string][]
  negate: boolean
}

export interface SortChoice {
  label: string
  sort: string
  order: 'asc' | 'desc'
}

export interface ConfigMeta {
  max_browsers: number
  sort_choices: SortChoice[]
  filters: FilterMeta[]
}

export interface ConfigGetResult {
  config: Config
  defaults: Config
  meta: ConfigMeta
}

export interface ValidateResult {
  ok: boolean
  errors: FieldError[]
  warnings: { field: string; message: string; query?: string }[]
  queries: { query: string; github_query: string | null; warnings: string[] }[]
  filters_described: Record<string, string>
  estimate: {
    pages: number
    seconds_low: number
    seconds_high: number
    workers: number
    chromes: number
  } | null
}

// ---------------------------------------------------------------- app / runs

/** Which browsers the sidecar can find on this machine (the first-run check). */
export interface BrowserStatus {
  selected: string
  custom_path: boolean
  /** what a run would launch for the saved settings; null = nothing usable */
  path: string | null
  available: boolean
  found: Record<BrowserKind, string | null>
}

/** Where the data folder came from and what can be done about it (app.info.storage). */
export interface StorageInfo {
  /** arg (--data-dir) | env (GHOSTSCRAPER_HOME) | pointer (chosen in Settings) | default */
  source: 'arg' | 'env' | 'pointer' | 'default'
  /** a folder chosen in Settings is in use */
  custom: boolean
  /** false when --data-dir / GHOSTSCRAPER_HOME decides: the pointer file would have no effect */
  can_change: boolean
  default_dir: string
  /** custom folder in use and the default folder still holds an earlier data.db */
  default_has_data: boolean
  /** data.db + data.db-wal, bytes */
  db_bytes: number
  /** why a saved custom folder is not being used (e.g. it no longer exists) */
  problem: string | null
  /** a move/reset was done and waits for a backend restart; the folder that will be used */
  pending_restart: string | null
}

export interface MovePlan {
  /** copy = empty or new folder; switch = the folder already holds data.db, nothing is copied */
  mode: 'copy' | 'switch'
  from: string
  to: string
  bytes_to_copy: number
  /** row counts per table of the data found at the target (mode "switch") */
  existing: Record<string, number> | null
}

export interface AppInfo {
  version: string
  pid: number
  platform: string
  python: string
  data_dir: string
  db_path: string
  config_path: string
  schema_version: number
  run_active: boolean
  test_mode: boolean
  max_browsers: number
  /** running from the packaged (PyInstaller) build */
  frozen: boolean
  browser: BrowserStatus
  /** data copied from the pre-rename app at startup: 'none' | 'migrated' | 'skipped-existing' | 'failed: …' */
  migration: string
  storage: StorageInfo
}

export type WorkerState =
  | 'starting'
  | 'searching'
  | 'repo'
  | 'idle'
  | 'paused'
  | 'recovering'
  | 'done'
  | 'stopped'
  | 'failed'
  | 'other'

export type RunKind = 'run' | 'preview'
export type RunStatusName = 'done' | 'stopped' | 'failed'

export interface WorkerStatus {
  index: number
  text: string
  state: WorkerState
  /** Not in the protocol today; used if the sidecar starts sending them. */
  url?: string | null
  pages?: number | null
}

export interface RunProgress {
  run_id: number | null
  queries_done: number
  queries_total: number
  repos_done: number
  repos_total: number
  eta_seconds: number | null
  elapsed: number
}

export interface RunStarted {
  run_id: number | null
  kind: RunKind
  workers: number
  queries: string[]
  config: Config
}

export interface RunCounts {
  repos: number
  repos_total: number
  queries_done: number
  queries_total: number
  errors: number
  write_errors: number
  elapsed: number
}

export interface PreviewTotals {
  status: RunStatusName
  totals: Record<string, number | null>
  queries: { query: string; github_query: string; total: number | null; over_cap: boolean }[]
}

export interface RunFinished {
  run_id: number | null
  kind: RunKind
  status: RunStatusName
  error: string | null
  counts: RunCounts
  totals?: Record<string, number | null>
}

export interface RunStatusIdle {
  active: false
  last: RunFinished | null
}

export interface RunStatusActive {
  active: true
  kind: RunKind
  run_id: number | null
  stopping: boolean
  elapsed: number
  workers: { index: number; text: string; state: WorkerState; paused: boolean }[]
  progress: RunProgress
}

export type RunStatus = RunStatusIdle | RunStatusActive

export interface RepoSaved {
  run_id: number
  full_name: string
  url: string
  description: string | null
  stars: number | null
  forks: number | null
  language: string | null
  topics: string[]
  error: string | null
  scraped_at: string
}

export type LogLevel = 'debug' | 'info' | 'warning' | 'error'

export interface LogEvent {
  level: LogLevel
  text: string
}

export interface PoolStats {
  run_id: number | null
  chrome_count: number
  process_count: number
  total_rss: number
  total_uss: number | null
  chromes: { pid: number; processes: number; rss: number; uss: number | null; workers: number[] }[]
  slots: { index: number; rss: number | null; uss: number | null; chrome: number | null }[]
}

export interface AuthOk {
  version: string
  pid: number
}

// ---------------------------------------------------------------- browsers / input

export interface ViewResult {
  active: number[]
  pending: number[]
}

export interface MouseInput {
  type: 'mouse'
  action: 'move' | 'down' | 'up' | 'wheel'
  x: number
  y: number
  button?: 'left' | 'right' | 'middle'
  click_count?: number
  delta_x?: number
  delta_y?: number
  modifiers?: number
}

export interface KeyInput {
  type: 'key'
  action: 'down' | 'up' | 'raw' | 'char'
  key?: string
  code?: string
  text?: string
  vk?: number
  modifiers?: number
}

export interface TextInput {
  type: 'text'
  text: string
}

export type InputEvent = MouseInput | KeyInput | TextInput

// ---------------------------------------------------------------- data (typed loosely; Data screen refines)

export type DataRecord = Record<string, unknown>

export interface DatasetRow {
  id: number
  name: string
  filters: Record<string, unknown>
  columns: string[] | null
  sort: [string, 'asc' | 'desc'][] | null
  created_at: string
  updated_at: string
}

export interface RunRow {
  id: number
  started_at: string
  finished_at: string | null
  status: 'running' | 'done' | 'stopped' | 'failed' | 'imported'
  config: Record<string, unknown>
  counts: Record<string, unknown>
}

// ---------------------------------------------------------------- method + event tables

type NoParams = Record<string, never>
type Overrides = { overrides?: Partial<Config> | Record<string, unknown> }

export interface Methods {
  'app.info': { params: NoParams; result: AppInfo }
  'app.shutdown': { params: NoParams; result: { ok: true } }
  'config.get': { params: NoParams; result: ConfigGetResult }
  'config.update': { params: { values: Partial<Config> | Record<string, unknown> }; result: { config: Config } }
  'config.validate': { params: Overrides; result: ValidateResult }
  'run.start': { params: Overrides; result: { run_id: number; workers: number } }
  'run.preview_counts': { params: Overrides; result: PreviewTotals }
  'run.stop': { params: NoParams; result: { stopping: boolean; run_id?: number } }
  'run.status': { params: NoParams; result: RunStatus }
  'worker.pause': { params: { index: number }; result: { index: number; paused: true; engaged: boolean } }
  'worker.resume': { params: { index: number }; result: { index: number; paused: false } }
  'browser.view': { params: { indices: number[]; fps?: number; max_width?: number }; result: ViewResult }
  'browser.input': { params: { index: number; event: InputEvent }; result: { ok: true } }
  'data.search': {
    params: {
      filters?: Record<string, unknown>
      sort?: [string, 'asc' | 'desc'][]
      limit?: number
      offset?: number
      columns?: string[]
    }
    result: { rows: DataRecord[]; total: number }
  }
  'data.facets': {
    params: { limit?: number }
    result: Record<'languages' | 'topics' | 'tech' | 'licenses', { value: string; count: number }[]>
  }
  'data.get': { params: { full_name: string }; result: DataRecord }
  'data.delete': { params: { full_names: string[] }; result: { deleted: number } }
  'data.import_json': {
    params: { paths: string[] }
    result: {
      imported: { file: string; run_id: number; repos: number; skipped_records: number }[]
      skipped: { file: string; reason: string }[]
      total_repos: number
    }
  }
  'datasets.list': { params: NoParams; result: { datasets: DatasetRow[] } }
  'datasets.save': {
    params: { name: string; filters?: Record<string, unknown>; columns?: string[]; sort?: [string, 'asc' | 'desc'][] }
    result: { id: number }
  }
  'datasets.delete': { params: { id: number }; result: { deleted: boolean } }
  'runs.list': { params: NoParams; result: { runs: RunRow[] } }
  'storage.move': {
    params: { path: string; dry_run?: boolean }
    result: MovePlan & {
      dry_run?: true
      restart_required?: true
      tables?: Record<string, number>
      warnings?: string[]
    }
  }
  'storage.reset': { params: NoParams; result: { restart_required: true; data_dir: string; kept: string } }
  'export.write': {
    params: {
      filters?: Record<string, unknown>
      full_names?: string[]
      run_id?: number
      sort?: [string, 'asc' | 'desc'][]
      columns?: string[]
      format: 'json' | 'csv'
      path: string
    }
    result: { rows: number; path: string; format: 'json' | 'csv' }
  }
}

export type MethodName = keyof Methods

export interface Events {
  'auth.ok': AuthOk
  'run.started': RunStarted
  'run.progress': RunProgress
  'run.finished': RunFinished
  'worker.status': WorkerStatus
  'repo.saved': RepoSaved
  log: LogEvent
  'pool.stats': PoolStats
}

export type EventName = keyof Events

// ---------------------------------------------------------------- binary frames (section 6)

export const FRAME_VERSION = 1
export const FRAME_HEADER_SIZE = 15

export interface Frame {
  /** worker / slot index */
  slot: number
  /** per-slot sequence number (skips when frames were dropped) */
  seq: number
  /** page viewport in CSS px at capture time: the coordinate space of browser.input */
  width: number
  height: number
  /** JPEG bytes (a view into the received buffer, not a copy) */
  jpeg: Uint8Array
}

/**
 * Parse one binary message: 15-byte big-endian header
 * `u8 version=1 | u16 slot | u32 seq | u32 width | u32 height` followed by the JPEG.
 * Returns null for anything that is not a well-formed v1 frame.
 */
export function parseFrame(data: ArrayBuffer | ArrayBufferView): Frame | null {
  const bytes =
    data instanceof ArrayBuffer
      ? new Uint8Array(data)
      : new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
  if (bytes.byteLength <= FRAME_HEADER_SIZE) return null
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  if (dv.getUint8(0) !== FRAME_VERSION) return null
  return {
    slot: dv.getUint16(1, false),
    seq: dv.getUint32(3, false),
    width: dv.getUint32(7, false),
    height: dv.getUint32(11, false),
    jpeg: bytes.subarray(FRAME_HEADER_SIZE),
  }
}

/** Build a frame the same way the sidecar does (used by tests). */
export function packFrame(slot: number, seq: number, width: number, height: number, jpeg: Uint8Array): Uint8Array {
  const out = new Uint8Array(FRAME_HEADER_SIZE + jpeg.byteLength)
  const dv = new DataView(out.buffer)
  dv.setUint8(0, FRAME_VERSION)
  dv.setUint16(1, slot, false)
  dv.setUint32(3, seq, false)
  dv.setUint32(7, width, false)
  dv.setUint32(11, height, false)
  out.set(jpeg, FRAME_HEADER_SIZE)
  return out
}
