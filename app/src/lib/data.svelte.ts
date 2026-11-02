// State and actions of the Data screen (Svelte 5 runes in a module, so the filters, sort, selection and the
// loaded dataset survive switching to another screen and back).
//
// The pure parts (filter form -> JSON filters, columns, sort rules) live in datafilters.ts so that
// app/scripts/data_smoke.mjs tests the same code.
//
// Reminder: `$state.raw` on a plain object property is not reactive; everything raw here is a class field.

import { api } from './client.ts'
import { errMessage } from './app.svelte.ts'
import {
  ALL_COLUMN_KEYS,
  COLUMN_BY_KEY,
  DEFAULT_COLUMNS,
  buildFilters,
  defaultFilters,
  exportFileName,
  exportParams,
  filterOfError,
  filtersToState,
  normaliseColumns,
  signature,
} from './datafilters.ts'
import type { ExportFormat, ExportScope, FilterState, SortSpec } from './datafilters.ts'
import { RPC, RpcError } from './protocol.ts'
import type { DataRecord, DatasetRow, RunRow } from './protocol.ts'
import { pickOpenPaths, pickSavePath } from './tauri.ts'

export const PAGE = 100

// ================================================================ table display options (per viewer)

export type RowDensity = 'compact' | 'regular' | 'tall'
/** Row height in CSS px per density. 'tall' fits two lines, so long text cells wrap instead of being cut. */
export const ROW_HEIGHTS: Record<RowDensity, number> = { compact: 24, regular: 30, tall: 46 }
export interface TablePrefs {
  density: RowDensity
  striped: boolean
  colLines: boolean
  /** keep the checkbox + Repository column in view while scrolling sideways */
  freezeName: boolean
}
export const DEFAULT_TABLE_PREFS: TablePrefs = { density: 'regular', striped: true, colLines: false, freezeName: true }
const TABLE_KEY = 'gs.data.table'

/** Validate whatever came out of storage; unknown or malformed fields fall back to the defaults. */
export function parseTablePrefs(raw: unknown): TablePrefs {
  const out: TablePrefs = { ...DEFAULT_TABLE_PREFS }
  if (!raw || typeof raw !== 'object') return out
  const o = raw as Record<string, unknown>
  if (o.density === 'compact' || o.density === 'regular' || o.density === 'tall') out.density = o.density
  if (typeof o.striped === 'boolean') out.striped = o.striped
  if (typeof o.colLines === 'boolean') out.colLines = o.colLines
  if (typeof o.freezeName === 'boolean') out.freezeName = o.freezeName
  return out
}

// ================================================================ persisted view settings

function readJson<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    return v ? (JSON.parse(v) as T) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable */
  }
}

/** Filter form (shared, deep reactive). */
export const flt: FilterState = $state(defaultFilters())

export const ui = $state({
  sidebar: readJson<boolean>('gs.data.sidebar', true),
  /** visible columns in display order; `full_name` is always first */
  columns: normaliseColumns(readJson<string[]>('gs.data.cols', DEFAULT_COLUMNS)),
  widths: readJson<Record<string, number>>('gs.data.widths', {}),
  sort: [['stars', 'desc']] as SortSpec,
  /** false = the built-in default (stars, or relevance while a text search is active) */
  sortTouched: false,
  table: parseTablePrefs(readJson<unknown>(TABLE_KEY, null)),
})

export function persistUi(): void {
  writeJson('gs.data.sidebar', ui.sidebar)
  writeJson('gs.data.cols', $state.snapshot(ui.columns))
  writeJson('gs.data.widths', $state.snapshot(ui.widths))
  writeJson(TABLE_KEY, $state.snapshot(ui.table))
}

export function setTablePrefs(patch: Partial<TablePrefs>): void {
  Object.assign(ui.table, patch)
  persistUi()
}

/** Current row height of the results table. */
export function rowHeight(): number {
  return ROW_HEIGHTS[ui.table.density]
}

export function colWidth(key: string): number {
  return ui.widths[key] ?? COLUMN_BY_KEY[key]?.width ?? 120
}

/** The sort sent to the sidecar. `undefined` lets it pick (relevance for text, else stars). */
export function effectiveSort(): SortSpec | undefined {
  if (!ui.sortTouched) return flt.text.trim() ? [['relevance', 'asc']] : [['stars', 'desc']]
  return $state.snapshot(ui.sort) as SortSpec
}

