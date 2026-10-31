# Sidecar protocol

The desktop shell talks to the Python sidecar (`python -m ghscraper.sidecar`) over one WebSocket.
Text messages are JSON-RPC 2.0; binary messages are screencast frames.

## 1. Launching

```
GHOSTSCRAPER_TOKEN=<secret, >= 32 chars>  python -m ghscraper.sidecar [--port N] [--data-dir DIR]
```

* No token, or fewer than 32 characters: the process prints an error to stderr and exits with code 2.
  The variable is removed from the process environment so Chrome children never inherit it.
* Binds `127.0.0.1` only. `--port 0` (default) picks a random port.
* When listening, it prints exactly one line to stdout (flushed) and nothing else on stdout ever:

  `READY {"port": 51234, "pid": 4242, "version": "1.0.0"}`

  (On Windows a venv `python.exe` is a launcher: the `pid` in this line is the real sidecar, not necessarily
  the process you spawned. A PyInstaller build has no such launcher.)
* Logs: stderr and `<data dir>/logs/sidecar.log` (rotating, 1 MB x 3).
* Data dir: `--data-dir`, else `GHOSTSCRAPER_HOME`, else the OS app-data dir (`%APPDATA%\BranchScrapper`). When the default dir is used (neither
  `--data-dir` nor `GHOSTSCRAPER_HOME`) and it is missing or empty, the sidecar first copies the pre-rename
  `%APPDATA%\GhostScraper` into it (`migrate_legacy_data_dir`; copy only, nothing is deleted from the old folder).
  Files: `data.db`, `config.json` (created on first run: copied from the repo-local `config.json` if there is
  one, else defaults; the packaged build never looks for a repo-local file), `logs/`.
* Exit: clean shutdown (stop the run, close browsers, close the store, close sockets with code 1001) on
  **stdin EOF** (parent died or closed the pipe), **SIGINT/SIGTERM** (SIGBREAK on Windows) or the
  `app.shutdown` RPC. Exit code 0.
* `--test-mode` enables test-only switches. With it, `GHOSTSCRAPER_FAKE_POOL=1` replaces Chrome by an offline
  fake (needs a source checkout: it serves `tests/fixtures`). Without `--test-mode` that variable is ignored
  (and logged). `app.info` reports `test_mode`. The PyInstaller build excludes the fake pool and refuses
  `--test-mode` (stderr message, exit code 2).

## 2. Connecting and authenticating

URL: `ws://127.0.0.1:<port>`.

1. **Origin check (at the HTTP upgrade).** If an `Origin` header is present it must be exactly
   `tauri://localhost`, `http://tauri.localhost`, `https://tauri.localhost`, or `http://localhost:<digits>`
   (Vite dev server). Anything else gets HTTP 403. Browsers always send `Origin` on WebSocket connections and
   page scripts cannot remove or forge it, so a tab on an arbitrary website cannot connect even if it guesses
   the port. A missing `Origin` (non-browser client such as a script) is accepted, because the token is the
   real credential. `http://127.0.0.1:*` is deliberately not allowed.
2. **Token.** The first message must be a text message `{"type":"auth","token":"<token>"}` within **5 s**.
   Compared with `hmac.compare_digest`. Wrong/missing/late/non-JSON/binary first message: the server closes
   with **code 4401** (reason `unauthorized` or `auth timeout`). No other message is processed before auth.
3. On success the server sends the notification `auth.ok` (`{"version","pid"}`).

Several clients may be connected at once (e.g. after a UI reload). Every authenticated client receives every
event. Inbound messages larger than 1 MiB close the socket with 1009. Binary messages from the client close it
with 1003. JSON-RPC batch arrays are not supported (-32600).

## 3. JSON-RPC conventions

Requests: `{"jsonrpc":"2.0","id":<number|string>,"method":"...","params":{...}}`. `params` is always an
object (or omitted). A request without `id` is a notification: it is executed and gets no reply. Requests run
concurrently; replies may come out of order. **Events caused by a call can arrive before that call's reply**
(e.g. `run.started` before the `run.start` result). Events are notifications:
`{"jsonrpc":"2.0","method":"run.progress","params":{...}}`.

Errors: `{"jsonrpc":"2.0","id":..,"error":{"code":..,"message":"..","data":..}}`

| Code | Name | Meaning |
|---|---|---|
| -32700 | parse error | not JSON (id is null) |
| -32600 | invalid request | not a JSON-RPC 2.0 request object |
| -32601 | method not found | |
| -32602 | invalid params | bad/missing parameter, or a validation failure. For configs `data.errors` is `[{field, message}]` |
| -32603 | internal error | bug; message is `Type: text` |
| -32001 | run active | a run or preview is already active |
| -32002 | no run | nothing is running |
| -32003 | not paused | `browser.input` while the worker is not paused by the user (see 5.4) |
| -32004 | not found | e.g. `data.get` of an unknown repo |
| -32005 | export failed | could not write the file (message has the OS error) |
| -32006 | bad worker | index outside `0..workers-1` of the active run |
| -32007 | browser error | the browser slot failed the operation |

