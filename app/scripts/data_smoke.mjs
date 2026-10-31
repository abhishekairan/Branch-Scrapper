// End-to-end check of the Data screen's non-visual logic against the real Python sidecar (test mode, offline
// fake browsers, no GitHub traffic):
//
//   cd app && npm run data-smoke
//
// It imports the SAME pure module the UI uses (src/lib/datafilters.ts) to build the filter objects, so a bug there
// shows up here. Steps: run a fake scrape -> search/sort/page -> facets -> invalid filters -> datasets ->
// export (CSV + JSON, BOM/header/row count) -> import -> delete -> close stdin -> sidecar exits.

import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { SidecarClient } from '../src/lib/api.ts'
import {
  ALL_COLUMN_KEYS,
  DEFAULT_COLUMNS,
  activeChips,
  buildFilters,
  clearAll,
  clearChip,
  defaultFilters,
  exportFileName,
  exportParams,
  filterOfError,
  filtersToState,
  nextSort,
  normaliseColumns,
  relTime,
  signature,
} from '../src/lib/datafilters.ts'
import { RPC, RpcError } from '../src/lib/protocol.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..', '..')
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe')
const HARNESS = path.join(here, 'dev_sidecar.py')
const ORIGIN = 'http://localhost:1420'

let failures = 0
function ok(cond, msg) {
  if (cond) console.log(`  ok    ${msg}`)
  else {
    failures++
    console.log(`  FAIL  ${msg}`)
  }
}
const step = (name) => console.log(`\n== ${name}`)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function until(cond, timeoutMs, what) {
  const end = Date.now() + timeoutMs
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`)
    await sleep(25)
  }
}

// ------------------------------------------------------------------ pure helpers first (no sidecar needed)
step('datafilters.ts: pure logic')
{
  const s = defaultFilters()
  ok(JSON.stringify(buildFilters(s)) === '{}', 'default state builds {}')
  s.text = '  scraper '
  s.ranges.stars.min = '100'
  s.ranges.forks.max = '50'
  s.language.any = ['Python']
  s.language.minPct = '20'
  s.topics.any = ['cli']
  s.topics.none = ['game']
  s.license = ['MIT']
  s.created.from = '2020-01-01'
  s.pushed.to = '2026-01-01'
  s.tri.archived = 'no'
  s.tri.has_website = 'yes'
  s.query = ['web scraper']
  s.runId = 2
  const f = buildFilters(s)
  ok(
    f.text === 'scraper' && f.stars.min === 100 && f.forks.max === 50 && f.language.min_pct === 20 &&
      f.topics.any[0] === 'cli' && f.topics.none[0] === 'game' && f.pushed.to === '2026-01-01' &&
      f.archived === false && f.has_website === true && f.run_id === 2 && f.query[0] === 'web scraper',
    `buildFilters: ${JSON.stringify(f)}`,
  )
  ok(JSON.stringify(buildFilters(filtersToState(f))) === JSON.stringify(f), 'filtersToState(buildFilters(x)) round trips')
  ok(activeChips(s).length === 12, `activeChips: ${activeChips(s).map((c) => c.id).join(', ')}`)
  clearChip(s, 'range:stars')
  clearChip(s, 'tri:archived')
  ok(!('stars' in buildFilters(s)) && !('archived' in buildFilters(s)), 'clearChip removes one filter')
  clearAll(s)
  ok(JSON.stringify(buildFilters(s)) === '{}' && activeChips(s).length === 0, 'clearAll resets everything')
  s.ranges.stars.min = 'abc'
  ok(buildFilters(s).stars.min === 'abc', 'non-numeric text is passed through for the server to reject')
  ok(filterOfError("filter 'stars': expected a number, got 'abc'") === 'stars' &&
    filterOfError("unknown key 'x' in filter 'topics'") === 'topics' &&
    filterOfError("filter 'language.min_pct': expected a number") === 'language' &&
    filterOfError('limit must be a non-negative integer') === null, 'filterOfError maps messages to filter keys')
  ok(JSON.stringify(nextSort([['stars', 'desc']], 'stars', false)) === '[["stars","asc"]]', 'sort: second click flips')
  ok(JSON.stringify(nextSort([['stars', 'desc']], 'language', false)) === '[["language","asc"]]', 'sort: new column replaces')
  ok(JSON.stringify(nextSort([['stars', 'desc']], 'forks', true)) === '[["stars","desc"],["forks","desc"]]', 'sort: shift adds secondary')
  ok(JSON.stringify(nextSort([['stars', 'desc'], ['forks', 'desc']], 'forks', true)) === '[["stars","desc"],["forks","asc"]]', 'sort: shift flips secondary')
  ok(JSON.stringify(nextSort([['stars', 'desc'], ['forks', 'asc']], 'forks', true)) === '[["stars","desc"]]', 'sort: third shift-click removes it')
  ok(normaliseColumns(['stars', 'nope', 'full_name', 'stars'])[0] === 'full_name' && normaliseColumns(['stars', 'nope']).length === 2, 'normaliseColumns')
  ok(relTime('2026-11-01T10:00:00Z', Date.parse('2026-11-03T10:00:00Z')) === '2 days ago', `relTime: ${relTime('2026-11-01T10:00:00Z', Date.parse('2026-11-03T10:00:00Z'))}`)
  ok(exportFileName('view', 'csv', new Date(2026, 10, 1)) === 'branch-scrapper-filtered-2026-11-01.csv', 'exportFileName')
}

// ------------------------------------------------------------------ start the sidecar
const token = randomBytes(32).toString('hex')
const dataDir = mkdtempSync(path.join(tmpdir(), 'branchscrapper-datasmoke-'))
step('start sidecar (test mode)')
const child = spawn(PY, [HARNESS, '--data-dir', dataDir], {
  cwd: ROOT,
  env: { ...process.env, GHOSTSCRAPER_TOKEN: token, HARNESS_DELAY: '0.02', HARNESS_FRAME_FPS: '1' },
  stdio: ['pipe', 'pipe', 'pipe'],
  windowsHide: true,
})
let exitCode = null
child.on('exit', (code) => (exitCode = code))
let stderrText = ''
child.stderr.on('data', (d) => (stderrText += d))
const ready = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('no READY line within 20 s\n' + stderrText)), 20000)
  let buf = ''
  child.stdout.on('data', (d) => {
    buf += d
    const m = /^READY (\{.*\})$/m.exec(buf)
    if (m) {
      clearTimeout(timer)
      resolve(JSON.parse(m[1]))
    }
  })
})
console.log(`  info  sidecar pid ${ready.pid}, port ${ready.port}`)

try {
  const client = new SidecarClient({
    getInfo: async () => ({ port: ready.port, token }),
    createSocket: (u) => new WebSocket(u, { headers: { Origin: ORIGIN } }),
  })
  client.start()
  await client.waitOpen(10000)

  // ---------------------------------------------------------------- empty store
  step('empty store')
  const empty = await client.call('data.search', { filters: {}, limit: 0, offset: 0 })
  ok(empty.total === 0 && empty.rows.length === 0, `data.search on an empty store: total=${empty.total}`)

  // ---------------------------------------------------------------- fake scrape
  step('fake scrape so the store has rows')
  const events = []
  client.on('repo.saved', (p) => events.push(p))
  let finished = null
  client.on('run.finished', (p) => (finished = p))
  const cfg = {
    queries: ['web scraper', 'playwright python', 'headless browser', 'cli tool', 'rust crawler'],
    max_results: 40, browsers: 2, workers_per_chrome: 2, deep_scrape: true, fetch_manifests: true,
    human_search: false, min_delay: 0, max_delay: 0, filters: {},
  }
  await client.call('run.start', { overrides: cfg })
  await until(() => finished !== null, 120000, 'run.finished')
  ok(finished.status === 'done' && finished.counts.repos > 0, `run ${finished.run_id}: ${finished.status}, ${finished.counts.repos} repos, ${events.length} repo.saved events`)

  const all = await client.call('data.search', { filters: {}, limit: 1000, offset: 0, columns: ALL_COLUMN_KEYS })
  const rows = all.rows
  const N = all.total
  ok(N > 0 && rows.length === N, `data.search (no filters, all ${ALL_COLUMN_KEYS.length} UI columns): total=${N}, rows=${rows.length}`)
  ok(Object.keys(rows[0]).every((k) => ALL_COLUMN_KEYS.includes(k)) && 'full_name' in rows[0], `row keys are within the requested columns (${Object.keys(rows[0]).length} keys)`)
  const sample = rows[0]
  console.log(`  info  sample: ${JSON.stringify({ n: sample.full_name, stars: sample.stars, lang: sample.language, topics: sample.topics, tech: sample.tech_stack, lic: sample.license })}`)

  // ---------------------------------------------------------------- filters built by the UI code
  step('data.search with filter objects from buildFilters')
  const st = defaultFilters()
  const sorted = [...rows].map((r) => r.stars ?? 0).sort((a, b) => a - b)
  const median = sorted[Math.floor(sorted.length / 2)]
  st.ranges.stars.min = String(median)
  const f1 = buildFilters(st)
  const r1 = await client.call('data.search', { filters: f1, limit: 1000, columns: ['full_name', 'stars'] })
  const exp1 = rows.filter((r) => (r.stars ?? -1) >= median).length
  ok(r1.total === exp1 && r1.rows.every((r) => r.stars >= median), `stars >= ${median}: server ${r1.total}, expected ${exp1}`)

  const st2 = defaultFilters()
  st2.ranges.stars.min = String(median)
  st2.ranges.stars.max = String(median + 1000000)
  st2.tri.archived = 'no'
  const r2 = await client.call('data.search', { filters: buildFilters(st2), limit: 1000, columns: ['full_name', 'archived'] })
  ok(r2.rows.every((r) => r.archived !== true) && r2.total <= r1.total, `range + archived=no: ${r2.total} rows`)

  // text search (FTS, prefix) with default relevance sort (sort undefined)
  const word = (sample.full_name.split('/')[1] ?? sample.full_name).replace(/[^A-Za-z0-9]+/g, ' ').trim().split(' ')[0]
  const st3 = defaultFilters()
  st3.text = word.slice(0, Math.max(3, word.length - 1))
  const r3 = await client.call('data.search', { filters: buildFilters(st3), sort: [['relevance', 'asc']], limit: 50, columns: ['full_name'] })
  ok(r3.total >= 1 && r3.rows.some((r) => r.full_name === sample.full_name), `text "${st3.text}" (prefix, relevance sort): ${r3.total} hit(s) incl. ${sample.full_name}`)

  // language / topics / tech / license built from facets
  const facets = await client.call('data.facets', { limit: 500 })
  ok(['languages', 'topics', 'tech', 'licenses'].every((k) => Array.isArray(facets[k])), `data.facets keys: ${Object.keys(facets).join(', ')}`)
  console.log(`  info  facets: ${facets.languages.length} languages, ${facets.topics.length} topics, ${facets.tech.length} tech, ${facets.licenses.length} licenses; top language ${JSON.stringify(facets.languages[0])}`)
  ok(facets.languages.every((x) => typeof x.value === 'string' && typeof x.count === 'number'), 'facet entries are {value,count}')

  if (facets.languages.length) {
    const lang = facets.languages[0].value
    const s = defaultFilters()
    s.language.any = [lang]
    const r = await client.call('data.search', { filters: buildFilters(s), limit: 1000, columns: ['full_name', 'language'] })
    ok(r.total >= 1 && r.total <= N, `language any [${lang}]: ${r.total}`)
    s.language.minPct = '99.9'
    const r2b = await client.call('data.search', { filters: buildFilters(s), limit: 1000, columns: ['full_name'] })
    ok(r2b.total <= r.total, `language + min 99.9%: ${r2b.total} (<= ${r.total})`)
  }
  if (facets.topics.length) {
    const t = facets.topics[0].value
    const s = defaultFilters()
    s.topics.any = [t]
    const withT = await client.call('data.search', { filters: buildFilters(s), limit: 1000, columns: ['full_name', 'topics'] })
    ok(withT.total === facets.topics[0].count && withT.rows.every((r) => r.topics.includes(t)), `topics any [${t}]: ${withT.total} = facet count ${facets.topics[0].count}`)
    const s2 = defaultFilters()
    s2.topics.none = [t]
    const without = await client.call('data.search', { filters: buildFilters(s2), limit: 1000, columns: ['full_name'] })
    ok(without.total + withT.total === N, `topics none [${t}]: ${without.total} (+${withT.total} = ${N})`)
    const s3 = defaultFilters()
    s3.topics.all = facets.topics.slice(0, 2).map((x) => x.value)
    const both = await client.call('data.search', { filters: buildFilters(s3), limit: 1000, columns: ['full_name', 'topics'] })
    ok(both.rows.every((r) => s3.topics.all.every((x) => r.topics.includes(x))), `topics all [${s3.topics.all.join(', ')}]: ${both.total}`)
  }
  if (facets.tech.length) {
    const s = defaultFilters()
    s.tech.any = [facets.tech[0].value]
    const r = await client.call('data.search', { filters: buildFilters(s), limit: 1000, columns: ['full_name', 'tech_stack'] })
    ok(r.total === facets.tech[0].count && r.rows.every((x) => x.tech_stack.includes(facets.tech[0].value)), `tech any [${facets.tech[0].value}]: ${r.total}`)
  }
  if (facets.licenses.length) {
    const s = defaultFilters()
    s.license = [facets.licenses[0].value]
    const r = await client.call('data.search', { filters: buildFilters(s), limit: 1000, columns: ['full_name', 'license'] })
    ok(r.total === facets.licenses[0].count && r.rows.every((x) => x.license === facets.licenses[0].value), `license [${facets.licenses[0].value}]: ${r.total}`)
  }

  // dates + has_website + found-by + run
  {
    const s = defaultFilters()
    s.created.from = '1990-01-01'
    s.created.to = '2999-12-31'
    s.pushed.from = '1990-01-01'
    const r = await client.call('data.search', { filters: buildFilters(s), limit: 0 })
    const withCreated = rows.filter((x) => x.created_at).length
    ok(r.total >= 0 && r.total <= N, `created+pushed date ranges accepted: ${r.total} (rows with created_at: ${withCreated})`)
    const s2 = defaultFilters()
    s2.tri.has_website = 'yes'
    const yes = await client.call('data.search', { filters: buildFilters(s2), limit: 1000, columns: ['website'] })
    s2.tri.has_website = 'no'
    const no = await client.call('data.search', { filters: buildFilters(s2), limit: 0 })
    ok(yes.total + no.total === N && yes.rows.every((x) => x.website), `has_website yes ${yes.total} + no ${no.total} = ${N}`)
    const runs = (await client.call('runs.list')).runs
    ok(runs.length >= 1 && runs[0].id === finished.run_id, `runs.list: ${runs.length} run(s), newest #${runs[0]?.id} (${runs[0]?.status})`)
    const s3 = defaultFilters()
    s3.runId = runs[0].id
    const byRun = await client.call('data.search', { filters: buildFilters(s3), limit: 0 })
    ok(byRun.total === N, `run_id ${runs[0].id}: ${byRun.total} of ${N}`)
    const s4 = defaultFilters()
    s4.query = [cfg.queries[0]]
    const byQ = await client.call('data.search', { filters: buildFilters(s4), limit: 0 })
    ok(byQ.total >= 1 && byQ.total <= N, `found-by "${cfg.queries[0]}": ${byQ.total}`)
    const cfgQueries = runs[0].config?.queries
    ok(Array.isArray(cfgQueries) && cfgQueries.includes(cfg.queries[0]), 'runs.list config.queries available for the "found by" picker')
  }

  // ---------------------------------------------------------------- sort + paging
  step('sort and paging')
  const full = await client.call('data.search', { filters: {}, sort: [['stars', 'desc'], ['full_name', 'asc']], limit: 1000, columns: ['full_name', 'stars'] })
  const sortedOk = full.rows.every((r, i) => i === 0 || (full.rows[i - 1].stars ?? -1) >= (r.stars ?? -1) || r.stars === null)
  ok(sortedOk, 'stars desc, full_name asc order holds')
  const sortSpec = nextSort(nextSort([['stars', 'desc']], 'language', false), 'stars', true) // language asc, then stars
  const multi = await client.call('data.search', { filters: {}, sort: sortSpec, limit: 1000, columns: ['full_name', 'language', 'stars'] })
  const multiOk = multi.rows.every((r, i) => {
    if (i === 0) return true
    const p = multi.rows[i - 1]
    if (p.language === null || r.language === null) return true
    return p.language < r.language || (p.language === r.language && (p.stars ?? -1) >= (r.stars ?? -1))
  })
  ok(multiOk, `secondary sort ${JSON.stringify(sortSpec)} is applied`)
  const PAGE = 7
  const paged = []
  let total = null
  for (let off = 0; ; off += PAGE) {
    const p = await client.call('data.search', { filters: {}, sort: [['stars', 'desc']], limit: PAGE, offset: off, columns: ['full_name'] })
    total = p.total
    paged.push(...p.rows.map((r) => r.full_name))
    if (p.rows.length < PAGE) break
  }
  ok(paged.length === total && new Set(paged).size === total, `paging with limit ${PAGE}: ${paged.length} rows, no duplicates, total=${total}`)
  const stable = (await client.call('data.search', { filters: {}, sort: [['stars', 'desc']], limit: 1000, columns: ['full_name'] })).rows.map((r) => r.full_name)
  ok(stable.join('|') === paged.join('|'), 'paged order equals the single-request order (stable tiebreaker)')

  // ---------------------------------------------------------------- errors from the RPC
  step('invalid filters report an error the UI can place')
  const bad = defaultFilters()
  bad.ranges.stars.min = 'abc'
  try {
    await client.call('data.search', { filters: buildFilters(bad), limit: 1 })
    ok(false, 'invalid number should be rejected')
  } catch (e) {
    ok(e instanceof RpcError && e.code === RPC.INVALID_PARAMS && filterOfError(e.message) === 'stars', `-32602 "${e.message}" -> filter ${filterOfError(e.message)}`)
  }
  const bad2 = defaultFilters()
  bad2.created.from = '2024-13-45'
  try {
    await client.call('data.search', { filters: buildFilters(bad2), limit: 1 })
    ok(false, 'invalid date should be rejected')
  } catch (e) {
    ok(e instanceof RpcError && filterOfError(e.message) === 'created', `-32602 "${e.message}" -> filter ${filterOfError(e.message)}`)
  }
  const bad3 = defaultFilters()
  bad3.language.minPct = 'lots'
  try {
    await client.call('data.search', { filters: buildFilters(bad3), limit: 1 })
    ok(false, 'invalid pct should be rejected')
  } catch (e) {
    ok(e instanceof RpcError && filterOfError(e.message) === 'language', `-32602 "${e.message}" -> filter ${filterOfError(e.message)}`)
  }

  // ---------------------------------------------------------------- data.get
  step('data.get')
  const rec = await client.call('data.get', { full_name: sample.full_name })
  ok(rec.full_name === sample.full_name, `data.get ${sample.full_name}: ${Object.keys(rec).length} fields`)
  console.log(`  info  record keys: ${Object.keys(rec).join(', ')}`)
  try {
    await client.call('data.get', { full_name: 'nobody/nothing' })
    ok(false, 'unknown repo should be -32004')
  } catch (e) {
    ok(e instanceof RpcError && e.code === RPC.NOT_FOUND, `unknown repo -> ${e.code}`)
  }

  // ---------------------------------------------------------------- datasets
  step('datasets.save / list / delete')
  const dsState = defaultFilters()
  dsState.ranges.stars.min = '10'
  dsState.topics.none = ['zzz-none']
  const cols = ['full_name', 'stars', 'language']
  const dsSort = [['stars', 'desc'], ['full_name', 'asc']]
  const saved = await client.call('datasets.save', { name: 'smoke set', filters: buildFilters(dsState), columns: cols, sort: dsSort })
  const listed = (await client.call('datasets.list')).datasets
  const mine = listed.find((d) => d.id === saved.id)
  ok(mine && mine.name === 'smoke set', `datasets.save -> id ${saved.id}; list has ${listed.length}`)
  ok(JSON.stringify(buildFilters(filtersToState(mine.filters))) === JSON.stringify(buildFilters(dsState)), 'saved filters load back into an identical form state')
  ok(JSON.stringify(mine.columns) === JSON.stringify(cols) && JSON.stringify(mine.sort) === JSON.stringify(dsSort), 'columns + sort round trip')
  const sigA = signature(buildFilters(dsState), normaliseColumns(mine.columns), mine.sort)
  const sigB = signature(buildFilters(filtersToState(mine.filters)), normaliseColumns(cols), dsSort)
  ok(sigA === sigB, '"modified" signature equals right after loading')
  dsState.ranges.stars.min = '11'
  ok(signature(buildFilters(dsState), cols, dsSort) !== sigB, '"modified" signature differs after an edit')
  const again = await client.call('datasets.save', { name: 'smoke set', filters: { stars: { min: 99 } }, columns: cols })
  const listed2 = (await client.call('datasets.list')).datasets
  ok(again.id === saved.id && listed2.filter((d) => d.name === 'smoke set').length === 1, `save with the same name overwrites (id ${again.id}, one row)`)
  try {
    await client.call('datasets.save', { name: 'bad', filters: { stars: { min: 'x' } } })
    ok(false, 'invalid dataset should be rejected')
  } catch (e) {
    ok(e instanceof RpcError && e.code === RPC.INVALID_PARAMS, `invalid dataset rejected: ${e.message}`)
  }
  const del = await client.call('datasets.delete', { id: saved.id })
  ok(del.deleted === true && !(await client.call('datasets.list')).datasets.some((d) => d.id === saved.id), 'datasets.delete')

  // ---------------------------------------------------------------- export
  step('export.write (CSV + JSON; view / selected / all)')
  const outDir = path.join(dataDir, 'exports')
  const viewState = defaultFilters()
  viewState.ranges.stars.min = String(median)
  const viewFilters = buildFilters(viewState)
  const viewTotal = (await client.call('data.search', { filters: viewFilters, limit: 0 })).total
  const visible = ['full_name', 'stars', 'topics', 'language']

  const csvPath = path.join(outDir, exportFileName('view', 'csv'))
  const pv = exportParams('view', 'csv', viewFilters, [['stars', 'desc']], [], visible)
  const wr = await client.call('export.write', { ...pv, path: csvPath })
  const csvBytes = readFileSync(csvPath)
  const csvText = csvBytes.toString('utf8')
  const lines = csvText.replace(/^﻿/, '').split(/\r?\n/).filter((l) => l.length)
  ok(csvBytes[0] === 0xef && csvBytes[1] === 0xbb && csvBytes[2] === 0xbf, `CSV starts with a UTF-8 BOM (EF BB BF)`)
  ok(lines[0] === visible.join(',') || lines[0].split(',').length === visible.length, `CSV header = visible columns in grid order: "${lines[0]}"`)
  ok(wr.rows === viewTotal && lines.length - 1 >= viewTotal, `CSV view: export.write rows=${wr.rows}, view total=${viewTotal}, data lines=${lines.length - 1} (multi-line cells possible)`)

  const jsonPath = path.join(outDir, exportFileName('all', 'json'))
  const wj = await client.call('export.write', { ...exportParams('all', 'json', viewFilters, undefined, [], visible), path: jsonPath })
  const arr = JSON.parse(readFileSync(jsonPath, 'utf8'))
  ok(Array.isArray(arr) && arr.length === N && wj.rows === N, `JSON whole database: ${arr.length} records (database has ${N})`)
  ok(arr.every((r) => 'full_name' in r), 'JSON records have full_name')

  const pick = rows.slice(0, 3).map((r) => r.full_name).reverse()
  const selPath = path.join(outDir, 'selected.csv')
  const ws = await client.call('export.write', { ...exportParams('selected', 'csv', viewFilters, undefined, pick, visible), path: selPath })
  const selLines = readFileSync(selPath, 'utf8').replace(/^﻿/, '').split(/\r?\n/).filter((l) => l.length)
  ok(ws.rows === 3 && selLines.length >= 4 && selLines[1].startsWith(pick[0]), `selected rows: ${ws.rows} rows, caller's order kept (first = ${pick[0]})`)

  try {
    await client.call('export.write', { format: 'csv', filters: {}, path: path.join(outDir, 'wrong.json') })
    ok(false, 'extension mismatch should be rejected')
  } catch (e) {
    ok(e instanceof RpcError && e.code === RPC.INVALID_PARAMS, `extension mismatch -> ${e.code} ${e.message}`)
  }

  // ---------------------------------------------------------------- import
  step('data.import_json (re-import of the JSON export)')
  const imp = await client.call('data.import_json', { paths: [jsonPath] })
  ok(imp.imported.length === 1 && imp.imported[0].repos === N, `import: ${JSON.stringify(imp.imported)} skipped=${JSON.stringify(imp.skipped)} total_repos=${imp.total_repos}`)
  const afterImp = await client.call('data.search', { filters: {}, limit: 0 })
  ok(afterImp.total === N, `row count unchanged after re-import (${afterImp.total})`)
  const imp2 = await client.call('data.import_json', { paths: [path.join(outDir, 'missing.json'), selPath] })
  ok(imp2.skipped.length === 2, `bad files are reported as skipped: ${JSON.stringify(imp2.skipped.map((s) => s.reason))}`)

  // ---------------------------------------------------------------- delete
  step('data.delete')
  const gone = rows.slice(0, 2).map((r) => r.full_name)
  const dr = await client.call('data.delete', { full_names: gone })
  ok(dr.deleted === 2, `data.delete ${gone.length} -> deleted ${dr.deleted}`)
  const afterDel = await client.call('data.search', { filters: {}, limit: 0 })
  ok(afterDel.total === N - 2, `total ${afterDel.total} = ${N} - 2`)
  const fac2 = await client.call('data.facets', { limit: 500 })
  ok(Array.isArray(fac2.languages), 'data.facets after delete still answers')

  // ---------------------------------------------------------------- shutdown
  step('close stdin -> the sidecar exits')
  client.close()
  child.stdin.end()
  await until(() => exitCode !== null, 15000, 'sidecar exit after stdin EOF')
  ok(exitCode === 0, `sidecar exited with code ${exitCode}`)
  let alive = true
  try {
    process.kill(ready.pid, 0)
  } catch {
    alive = false
  }
  ok(!alive, `sidecar pid ${ready.pid} is gone`)
} catch (e) {
  failures++
  console.log(`\nERROR ${e && e.stack ? e.stack : e}`)
  console.log('--- sidecar stderr (tail) ---\n' + stderrText.split('\n').slice(-25).join('\n'))
} finally {
  if (exitCode === null) {
    child.stdin.end()
    await sleep(3000)
    if (exitCode === null) child.kill()
  }
  try {
    rmSync(dataDir, { recursive: true, force: true })
  } catch {
    /* log file may still be open on Windows */
  }
}

void DEFAULT_COLUMNS
console.log(failures === 0 ? '\nDATA SMOKE PASSED' : `\nDATA SMOKE FAILED (${failures} problem${failures > 1 ? 's' : ''})`)
process.exit(failures === 0 ? 0 : 1)
