// Pure (framework-free) model of the Data screen: the filter form state, the conversion to the JSON filter
// language of ghscraper/store/query.py, saved-dataset round trips, column definitions and sort rules.
//
// No Svelte, DOM or Tauri in here so that app/scripts/data_smoke.mjs can run the exact code the UI uses.
// Keep erasable TypeScript only (see protocol.ts): no enums, no namespaces, no parameter properties.

// ---------------------------------------------------------------- keys (mirror store/query.py)

export const RANGE_KEYS = [
  'stars',
  'forks',
  'watchers',
  'contributors',
  'open_issues',
  'open_pull_requests',
  'commits',
  'releases',
] as const
export type RangeKey = (typeof RANGE_KEYS)[number]

export const RANGE_LABELS: Record<RangeKey, string> = {
  stars: 'Stars',
  forks: 'Forks',
  watchers: 'Watchers',
  contributors: 'Contributors',
  open_issues: 'Open issues',
  open_pull_requests: 'Open PRs',
  commits: 'Commits',
  releases: 'Releases',
}

export const BOOL_KEYS = ['archived', 'is_fork', 'is_template', 'has_website'] as const
export type BoolKey = (typeof BOOL_KEYS)[number]

export const BOOL_LABELS: Record<BoolKey, string> = {
  archived: 'Archived',
  is_fork: 'Fork',
  is_template: 'Template',
  has_website: 'Has website',
}

export type Tri = 'any' | 'yes' | 'no'
export type SetMode = 'any' | 'all' | 'none'

// ---------------------------------------------------------------- filter form state

export interface RangeVal {
  min: string
  max: string
}
export interface SetVal {
  any: string[]
  all: string[]
  none: string[]
}
export interface DateVal {
  from: string
  to: string
}

export interface FilterState {
  text: string
  ranges: Record<RangeKey, RangeVal>
  language: { any: string[]; minPct: string }
  topics: SetVal
  tech: SetVal
  license: string[]
  created: DateVal
  pushed: DateVal
  tri: Record<BoolKey, Tri>
  query: string[]
  runId: number | null
}

export function defaultFilters(): FilterState {
  const ranges = {} as Record<RangeKey, RangeVal>
  for (const k of RANGE_KEYS) ranges[k] = { min: '', max: '' }
  const tri = {} as Record<BoolKey, Tri>
  for (const k of BOOL_KEYS) tri[k] = 'any'
  return {
    text: '',
    ranges,
    language: { any: [], minPct: '' },
    topics: { any: [], all: [], none: [] },
    tech: { any: [], all: [], none: [] },
    license: [],
    created: { from: '', to: '' },
    pushed: { from: '', to: '' },
    tri,
    query: [],
    runId: null,
  }
}

/**
 * A typed number, or the raw text when it is not one. Raw text is sent as-is on purpose: the sidecar rejects it
 * with "filter 'stars': expected a number" and the sidebar shows that message next to the field.
 */
function num(s: string): number | string | undefined {
  const t = s.trim()
  if (t === '') return undefined
  const n = Number(t)
  return Number.isFinite(n) ? n : t
}

function setVal(v: SetVal): Record<string, string[]> | null {
  const out: Record<string, string[]> = {}
  if (v.any.length) out.any = [...v.any]
  if (v.all.length) out.all = [...v.all]
  if (v.none.length) out.none = [...v.none]
  return Object.keys(out).length ? out : null
}

function dateVal(v: DateVal): Record<string, string> | null {
  const out: Record<string, string> = {}
  if (v.from.trim()) out.from = v.from.trim()
  if (v.to.trim()) out.to = v.to.trim()
  return Object.keys(out).length ? out : null
}

/** The filter object for `data.search` / `export.write` / `datasets.save`. Keys with no effect are omitted. */
export function buildFilters(s: FilterState): Record<string, unknown> {
  const f: Record<string, unknown> = {}
  const text = s.text.trim()
  if (text) f.text = text
  for (const k of RANGE_KEYS) {
    const r: Record<string, number | string> = {}
    const a = num(s.ranges[k].min)
    const b = num(s.ranges[k].max)
    if (a !== undefined) r.min = a
    if (b !== undefined) r.max = b
    if (Object.keys(r).length) f[k] = r
  }
  const lang: Record<string, unknown> = {}
  if (s.language.any.length) lang.any = [...s.language.any]
  const pct = num(s.language.minPct)
  if (pct !== undefined) lang.min_pct = pct
  if (Object.keys(lang).length) f.language = lang
  const topics = setVal(s.topics)
  if (topics) f.topics = topics
  const tech = setVal(s.tech)
  if (tech) f.tech = tech
  if (s.license.length) f.license = [...s.license]
  const created = dateVal(s.created)
  if (created) f.created = created
  const pushed = dateVal(s.pushed)
  if (pushed) f.pushed = pushed
  for (const k of BOOL_KEYS) {
    if (s.tri[k] === 'yes') f[k] = true
    else if (s.tri[k] === 'no') f[k] = false
  }
  if (s.query.length) f.query = [...s.query]
  if (s.runId !== null) f.run_id = s.runId
  return f
}