/** What the grid is a result of. Reads the reactive filter state, so an $effect calling it re-runs on edits. */
export function querySig(): string {
  return JSON.stringify({ f: buildFilters(flt), s: effectiveSort() })
}

// ================================================================ the paged result set

type Row = DataRecord

class Grid {
  /** bumped whenever cached rows change; readers (`rowAt`) subscribe to it */
  rev = $state(0)
  /** bumped on every new query: the grid scrolls back to the top */
  resets = $state(0)
  total = $state<number | null>(null)
  loading = $state(false)
  error = $state('')
  /** top-level filter key -> message from the sidecar */
  fieldErrors = $state<Record<string, string>>({})

  private pages = new Map<number, Row[]>()
  private cacheGen = 0
  private gen = 0
  private pending = new Set<string>()
  private failed = new Set<number>()
  private q: { filters: Record<string, unknown>; sort: SortSpec | undefined } = { filters: {}, sort: undefined }

  /** Signature of the query behind the rows on screen (plain field: the filter effect compares against it). */
  sig = ''

  /** Start a new query from the current filters + sort. Responses of older queries are ignored. */
  reset(): void {
    const gen = ++this.gen
    this.pending.clear()
    this.failed.clear()
    this.q = { filters: buildFilters(flt), sort: effectiveSort() }
    this.sig = querySig()
    this.error = ''
    this.fieldErrors = {}
    live.newCount = 0
    this.resets++
    void this.fetch(0, gen)
  }

  get filtersInUse(): Record<string, unknown> {
    return this.q.filters
  }

  rowAt(i: number): Row | undefined {
    void this.rev
    return this.pages.get(Math.floor(i / PAGE))?.[i % PAGE]
  }

  /** Make sure the pages covering rows first..last (inclusive) are loaded or loading. */
  ensure(first: number, last: number): void {
    if (this.total === 0 || first < 0) return
    const lo = Math.floor(first / PAGE)
    const hi = Math.floor(Math.max(first, last) / PAGE)
    const fresh = this.cacheGen === this.gen
    for (let p = lo; p <= hi; p++) {
      if (fresh && this.pages.has(p)) continue
      if (this.failed.has(p) || this.pending.has(`${this.gen}:${p}`)) continue
      if (fresh && this.total !== null && p * PAGE >= this.total) continue
      void this.fetch(p, this.gen)
    }
  }

  private async fetch(p: number, gen: number): Promise<void> {
    const key = `${gen}:${p}`
    if (this.pending.has(key)) return
    this.pending.add(key)
    this.loading = true
    try {
      const r = await api.call('data.search', {
        filters: this.q.filters,
        sort: this.q.sort,
        limit: PAGE,
        offset: p * PAGE,
        columns: ALL_COLUMN_KEYS,
      })
      if (gen !== this.gen) return // the user changed the query meanwhile: drop this answer
      if (this.cacheGen !== gen) {
        this.pages.clear()
        this.cacheGen = gen
      }
      this.pages.set(p, r.rows)
      this.total = r.total
      this.error = ''
      this.fieldErrors = {}
      this.rev++
    } catch (e) {
      if (gen !== this.gen) return
      this.failed.add(p)
      const msg = errMessage(e)
      const key2 = e instanceof RpcError && e.code === RPC.INVALID_PARAMS ? filterOfError(msg) : null
      if (key2) this.fieldErrors = { ...this.fieldErrors, [key2]: msg }
      else this.error = msg
      if (p === 0) {
        // no rows for a rejected query; keep nothing stale on screen
        this.pages.clear()
        this.cacheGen = gen
        this.total = null
        this.rev++
      }
    } finally {
      this.pending.delete(key)
      if (![...this.pending].some((k) => k.startsWith(`${this.gen}:`))) this.loading = false
    }
  }
}

export const grid = new Grid()

let debounce: ReturnType<typeof setTimeout> | null = null
/** Re-run the query after the user stops changing things (250 ms), or now. */
export function scheduleSearch(immediate = false): void {
  if (debounce) clearTimeout(debounce)
  debounce = null
  if (immediate) grid.reset()
  else
    debounce = setTimeout(() => {
      debounce = null
      grid.reset()
    }, 250)
}

// ================================================================ facets, runs, datasets