Server-side delivery guarantee: **JSON messages (replies and events) are never dropped** and keep their order
per client. Only screencast frames may be dropped (section 6).

## 4. Config object

`Config` fields (all of `config.json`): `queries: string[]`, `max_results`, `sort` (`best-match|stars|forks|updated`),
`order` (`desc|asc`), `filters` (object keyed by filter name, see `config.get().meta.filters`; values are
normalised: ranges like `">=100"`, `"10..50"`, dates like `"30d.."`), `deep_scrape`, `fetch_manifests`,
`browsers` (1..5), `workers_per_chrome`, `isolate_workers`, `block_resources`, `browser`
(`chrome|msedge|brave`), `browser_path`, `headless`, `human_search`, `min_delay`, `max_delay`, `typing_delay`,
`max_retries`, `proxy`, `user_data_dir`, `page_timeout`, `output_dir`, `formats` (`json`/`csv`).

An **overrides** object contains any subset of those fields. It is overlaid on the saved config; a bad value
reports `{field, message}` (nested filters as `filters.<key>`), unknown keys are errors. `queries` may also
be a string (split on `;`/newline/comma by the CLI rules).

## 5. Methods

### 5.1 App
* `app.info {}` -> `{version, pid, platform, python, data_dir, db_path, config_path, schema_version, run_active, test_mode, max_browsers, frozen, browser, migration}`;
  `migration` = result of the startup data copy from Ghost Scraper (<= 0.1.0): `"none"`, `"migrated"`, `"skipped-existing"` or `"failed: <reason>"`;
  `browser` = `{selected, custom_path, path, available, found:{chrome,msedge,brave -> path|null}}` (is a browser installed? `path` is what a run would launch); `frozen` = running from the PyInstaller build (where `--test-mode` is refused)
* `app.shutdown {}` -> `{ok:true}`; the process begins a clean shutdown ~0.1 s after replying.

### 5.2 Config
* `config.get {}` -> `{config, defaults, meta:{max_browsers, sort_choices:[{label,sort,order}], filters:[{key,label,kind,qualifier,help,group,choices,negate}]}}`
* `config.update {values: <overrides>}` -> `{config}`. Validated for types/values (an empty `queries` list is
  allowed so a draft can be saved), then written to `config.json`. Errors: -32602 with `data.errors`. Nothing is
  applied if any field is invalid.
* `config.validate {overrides?}` -> `{ok, errors:[{field,message}], warnings:[{field,message,query?}], queries:[{query, github_query, warnings:[string]}], filters_described:{key:text}, estimate:{pages, seconds_low, seconds_high, workers, chromes} | null}`.
  `github_query` is the exact `q=` string sent to GitHub. Warnings include `check_query` typo hints (`topc:`).
  Runnability rules apply (no queries is an error). Nothing is saved.

### 5.3 Runs
* `run.start {overrides?}` -> `{run_id, workers}`. Errors: -32602 (`data.errors`), -32001. Creates a `runs` row,
  scrapes, saves each repo as it arrives, ends with `run.finished`.
* `run.preview_counts {overrides?}` -> `{status, totals:{query:int|null}, queries:[{query, github_query, total, over_cap}]}`.
  Loads page 1 of each search only; nothing is stored. The reply comes when finished (can take a while; other
  calls keep working). It occupies the single run slot (-32001 if busy) and emits `run.started`/`run.finished`
  with `kind:"preview"` and `run_id:null`. `run.stop` cancels it (`status:"stopped"`, partial totals).
* `run.stop {}` -> `{stopping:bool, run_id?}`. Idempotent (`stopping:false` when idle). Completion is the
  `run.finished` event (`status:"stopped"`).
* `run.status {}` -> idle: `{active:false, last:<last run.finished payload>|null}`; running: `{active:true, kind, run_id, stopping, elapsed, workers:[{index,text,state,paused}], progress:<run.progress payload>}`.
* `worker.pause {index}` -> `{index, paused:true, engaged}`; `worker.resume {index}` -> `{index, paused:false}`.
  Pausing lets the worker finish its current page/task; `engaged` is true once it is already waiting. The
  `worker.status` event with `state:"paused"` signals that it is now idle and safe to control. Errors: -32002, -32006.
  Stopping a run releases paused workers.

### 5.4 Browsers
* `browser.view {indices:[int], fps?=2, max_width?=480}` -> `{active:[int], pending:[int]}`.
  `fps` 1..30, `max_width` 160..1920, indices in 0..4. **Replaces this client's whole view set**: exactly
  those slots stream, and every slot nobody watches gets `stop_screencast`. `[]` stops all of this client's
  streams. `pending` = slots that do not exist yet (they start streaming as soon as the worker has a browser).
  With several clients the union is streamed at the highest requested fps/width. A client's streams stop when
  it disconnects. The view set persists across worker crashes/recycles and across runs: streaming restarts
  automatically when a watched slot reappears.