function str(v: unknown): string {
  return v === null || v === undefined ? '' : String(v)
}
function strs(v: unknown): string[] {
  if (typeof v === 'string') return [v]
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}
function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}

/** Inverse of buildFilters (for loading a saved dataset). Unknown keys are ignored. */
export function filtersToState(f: Record<string, unknown> | null | undefined): FilterState {
  const s = defaultFilters()
  const src = f ?? {}
  s.text = str(src.text)
  for (const k of RANGE_KEYS) {
    const r = obj(src[k])
    s.ranges[k] = { min: str(r.min), max: str(r.max) }
  }
  const lang = obj(src.language)
  s.language = { any: strs(lang.any), minPct: str(lang.min_pct) }
  for (const k of ['topics', 'tech'] as const) {
    const v = obj(src[k])
    s[k] = { any: strs(v.any), all: strs(v.all), none: strs(v.none) }
  }
  s.license = strs(src.license)
  const dates = (k: string): DateVal => {
    const d = obj(src[k])
    return { from: str(d.from), to: str(d.to) }
  }
  s.created = dates('created')
  s.pushed = src.pushed !== undefined ? dates('pushed') : dates('last_commit_at')
  for (const k of BOOL_KEYS) s.tri[k] = src[k] === true ? 'yes' : src[k] === false ? 'no' : 'any'
  s.query = strs(src.query)
  const run = Array.isArray(src.run_id) ? src.run_id[0] : src.run_id
  s.runId = typeof run === 'number' ? run : null
  return s
}

/** Stable text for "did the user change anything" comparisons. */
export function signature(filters: Record<string, unknown>, columns: string[], sort: SortSpec | null): string {
  return JSON.stringify({ f: sortKeys(filters), c: columns, s: sort })
}

function sortKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortKeys)
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    return Object.fromEntries(Object.keys(o).sort().map((k) => [k, sortKeys(o[k])]))
  }
  return v
}

// ---------------------------------------------------------------- errors from the sidecar

/**
 * Which filter an error message is about. query.py words them `filter 'stars': ...`, `unknown key 'x' in filter
 * 'stars'` or `filter 'language.min_pct': ...`. Returns the top-level filter key or null.
 */
export function filterOfError(message: string): string | null {
  const m = /filter '([a-z_]+)(?:\.[a-z_]+)?'/.exec(message)
  if (!m) return null
  return m[1] === 'last_commit_at' ? 'pushed' : m[1]
}

// ---------------------------------------------------------------- chips

export interface Chip {
  id: string
  label: string
}

function range(label: string, r: RangeVal): string {
  const a = r.min.trim()
  const b = r.max.trim()
  if (a && b) return `${label} ${a} – ${b}`
  return a ? `${label} ≥ ${a}` : `${label} ≤ ${b}`
}

function dates(label: string, d: DateVal): string {
  if (d.from && d.to) return `${label} ${d.from} → ${d.to}`
  return d.from ? `${label} from ${d.from}` : `${label} until ${d.to}`
}

function setLabel(label: string, v: SetVal): string {
  const parts: string[] = []
  if (v.any.length) parts.push(`any of ${v.any.join(', ')}`)
  if (v.all.length) parts.push(`all of ${v.all.join(', ')}`)
  if (v.none.length) parts.push(`none of ${v.none.join(', ')}`)
  return `${label}: ${parts.join('; ')}`
}

export function activeChips(s: FilterState): Chip[] {
  const out: Chip[] = []
  if (s.text.trim()) out.push({ id: 'text', label: `Search “${s.text.trim()}”` })
  for (const k of RANGE_KEYS) {
    const r = s.ranges[k]
    if (r.min.trim() || r.max.trim()) out.push({ id: `range:${k}`, label: range(RANGE_LABELS[k], r) })
  }
  if (s.language.any.length || s.language.minPct.trim()) {
    const l = s.language.any.length ? s.language.any.join(', ') : 'any'
    out.push({ id: 'language', label: `Language: ${l}${s.language.minPct.trim() ? ` ≥ ${s.language.minPct.trim()}%` : ''}` })
  }
  if (setVal(s.topics)) out.push({ id: 'topics', label: setLabel('Topics', s.topics) })
  if (setVal(s.tech)) out.push({ id: 'tech', label: setLabel('Tech', s.tech) })
  if (s.license.length) out.push({ id: 'license', label: `License: ${s.license.join(', ')}` })
  if (dateVal(s.created)) out.push({ id: 'created', label: dates('Created', s.created) })
  if (dateVal(s.pushed)) out.push({ id: 'pushed', label: dates('Last commit', s.pushed) })
  for (const k of BOOL_KEYS) {
    if (s.tri[k] !== 'any') out.push({ id: `tri:${k}`, label: `${BOOL_LABELS[k]}: ${s.tri[k]}` })
  }
  if (s.query.length) out.push({ id: 'query', label: `Found by: ${s.query.join(', ')}` })
  if (s.runId !== null) out.push({ id: 'run', label: `Run #${s.runId}` })
  return out
}