export interface Facet {
  value: string
  count: number
}
export interface Facets {
  languages: Facet[]
  topics: Facet[]
  tech: Facet[]
  licenses: Facet[]
}

class Lists {
  facets = $state.raw<Facets | null>(null)
  runs = $state.raw<RunRow[]>([])
  datasets = $state.raw<DatasetRow[]>([])
  /** the database has no rows at all (decided by an unfiltered count) */
  dbRows = $state<number | null>(null)
}
export const lists = new Lists()

export async function loadFacets(): Promise<void> {
  try {
    lists.facets = await api.call('data.facets', { limit: 500 })
  } catch (e) {
    console.warn('data.facets failed', e)
  }
}

export async function loadRuns(): Promise<void> {
  try {
    lists.runs = (await api.call('runs.list')).runs
  } catch (e) {
    console.warn('runs.list failed', e)
  }
}

export async function loadDatasets(): Promise<void> {
  try {
    lists.datasets = (await api.call('datasets.list')).datasets
  } catch (e) {
    console.warn('datasets.list failed', e)
  }
}

/** Count of every row in the store (not the filtered count): tells "no data yet" from "no matches". */
export async function loadDbCount(): Promise<void> {
  try {
    lists.dbRows = (await api.call('data.search', { filters: {}, limit: 0, offset: 0 })).total
  } catch (e) {
    console.warn('db count failed', e)
  }
}

/** Everything that is not the grid; called on connect and after data changed. */
export async function loadMeta(): Promise<void> {
  await Promise.all([loadFacets(), loadRuns(), loadDatasets(), loadDbCount()])
}

// ================================================================ selection + drawer

class Selection {
  names = $state.raw<Set<string>>(new Set())
  /** keyboard cursor / last clicked row index (-1 none) */
  active = $state(-1)
  anchor = -1
  busy = $state(false)

  has(name: string): boolean {
    return this.names.has(name)
  }
  set(names: Iterable<string>): void {
    this.names = new Set(names)
  }
  toggle(name: string): void {
    const n = new Set(this.names)
    if (n.has(name)) n.delete(name)
    else n.add(name)
    this.names = n
  }
  clear(): void {
    this.names = new Set()
  }
}
export const sel = new Selection()

/** Shift-click: select (or deselect) the loaded rows between the anchor and `to`. */
export function selectRange(to: number): void {
  const from = sel.anchor < 0 ? to : sel.anchor
  const [a, b] = from <= to ? [from, to] : [to, from]
  const n = new Set(sel.names)
  for (let i = a; i <= b; i++) {
    const name = grid.rowAt(i)?.full_name
    if (typeof name === 'string') n.add(name)
  }
  sel.names = n
}

/** Header checkbox: every row matching the current filters (names only; paged in chunks of 1000). */
export async function selectAllMatching(): Promise<void> {
  const total = grid.total ?? 0
  if (!total) return
  sel.busy = true
  try {
    const names: string[] = []
    const filters = grid.filtersInUse
    const sort = effectiveSort()
    const cap = Math.min(total, 10_000)
    for (let off = 0; off < cap; off += 1000) {
      const r = await api.call('data.search', { filters, sort, limit: 1000, offset: off, columns: ['full_name'] })
      for (const row of r.rows) names.push(String(row.full_name))
    }
    sel.set(names)
    if (total > cap) notify('warn', `Selected the first ${cap.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} rows (the limit for one action).`)
  } catch (e) {
    notify('err', errMessage(e))
  } finally {
    sel.busy = false
  }
}

class Drawer {
  name = $state('')
  record = $state.raw<DataRecord | null>(null)
  loading = $state(false)
  error = $state('')
  private seq = 0

  async open(name: string): Promise<void> {
    if (!name) return
    const my = ++this.seq
    this.name = name
    this.loading = true
    this.error = ''
    try {
      const r = await api.call('data.get', { full_name: name })
      if (my !== this.seq) return
      this.record = r
    } catch (e) {
      if (my !== this.seq) return
      this.record = null
      this.error = e instanceof RpcError && e.code === RPC.NOT_FOUND ? 'This repository is no longer in the database.' : errMessage(e)
    } finally {
      if (my === this.seq) this.loading = false
    }
  }

  close(): void {
    this.seq++
    this.name = ''
    this.record = null
    this.error = ''
    this.loading = false
    sel.active = -1
  }
}
export const drawer = new Drawer()