* `browser.input {index, event}` -> `{ok:true}`. `event` is one of
  `{"type":"mouse","action":"move|down|up|wheel","x","y","button":"left|right|middle","click_count","delta_x","delta_y","modifiers"}`
  (x, y in page CSS pixels), `{"type":"key","action":"down|up|raw|char","key","code","text","vk","modifiers"}`,
  `{"type":"text","text":"..."}`. **Only allowed while the worker is paused by the user *and* has reached the
  paused state** (`worker.status` state `paused`); otherwise -32003. Bad event contents: -32602; the browser
  failing: -32007.

### 5.5 Data (all read the SQLite store; filters are the JSON filter language of `store/query.py`)
* `data.search {filters?, sort?, limit?=100 (0..1000), offset?=0, columns?}` -> `{rows:[record], total}`.
  `sort` is `[[column, "asc"|"desc"], ...]` (`"relevance"` allowed with a `text` filter). Default sort: stars desc
  (relevance when searching text). Unknown filter keys / columns / sort columns: -32602.
* `data.facets {limit?}` -> `{languages, topics, tech, licenses}`, each `[{value,count}]`.
* `data.get {full_name}` -> the full record; -32004 if missing.
* `data.delete {full_names:[string] (<=10000)}` -> `{deleted:n}`.
* `data.import_json {paths:[absolute path]}` -> `{imported:[{file,run_id,repos,skipped_records}], skipped:[{file,reason}], total_repos}`.
* `datasets.list {}` -> `{datasets:[{id,name,filters,columns,sort,created_at,updated_at}]}`;
  `datasets.save {name, filters?, columns?, sort?}` -> `{id}` (upsert by name; validated, -32602);
  `datasets.delete {id}` -> `{deleted:bool}`.
* `runs.list {}` -> `{runs:[{id,started_at,finished_at,status,config,counts}]}`, newest first
  (`status`: running|done|stopped|failed|imported).

### 5.6 Export
`export.write {filters | full_names | run_id, sort?, columns?, format, path}` -> `{rows, path, format}`

* Exactly one scope: `filters` (object; `{}` = everything), `full_names` (list; keeps your order, `sort`
  ignored) or `run_id` (int).
* `format`: `json` or `csv`. `path`: **absolute**, extension must match (`.json` / `.csv`), not a directory.
  The UI gets it from the native save dialog. Parent folders are created.
* Streams from the store in chunks (not loaded into memory), written to `<path>.part` and renamed on success;
  nothing is left behind on failure. CSV: UTF-8 with BOM, lists/dicts flattened, formula-looking cells prefixed
  with `'`. JSON: array of records. `columns` selects/orders fields (CSV default: the standard column set).
* Errors: -32602 (bad path/scope/filters/columns), -32005 (I/O).

## 6. Binary frames (screencast)

Server -> client only, one frame per binary message, big-endian:

```
offset size  field
0      u8    version = 1
1      u16   slot index (worker index, same as in worker.* events)
3      u32   frame sequence number (per slot, starts at 1 each run, wraps at 2^32)
7      u32   width  (CSS px of the page viewport at capture time)
11     u32   height
15     ...   JPEG bytes
```

Input coordinates for `browser.input` are in the same CSS-pixel space (scale tile coordinates by
`width / tile_width`). Frames only flow for slots in the client's `browser.view` set.

**Backpressure:** each client has a one-frame buffer per slot. If the socket is busy a newer frame replaces the
unsent one (latest wins; sequence numbers will skip). JSON messages are sent before frames and are never
dropped. A client that stops reading will therefore only ever cost one frame per slot of memory plus its queued
JSON.

## 7. Events (server -> client notifications)

| Event | Params |
|---|---|
| `run.started` | `{run_id|null, kind:"run"|"preview", workers, queries, config}` |
| `run.progress` | `{run_id, queries_done, queries_total, repos_done, repos_total, eta_seconds|null, elapsed}`. `repos_total` grows while searches finish. ETA is a rough extrapolation (null until 2 repos are done) |
| `run.finished` | `{run_id|null, kind, status:"done"|"stopped"|"failed", error|null, counts:{repos,repos_total,queries_done,queries_total,errors,write_errors,elapsed}, totals? (preview)}`. `failed` also when every browser died with work left |
| `worker.status` | `{index, text, state}`; `text` is plain (no markup); `state`: starting, searching, repo, idle, paused, recovering, done, stopped, failed, other |
| `repo.saved` | `{run_id, full_name, url, description, stars, forks, language, topics, error|null, scraped_at}`, sent after the row is committed |
| `log` | `{level:"debug"|"info"|"warning"|"error", text}`; rich markup is converted to plain text, level from the colour (red=error, yellow=warning, dim=debug) |
| `pool.stats` | every 2 s while a run is active: `{run_id, chrome_count, process_count, total_rss, total_uss|null, chromes:[{pid,processes,rss,uss,workers:[index]}], slots:[{index,rss,uss,chrome}]}`. Bytes. Per-slot figures are the Chrome's tree split evenly between the workers sharing it, and are null when the process-to-worker mapping is unknown |
| `auth.ok` | sent once after authentication |