/** Reset the part of the form a chip stands for. Mutates `s` (works on a Svelte $state proxy). */
export function clearChip(s: FilterState, id: string): void {
  const d = defaultFilters()
  if (id === 'text') s.text = ''
  else if (id.startsWith('range:')) {
    const k = id.slice(6) as RangeKey
    if (k in d.ranges) s.ranges[k] = { min: '', max: '' }
  } else if (id === 'language') s.language = d.language
  else if (id === 'topics') s.topics = d.topics
  else if (id === 'tech') s.tech = d.tech
  else if (id === 'license') s.license = []
  else if (id === 'created') s.created = d.created
  else if (id === 'pushed') s.pushed = d.pushed
  else if (id.startsWith('tri:')) {
    const k = id.slice(4) as BoolKey
    if (k in d.tri) s.tri[k] = 'any'
  } else if (id === 'query') s.query = []
  else if (id === 'run') s.runId = null
}

export function clearAll(s: FilterState): void {
  Object.assign(s, defaultFilters())
}

// ---------------------------------------------------------------- columns

export type ColKind = 'name' | 'text' | 'num' | 'list' | 'langs' | 'date' | 'bool' | 'url'

export interface ColumnDef {
  key: string
  label: string
  kind: ColKind
  width: number
  /** accepted by `data.search` sort (store/query.py SORT_COLUMNS) */
  sortable: boolean
}

function c(key: string, label: string, kind: ColKind, width: number, sortable = true): ColumnDef {
  return { key, label, kind, width, sortable }
}

export const COLUMNS: ColumnDef[] = [
  c('full_name', 'Repository', 'name', 240),
  c('description', 'Description', 'text', 320),
  c('stars', 'Stars', 'num', 90),
  c('forks', 'Forks', 'num', 80),
  c('watchers', 'Watchers', 'num', 90),
  c('language', 'Language', 'text', 120),
  c('languages', 'Languages', 'langs', 200, false),
  c('topics', 'Topics', 'list', 260, false),
  c('tech_stack', 'Tech stack', 'list', 240, false),
  c('license', 'License', 'text', 130),
  c('website', 'Website', 'url', 200),
  c('contributors', 'Contributors', 'num', 110),
  c('commits', 'Commits', 'num', 90),
  c('releases', 'Releases', 'num', 90),
  c('branches', 'Branches', 'num', 90),
  c('tags', 'Tags', 'num', 80),
  c('open_issues', 'Open issues', 'num', 100),
  c('open_pull_requests', 'Open PRs', 'num', 90),
  c('created_at', 'Created', 'date', 120),
  c('updated_at', 'Updated', 'date', 120),
  c('last_commit_at', 'Last commit', 'date', 120),
  c('scraped_at', 'Scraped', 'date', 120),
  c('archived', 'Archived', 'bool', 90),
  c('is_fork', 'Fork', 'bool', 70),
  c('is_template', 'Template', 'bool', 90),
  c('owner', 'Owner', 'text', 140),
  c('default_branch', 'Branch', 'text', 100),
  c('matched_queries', 'Found by', 'list', 220, false),
  c('url', 'URL', 'url', 240),
  c('error', 'Error', 'text', 200),
]

export const COLUMN_BY_KEY: Record<string, ColumnDef> = Object.fromEntries(COLUMNS.map((x) => [x.key, x]))

export const DEFAULT_COLUMNS = [
  'full_name',
  'stars',
  'forks',
  'language',
  'topics',
  'tech_stack',
  'license',
  'last_commit_at',
  'created_at',
  'contributors',
  'description',
  'website',
]

/** Every column the grid can show: requested on every page so toggling columns never refetches. */
export const ALL_COLUMN_KEYS = COLUMNS.map((x) => x.key)