export function openRow(i: number): void {
  const row = grid.rowAt(i)
  if (!row) return
  sel.active = i
  sel.anchor = i
  void drawer.open(String(row.full_name))
}

/** Arrow keys: move the cursor and open that row. */
export function moveActive(delta: number): number {
  const total = grid.total ?? 0
  if (!total) return -1
  const from = sel.active < 0 ? (delta > 0 ? -1 : total) : sel.active
  const to = Math.max(0, Math.min(total - 1, from + delta))
  sel.active = to
  sel.anchor = to
  const row = grid.rowAt(to)
  if (row) void drawer.open(String(row.full_name))
  return to
}

// ================================================================ notices + confirm

export interface Notice {
  kind: 'ok' | 'err' | 'warn' | 'info'
  text: string
  lines?: string[]
}
class Notices {
  current = $state.raw<Notice | null>(null)
}
export const notices = new Notices()
let noticeTimer: ReturnType<typeof setTimeout> | null = null

export function notify(kind: Notice['kind'], text: string, lines?: string[]): void {
  notices.current = { kind, text, lines }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = kind === 'err' ? null : setTimeout(() => (notices.current = null), 12_000)
}
export function dismissNotice(): void {
  notices.current = null
}

export const confirmBox = $state({
  open: false,
  title: '',
  body: '',
  action: 'Confirm',
  danger: false,
  busy: false,
  error: '',
  run: null as null | (() => Promise<void>),
})

export function askConfirm(o: { title: string; body: string; action: string; danger?: boolean; run: () => Promise<void> }): void {
  Object.assign(confirmBox, { open: true, title: o.title, body: o.body, action: o.action, danger: !!o.danger, busy: false, error: '', run: o.run })
}

export async function acceptConfirm(): Promise<void> {
  if (!confirmBox.run || confirmBox.busy) return
  confirmBox.busy = true
  confirmBox.error = ''
  try {
    await confirmBox.run()
    confirmBox.open = false
  } catch (e) {
    confirmBox.error = errMessage(e)
  } finally {
    confirmBox.busy = false
  }
}

// ================================================================ delete

export function askDeleteSelected(): void {
  const names = [...sel.names]
  if (!names.length) return
  askConfirm({
    title: `Delete ${names.length.toLocaleString('en-US')} ${names.length === 1 ? 'repository' : 'repositories'}?`,
    body: 'They are removed from the local database. This cannot be undone (run the scrape again to get them back).',
    action: 'Delete',
    danger: true,
    run: async () => {
      let deleted = 0
      for (let i = 0; i < names.length; i += 10_000) {
        deleted += (await api.call('data.delete', { full_names: names.slice(i, i + 10_000) })).deleted
      }
      if (drawer.name && names.includes(drawer.name)) drawer.close()
      sel.clear()
      sel.active = -1
      notify('ok', `Deleted ${deleted.toLocaleString('en-US')} ${deleted === 1 ? 'repository' : 'repositories'}.`)
      scheduleSearch(true)
      void loadMeta()
    },
  })
}

// ================================================================ datasets

export const ds = $state({
  loadedId: null as number | null,
  loadedSig: '',
  error: '',
})

function currentSort(): SortSpec | null {
  return ui.sortTouched ? ($state.snapshot(ui.sort) as SortSpec) : null
}

export function currentSignature(): string {
  return signature(buildFilters(flt), $state.snapshot(ui.columns) as string[], currentSort())
}

export function datasetModified(): boolean {
  return ds.loadedId !== null && currentSignature() !== ds.loadedSig
}

export function loadDataset(d: DatasetRow): void {
  Object.assign(flt, filtersToState(d.filters))
  ui.columns = normaliseColumns(d.columns ?? DEFAULT_COLUMNS)
  if (d.sort && d.sort.length) {
    ui.sort = d.sort.map((s) => [s[0], s[1]] as [string, 'asc' | 'desc'])
    ui.sortTouched = true
  } else {
    ui.sort = [['stars', 'desc']]
    ui.sortTouched = false
  }
  persistUi()
  ds.loadedId = d.id
  ds.loadedSig = currentSignature()
  ds.error = ''
  scheduleSearch(true)
}

export function unloadDataset(): void {
  ds.loadedId = null
  ds.loadedSig = ''
}

