// End-to-end smoke test of the desktop client against the real Python sidecar (test mode, no GitHub traffic).
//
//   cd app && npm run smoke
//
// It starts `app/scripts/dev_sidecar.py` (the real sidecar with --test-mode and the offline fake browsers, which
// also emit screencast frames), then drives it through src/lib/api.ts - the exact client code the UI uses - and
// src/lib/protocol.ts (the frame parser). Needs Node >= 22.6 with --experimental-strip-types (see package.json).

import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { SidecarClient } from '../src/lib/api.ts'
import { toPagePoint, keyEvent } from '../src/lib/input.ts'
import { RPC, RpcError, packFrame, parseFrame } from '../src/lib/protocol.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..', '..')
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe')
const HARNESS = path.join(here, 'dev_sidecar.py')
const ORIGIN = 'http://localhost:1420' // the Vite dev server origin the sidecar accepts

let failures = 0
function ok(cond, msg) {
  if (cond) console.log(`  ok    ${msg}`)
  else {
    failures++
    console.log(`  FAIL  ${msg}`)
  }
}
function step(name) {
  console.log(`\n== ${name}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function until(cond, timeoutMs, what) {
  const end = Date.now() + timeoutMs
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`)
    await sleep(25)
  }
}

// ------------------------------------------------------------------ start the sidecar
const token = randomBytes(32).toString('hex')
const dataDir = mkdtempSync(path.join(tmpdir(), 'branchscrapper-smoke-'))
step('start sidecar (test mode)')
const child = spawn(PY, [HARNESS, '--data-dir', dataDir], {
  cwd: ROOT,
  env: { ...process.env, GHOSTSCRAPER_TOKEN: token, HARNESS_DELAY: '0.4', HARNESS_FRAME_FPS: '20' },
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
ok(ready.port > 0 && ready.pid > 0 && typeof ready.version === 'string', `READY line parsed: ${JSON.stringify(ready)}`)

const info = { port: ready.port, token }
const url = `ws://127.0.0.1:${ready.port}`
const withOrigin = (origin) => (u) => new WebSocket(u, { headers: { Origin: origin } })

try {
  // ---------------------------------------------------------------- handshake rules
  step('origin + token checks (raw sockets)')
  const evil = await new Promise((resolve) => {
    const ws = withOrigin('http://evil.example')(url)
    ws.onopen = () => {
      ws.close()
      resolve('opened')
    }
    ws.onerror = () => resolve('rejected')
    ws.onclose = () => resolve('rejected')
  })
  ok(evil === 'rejected', `foreign Origin is rejected (${evil})`)

  const badToken = await new Promise((resolve) => {
    const ws = withOrigin(ORIGIN)(url)
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: 'x'.repeat(40) }))
    ws.onclose = (e) => resolve(e.code)
    setTimeout(() => resolve('timeout'), 5000)
  })
  ok(badToken === 4401, `wrong token closes with 4401 (${badToken})`)

  // ---------------------------------------------------------------- connect with the app's client
  step('connect with SidecarClient (api.ts)')
  const client = new SidecarClient({ getInfo: async () => info, createSocket: withOrigin(ORIGIN) })
  const states = []
  client.onState((s) => states.push(s))
  client.start()
  await client.waitOpen(10000)
  ok(client.isOpen && states.includes('open'), `connected (${states.join(' -> ')})`)

  const appInfo = await client.call('app.info')
  ok(appInfo.test_mode === true, `app.info: test_mode=${appInfo.test_mode}, max_browsers=${appInfo.max_browsers}`)

  // ---------------------------------------------------------------- config
  step('config.get / config.validate')
  const got = await client.call('config.get')
  ok(got.meta.filters.length > 10 && got.meta.sort_choices.length === 7, `config.get: ${got.meta.filters.length} filters, ${got.meta.sort_choices.length} sort choices`)

  const tiny = {
    queries: ['web scraper'],
    max_results: 10,
    browsers: 2,
    workers_per_chrome: 2,
    deep_scrape: true,
    fetch_manifests: false,
    human_search: false,
    min_delay: 0,
    max_delay: 0,
    filters: { stars: '100+', language: 'python' },
  }
  const v = await client.call('config.validate', { overrides: tiny })
  ok(v.ok && v.queries.length === 1 && /stars:>=100/.test(v.queries[0].github_query) && v.estimate !== null,
    `validate ok, github_query = ${JSON.stringify(v.queries[0]?.github_query)}, estimate ${JSON.stringify(v.estimate)}`)

  const bad = await client.call('config.validate', { overrides: { ...tiny, filters: { stars: 'lots' }, browsers: 99 } })
  ok(!bad.ok && bad.errors.some((e) => e.field === 'filters.stars') && bad.errors.some((e) => e.field === 'browsers'),
    `validate reports per-field errors: ${bad.errors.map((e) => e.field).join(', ')}`)

  // ---------------------------------------------------------------- run + frames + control
  step('browser.view, run.start, events, frames')
  const events = []
  const names = ['run.started', 'run.progress', 'run.finished', 'worker.status', 'repo.saved', 'log', 'pool.stats']
  for (const n of names) client.on(n, (p) => events.push({ n, p, t: Date.now() }))
  const frames = []
  client.onFrame((f) => frames.push(f))

  const viewReply = await client.setView([0], 20, 480)
  ok(viewReply && viewReply.pending.includes(0), `browser.view before the run: pending=${JSON.stringify(viewReply)}`)

  let replyAt = 0
  const started = await client.call('run.start', { overrides: tiny })
  replyAt = Date.now()
  const startedEvt = events.find((e) => e.n === 'run.started')
  ok(started.run_id > 0 && started.workers === 2, `run.start -> ${JSON.stringify(started)}`)
  console.log(`  info  run.started event ${startedEvt ? (startedEvt.t <= replyAt ? 'arrived before/with' : 'arrived after') : 'missing at'} the reply (both orders are legal)`)

  await until(() => frames.some((f) => f.slot === 0), 15000, 'a frame for slot 0')
  const f0 = frames.find((f) => f.slot === 0)
  ok(f0.width === 1280 && f0.height === 800 && f0.jpeg[0] === 0xff && f0.jpeg[1] === 0xd8 && f0.seq >= 1,
    `frame parsed: slot=${f0.slot} seq=${f0.seq} ${f0.width}x${f0.height} jpeg=${f0.jpeg.length} bytes (FFD8 magic ok)`)
  ok(frames.every((f) => f.slot === 0), 'only the watched slot (0) streams')
  await until(() => frames.length >= 5, 8000, '5 frames')
  const seqs = frames.filter((f) => f.slot === 0).map((f) => f.seq)
  ok(seqs.every((s, i) => i === 0 || s > seqs[i - 1]), `sequence numbers increase (${seqs.slice(0, 6).join(', ')} ...)`)

  // input is refused unless the worker is paused
  try {
    await client.call('browser.input', { index: 1, event: { type: 'mouse', action: 'move', x: 5, y: 5 } })
    ok(false, 'browser.input on a running worker should be refused')
  } catch (e) {
    ok(e instanceof RpcError && e.code === RPC.NOT_PAUSED, `browser.input while not paused -> ${e.code} (NOT_PAUSED)`)
  }

  // take control: pause, wait for the "paused" status event, send input, resume
  const pauseReply = await client.call('worker.pause', { index: 0 })
  ok(pauseReply.paused === true, `worker.pause -> ${JSON.stringify(pauseReply)}`)
  await until(
    () => events.some((e) => e.n === 'worker.status' && e.p.index === 0 && e.p.state === 'paused'),
    20000,
    'worker 0 to reach state "paused"',
  )
  ok(true, 'worker.status state=paused received')
  const pt = toPagePoint(400, 300, { left: 100, top: 100, width: 640, height: 500 }, 1280, 800)
  const inputReply = await client.call('browser.input', { index: 0, event: { type: 'mouse', action: 'down', x: pt.x, y: pt.y, button: 'left', click_count: 1 } })
  ok(inputReply.ok === true, `browser.input mouse down at page (${pt.x}, ${pt.y}) while paused`)
  const k = keyEvent('down', { key: 'a', code: 'KeyA', keyCode: 65, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false })
  await client.call('browser.input', { index: 0, event: k })
  ok(true, `browser.input key ${JSON.stringify(k)}`)
  await client.call('worker.resume', { index: 0 })

  await until(() => events.some((e) => e.n === 'run.finished'), 90000, 'run.finished')
  const fin = events.find((e) => e.n === 'run.finished').p
  const saved = events.filter((e) => e.n === 'repo.saved').length
  ok(fin.status === 'done' && fin.counts.repos > 0 && saved === fin.counts.repos,
    `run.finished status=${fin.status} repos=${fin.counts.repos} repo.saved events=${saved}`)
  ok(events.some((e) => e.n === 'run.progress'), `run.progress events: ${events.filter((e) => e.n === 'run.progress').length}`)
  console.log(`  info  pool.stats events: ${events.filter((e) => e.n === 'pool.stats').length} (the fake pool reports no processes)`)
  const logSample = events.filter((e) => e.n === 'log').slice(0, 3).map((e) => `${e.p.level}: ${e.p.text}`)
  console.log(`  info  log samples: ${JSON.stringify(logSample)}`)
  const st = await client.call('run.status')
  ok(st.active === false && st.last?.status === 'done', 'run.status after the run: idle, last=done')

  // ---------------------------------------------------------------- reconnect + view restore
  step('reconnect: backoff, fresh getInfo, view set restored')
  let infoCalls = 0
  const c2 = new SidecarClient({
    getInfo: async () => {
      infoCalls++
      if (infoCalls <= 2) throw new Error('sidecar not ready yet (simulated)')
      return info
    },
    createSocket: withOrigin(ORIGIN),
    backoffMinMs: 50,
    backoffMaxMs: 200,
  })
  const f2 = []
  c2.onFrame((f) => f2.push(f))
  await c2.setView([1], 20, 320) // remembered while not connected
  c2.start()
  await c2.waitOpen(10000)
  ok(infoCalls === 3 && c2.connections === 1, `connected after ${infoCalls} getInfo calls (2 simulated failures)`)

  // start another run so slot 1 exists; the restored view must stream it
  await c2.call('run.start', { overrides: tiny })
  await until(() => f2.some((f) => f.slot === 1), 15000, 'frames for slot 1 from the restored view')
  ok(true, 'frames arrive for the view set that was set before connecting')

  // drop the socket from the client side and watch it come back with the view restored
  const before = f2.length
  c2.ws.close()
  await until(() => c2.connections === 2 && c2.isOpen, 10000, 'automatic reconnect')
  ok(true, `reconnected automatically (connections=${c2.connections}, infoCalls=${infoCalls})`)
  await until(() => f2.length > before + 3, 10000, 'frames after reconnect')
  ok(true, 'frames keep flowing after the reconnect (view set re-sent)')
  await c2.call('run.stop')
  await until(() => c2.isOpen, 1000, 'open')
  await sleep(500)
  c2.close()

  // ---------------------------------------------------------------- pure helpers
  step('parser round trip')
  const packed = packFrame(3, 0xfffffffe, 1920, 1080, new Uint8Array([0xff, 0xd8, 1, 2, 3]))
  const back = parseFrame(packed.buffer)
  ok(back && back.slot === 3 && back.seq === 0xfffffffe && back.width === 1920 && back.height === 1080 && back.jpeg.length === 5,
    `packFrame/parseFrame round trip: ${JSON.stringify({ ...back, jpeg: back?.jpeg.length })}`)
  ok(parseFrame(new Uint8Array([2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]).buffer) === null, 'unknown frame version -> null')
  ok(parseFrame(new Uint8Array(15).buffer) === null, 'header-only message -> null')
  ok(toPagePoint(10, 10, { left: 0, top: 0, width: 800, height: 800 }, 1600, 800) === null, 'point in the letterbox bar -> null')

  // ---------------------------------------------------------------- shutdown by closing stdin
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

console.log(failures === 0 ? '\nSMOKE PASSED' : `\nSMOKE FAILED (${failures} problem${failures > 1 ? 's' : ''})`)
process.exit(failures === 0 ? 0 : 1)