/** Keep only known column keys, de-duplicated, `full_name` first. */
export function normaliseColumns(cols: unknown): string[] {
  const out: string[] = []
  if (Array.isArray(cols)) {
    for (const k of cols) if (typeof k === 'string' && k in COLUMN_BY_KEY && !out.includes(k)) out.push(k)
  }
  const rest = out.filter((k) => k !== 'full_name')
  return ['full_name', ...rest]
}

// ---------------------------------------------------------------- sorting

export type SortDir = 'asc' | 'desc'
export type SortSpec = [string, SortDir][]

export function defaultDir(col: string): SortDir {
  const k = COLUMN_BY_KEY[col]?.kind
  return k === 'num' || k === 'date' ? 'desc' : 'asc'
}

/**
 * Header click. Plain click: sort by this column only (a second click flips the direction). Shift-click: add as
 * a secondary key; shift-clicking a present key flips it, and a third shift-click removes it.
 */
export function nextSort(sort: SortSpec, col: string, shift: boolean): SortSpec {
  const i = sort.findIndex((s) => s[0] === col)
  if (!shift) {
    if (i === 0 && sort.length >= 1) {
      const d: SortDir = sort[0][1] === 'asc' ? 'desc' : 'asc'
      return [[col, d]]
    }
    return [[col, i >= 0 ? sort[i][1] : defaultDir(col)]]
  }
  if (i < 0) return [...sort, [col, defaultDir(col)]]
  const first = defaultDir(col)
  if (sort[i][1] === first) return sort.map((s, j) => (j === i ? ([col, first === 'asc' ? 'desc' : 'asc'] as [string, SortDir]) : s))
  return sort.filter((_, j) => j !== i)
}

// ---------------------------------------------------------------- formatting

export function relTime(iso: unknown, now: number = Date.now()): string {
  if (typeof iso !== 'string' || !iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const s = Math.round((now - t) / 1000)
  const abs = Math.abs(s)
  const suffix = s >= 0 ? 'ago' : 'from now'
  if (abs < 45) return s >= 0 ? 'just now' : 'in a moment'
  const units: [number, string][] = [
    [60, 'min'],
    [3600, 'h'],
    [86400, 'day'],
    [86400 * 30, 'month'],
    [86400 * 365, 'year'],
  ]
  const div = [60, 3600, 86400, 86400 * 30, 86400 * 365]
  let idx = 0
  for (let i = 0; i < div.length; i++) if (abs >= div[i]) idx = i
  const n = Math.max(1, Math.floor(abs / div[idx]))
  const name = units[idx][1]
  const plural = name === 'h' || name === 'min' ? name : n === 1 ? name : `${name}s`
  return `${n} ${plural} ${suffix}`
}

export function exactTime(iso: unknown): string {
  if (typeof iso !== 'string' || !iso) return ''
  const t = Date.parse(iso)
  return Number.isNaN(t) ? iso : new Date(t).toLocaleString()
}

/** `{Python: 71.2, Shell: 3}` -> sorted entries; tolerates a missing or odd value. */
export function languageEntries(v: unknown): [string, number][] {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return []
  return Object.entries(v as Record<string, unknown>)
    .filter((e): e is [string, number] => typeof e[1] === 'number' && Number.isFinite(e[1]))
    .sort((a, b) => b[1] - a[1])
}

export function stringList(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String) : []
}

/** Default file name for an export, e.g. `branch-scrapper-filtered-2026-11-01.csv`. */
export function exportFileName(scope: ExportScope, format: ExportFormat, today: Date = new Date()): string {
  const d = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const name = scope === 'view' ? 'filtered' : scope === 'selected' ? 'selected' : 'all'
  return `branch-scrapper-${name}-${d}.${format}`
}

export type ExportScope = 'view' | 'selected' | 'all'
export type ExportFormat = 'json' | 'csv'

/**
 * The `export.write` params for a scope (minus `path`). `view` uses the filters + sort of the grid, `selected`
 * the chosen names (their order is kept), `all` an empty filter. CSV gets the visible columns in grid order.
 */
export function exportParams(
  scope: ExportScope,
  format: ExportFormat,
  filters: Record<string, unknown>,
  sort: SortSpec | undefined,
  selected: string[],
  columns: string[],
): {
  filters?: Record<string, unknown>
  full_names?: string[]
  sort?: SortSpec
  columns?: string[]
  format: ExportFormat
} {
  const p: { filters?: Record<string, unknown>; full_names?: string[]; sort?: SortSpec; columns?: string[]; format: ExportFormat } = { format }
  if (scope === 'selected') p.full_names = selected
  else if (scope === 'all') p.filters = {}
  else {
    p.filters = filters
    if (sort && sort.length) p.sort = sort
  }
  if (format === 'csv') p.columns = columns
  return p
}