/** Save (or overwrite, by name) the current filters + visible columns + sort. Throws on failure. */
export async function saveDataset(name: string): Promise<void> {
  const r = await api.call('datasets.save', {
    name: name.trim(),
    filters: buildFilters(flt),
    columns: $state.snapshot(ui.columns) as string[],
    sort: currentSort() ?? undefined,
  })
  await loadDatasets()
  ds.loadedId = r.id
  ds.loadedSig = currentSignature()
}

export function datasetNameTaken(name: string): DatasetRow | undefined {
  const n = name.trim().toLowerCase()
  return lists.datasets.find((d) => d.name.toLowerCase() === n)
}

export function askDeleteDataset(d: DatasetRow): void {
  askConfirm({
    title: `Delete the dataset “${d.name}”?`,
    body: 'Only the saved filters are removed, not any scraped data.',
    action: 'Delete dataset',
    danger: true,
    run: async () => {
      await api.call('datasets.delete', { id: d.id })
      if (ds.loadedId === d.id) unloadDataset()
      await loadDatasets()
    },
  })
}

// ================================================================ export + import

export const exporting = $state({ busy: false })

/** Ask for a path with the native dialog, then let the sidecar stream the file. */
export async function doExport(scope: ExportScope, format: ExportFormat): Promise<void> {
  if (exporting.busy) return
  const selected = [...sel.names]
  if (scope === 'selected' && !selected.length) return
  exporting.busy = true
  try {
    const path = await pickSavePath({
      title: 'Export',
      defaultPath: exportFileName(scope, format),
      filters: [format === 'csv' ? { name: 'CSV (opens in Excel)', extensions: ['csv'] } : { name: 'JSON', extensions: ['json'] }],
    })
    if (!path) return
    const params = exportParams(scope, format, buildFilters(flt), effectiveSort(), selected, $state.snapshot(ui.columns) as string[])
    const r = await api.call('export.write', { ...params, path })
    notify('ok', `Exported ${r.rows.toLocaleString('en-US')} ${r.rows === 1 ? 'row' : 'rows'} to ${r.path}`)
  } catch (e) {
    notify('err', `Export failed: ${errMessage(e)}`)
  } finally {
    exporting.busy = false
  }
}

export const importing = $state({ busy: false })

export async function doImport(): Promise<void> {
  if (importing.busy) return
  importing.busy = true
  try {
    const paths = await pickOpenPaths({ title: 'Import old results', filters: [{ name: 'JSON', extensions: ['json'] }] })
    if (!paths.length) return
    const r = await api.call('data.import_json', { paths })
    const lines = [
      ...r.imported.map((x) => `${x.file}: ${x.repos} repos${x.skipped_records ? `, ${x.skipped_records} records skipped` : ''}`),
      ...r.skipped.map((x) => `${x.file}: skipped (${x.reason})`),
    ]
    notify(r.skipped.length && !r.imported.length ? 'err' : 'ok', `Imported ${r.total_repos.toLocaleString('en-US')} repositories from ${r.imported.length} of ${paths.length} file(s).`, lines)
    scheduleSearch(true)
    void loadMeta()
  } catch (e) {
    notify('err', `Import failed: ${errMessage(e)}`)
  } finally {
    importing.busy = false
  }
}

// ================================================================ live updates

export const live = $state({ newCount: 0 })

let wired = false
let autoTimer: ReturnType<typeof setTimeout> | null = null

/** Subscribe to the events that matter here. Safe to call repeatedly; the Data screen calls it on mount. */
export function initDataEvents(): void {
  if (wired) return
  wired = true
  api.on('repo.saved', () => {
    if ((lists.dbRows ?? 1) === 0 && !autoTimer) {
      // nothing on screen yet: show the first results without asking
      autoTimer = setTimeout(() => {
        autoTimer = null
        scheduleSearch(true)
        void loadMeta()
      }, 600)
      return
    }
    live.newCount += 1
  })
  api.on('run.finished', () => {
    void loadFacets()
    void loadRuns()
    void loadDbCount()
  })
}

let syncedConn = 0
/** First time on each (re)connection: load the side lists and run the query. */
export function syncOnConnect(): void {
  if (!api.isOpen || syncedConn === api.connections) return
  syncedConn = api.connections
  void loadMeta()
  scheduleSearch(true)
}

export function refreshNow(): void {
  scheduleSearch(true)
  void loadMeta()
}

// ================================================================ clipboard

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}
