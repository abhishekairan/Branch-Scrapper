<script lang="ts">
  import Field from '../components/Field.svelte'
  import FilterForm from '../components/FilterForm.svelte'
  import Icon from '../components/Icon.svelte'
  import LogPane from '../components/LogPane.svelte'
  import {
    cfg,
    clock,
    conn,
    dismissMigrationNotice,
    errorsFor,
    migrationNotice,
    previewCounts,
    recent,
    recheckBrowsers,
    resetToDefaults,
    run,
    startRun,
    stopRun,
    warningsFor,
  } from '../lib/app.svelte.ts'
  import { formatCount, formatDuration, formatRange } from '../lib/format.ts'
  import type { BrowserKind, WorkerState } from '../lib/protocol.ts'

  const ready = $derived(conn.state === 'open' && cfg.loaded)
  const val = $derived(cfg.validation)
  const hasErrors = $derived(!!val && !val.ok)
  const canStart = $derived(ready && !run.active && !run.busy && !hasErrors)
  const canPreview = $derived(ready && !run.active && !run.busy && !hasErrors)

  const sortValue = $derived(cfg.draft ? `${cfg.draft.sort}|${cfg.draft.order}` : '')
  function setSort(v: string): void {
    const [sort, order] = v.split('|')
    if (cfg.draft) {
      cfg.draft.sort = sort
      cfg.draft.order = order as 'asc' | 'desc'
    }
  }

  const elapsed = $derived(run.active ? (clock.now - run.startedAt) / 1000 : (run.finished?.counts.elapsed ?? null))
  const pct = $derived.by(() => {
    const p = run.progress
    if (!p || !p.repos_total) return 0
    return Math.min(100, (p.repos_done / p.repos_total) * 100)
  })

  const chipTone: Record<WorkerState, string> = {
    starting: 'info',
    searching: 'info',
    repo: 'accent',
    idle: '',
    paused: 'warn',
    recovering: 'warn',
    done: 'ok',
    stopped: '',
    failed: 'danger',
    other: '',
  }

  const nonFieldErrors = $derived(
    (val?.errors ?? []).filter((e) => !['queries', 'max_results', 'min_delay', 'max_delay'].includes(e.field) && !e.field.startsWith('filters')),
  )
  const queryWarnings = $derived((val?.warnings ?? []).filter((w) => w.field !== 'queries'))
  // First-run check: the browser chosen in the settings is not installed (an explicit executable path is trusted).
  const BROWSER_LABEL: Record<string, string> = { chrome: 'Google Chrome', msedge: 'Microsoft Edge', brave: 'Brave' }
  const browserIssue = $derived.by(() => {
    const found = conn.browsersFound
    const d = cfg.draft
    if (!found || !d || d.browser_path || found[d.browser]) return null
    return { selected: d.browser, others: (Object.keys(found) as BrowserKind[]).filter((k) => found[k]) }
  })
  const showProgress = $derived(run.active || run.finished !== null)
</script>

<section class="run">
  <!-- ============================================================ settings -->
  <div class="left">
    {#if !cfg.draft}
      <div class="card"><div class="body muted">{conn.state === 'open' ? 'Loading settings…' : 'Waiting for the backend…'}</div></div>
    {:else}
      {@const d = cfg.draft}

      <div class="card">
        <header>Search</header>
        <div class="body stack">
          <Field label="Search queries" hint="one per line" errors={errorsFor('queries')} warnings={warningsFor('queries')}>
            <textarea
              class="textarea"
              class:bad={errorsFor('queries').length > 0}
              rows="5"
              spellcheck="false"
              placeholder={'web scraper\nplaywright python'}
              bind:value={cfg.queriesText}
            ></textarea>
          </Field>
          <div class="two">
            <Field label="Max results per query" errors={errorsFor('max_results')} warnings={warningsFor('max_results')}>
              <input class="input num" type="number" min="1" max="1000" step="1" bind:value={d.max_results} />
            </Field>
            <Field label="Sort by" errors={errorsFor('sort').concat(errorsFor('order'))}>
              <select class="select" value={sortValue} onchange={(e) => setSort(e.currentTarget.value)}>
                {#each cfg.meta?.sort_choices ?? [] as s (`${s.sort}|${s.order}`)}
                  <option value={`${s.sort}|${s.order}`}>{s.label}</option>
                {/each}
              </select>
            </Field>
          </div>
        </div>
      </div>

      <div class="card">
        <header>Filters <span class="sub">added to every query</span></header>
        <div class="body"><FilterForm /></div>
      </div>

      <div class="card">
        <header>What to collect</header>
        <div class="body stack">
          <Field label="Deep scrape" hint="Open every repository page for the full details" inline errors={errorsFor('deep_scrape')}>
            <input type="checkbox" bind:checked={d.deep_scrape} />
          </Field>
          <Field label="Fetch manifests" hint="Read package.json, pyproject.toml, … to detect the tech stack" inline>
            <input type="checkbox" bind:checked={d.fetch_manifests} disabled={!d.deep_scrape} />
          </Field>
        </div>
      </div>

      <div class="card">
        <header>Browsers</header>
        <div class="body stack">
          <div class="two">
            <Field label="Workers" hint="1 – {conn.maxBrowsers}" errors={errorsFor('browsers')}>
              <input class="input num" type="number" min="1" max={conn.maxBrowsers} step="1" bind:value={d.browsers} />
            </Field>
            <Field label="Workers per Chrome" errors={errorsFor('workers_per_chrome')}>
              <input class="input num" type="number" min="1" max={conn.maxBrowsers} step="1" bind:value={d.workers_per_chrome} disabled={d.isolate_workers} />
            </Field>
          </div>
          <Field label="Isolate workers" hint="One Chrome per worker: more RAM, no shared fate" inline>
            <input type="checkbox" bind:checked={d.isolate_workers} />
          </Field>
          <Field label="Headless" hint="Faster to hide, but easier for GitHub to detect. Leave off unless you need it." inline>
            <input type="checkbox" bind:checked={d.headless} />
          </Field>
          <Field label="Block images, fonts and media" hint="Saves bandwidth and RAM; the parsers only need HTML" inline>
            <input type="checkbox" bind:checked={d.block_resources} />
          </Field>
        </div>
      </div>

      <div class="card">
        <header>Pacing</header>
        <div class="body stack">
          <div class="two">
            <Field label="Min delay (s)" hint="between page loads" errors={errorsFor('min_delay')}>
              <input class="input num" type="number" min="0" step="0.5" bind:value={d.min_delay} />
            </Field>
            <Field label="Max delay (s)" errors={errorsFor('max_delay')}>
              <input class="input num" type="number" min="0" step="0.5" bind:value={d.max_delay} />
            </Field>
          </div>
          <details class="adv">
            <summary>Advanced</summary>
            <div class="stack adv-body">
              <Field label="Browser" errors={errorsFor('browser')}>
                <select class="select" bind:value={d.browser}>
                  <option value="chrome">Chrome</option>
                  <option value="msedge">Microsoft Edge</option>
                  <option value="brave">Brave</option>
                </select>
              </Field>
              <Field label="Type the query into GitHub's search box" hint="more human-like, slower" inline>
                <input type="checkbox" bind:checked={d.human_search} />
              </Field>
              <div class="two">
                <Field label="Typing delay (s)" errors={errorsFor('typing_delay')}>
                  <input class="input num" type="number" min="0" step="0.01" bind:value={d.typing_delay} />
                </Field>
                <Field label="Retries" errors={errorsFor('max_retries')}>
                  <input class="input num" type="number" min="0" step="1" bind:value={d.max_retries} />
                </Field>
              </div>
              <Field label="Page timeout (s)" errors={errorsFor('page_timeout')}>
                <input class="input num" type="number" min="5" step="5" bind:value={d.page_timeout} />
              </Field>
              <Field label="Proxy" hint="host:port or user:pass@host:port; comma-separate for one per browser" errors={errorsFor('proxy')}>
                <input class="input" type="text" spellcheck="false" bind:value={d.proxy} />
              </Field>
            </div>
          </details>
          <div class="row foot">
            <button class="btn ghost sm" onclick={resetToDefaults}>Reset all to defaults</button>
            <span class="grow"></span>
            {#if cfg.saveError}<span class="err-text" title={cfg.saveError}>could not save settings</span>
            {:else}<span class="muted small">Settings are saved automatically</span>{/if}
          </div>
        </div>
      </div>
    {/if}
  </div>

  <!-- ============================================================ run -->
  <div class="right">
    <div class="actions card">
      <div class="body row">
        <button class="btn primary" disabled={!canStart} onclick={startRun}>
          <Icon name="play" /> {run.busy === 'start' ? 'Starting…' : 'Start run'}
        </button>
        <button class="btn" disabled={!canPreview} onclick={previewCounts}>
          <Icon name="eye" /> {run.busy === 'preview' ? 'Counting…' : 'Preview counts'}
        </button>
        <button class="btn danger" disabled={!run.active || run.stopping} onclick={stopRun}>
          <Icon name="stop" /> {run.stopping ? 'Stopping…' : 'Stop'}
        </button>
        <span class="grow"></span>
        {#if cfg.validating}<span class="muted small">checking…</span>{/if}
      </div>
      {#if browserIssue}
        <div class="body tight">
          <div class="msg err browser-issue">
            <div>
              <strong>{BROWSER_LABEL[browserIssue.selected] ?? browserIssue.selected} was not found on this computer.</strong>
              {#if browserIssue.others.length}
                Runs need a Chromium browser. Switch to one that is installed:
              {:else}
                Runs need a Chromium browser and none was found. Install Google Chrome (google.com/chrome), or use
                Microsoft Edge, which ships with Windows, then check again.
              {/if}
            </div>
            <div class="row">
              {#each browserIssue.others as name (name)}
                <button
                  class="btn sm"
                  onclick={() => {
                    if (cfg.draft) cfg.draft.browser = name
                  }}>Use {BROWSER_LABEL[name] ?? name}</button
                >
              {/each}
              <button class="btn ghost sm" onclick={recheckBrowsers}>Check again</button>
            </div>
          </div>
        </div>
      {/if}
      {#if migrationNotice.visible}
        <div class="body tight">
          <div class="msg browser-issue" class:warn={migrationNotice.failed !== ''} class:info={migrationNotice.failed === ''} role="status">
            <div>
              {#if migrationNotice.failed}
                <strong>Your data could not be moved from Ghost Scraper.</strong>
                Branch Scrapper started with an empty data folder; your old data is untouched in the Ghost Scraper folder
                ({migrationNotice.failed}).
              {:else}
                <strong>Your data was moved from Ghost Scraper.</strong>
                It was copied to {conn.dataDir || 'the Branch Scrapper data folder'}. The old Ghost Scraper folder was left in
                place (it contains a MIGRATED_TO.txt note) and is safe to delete once you have checked everything is here.
              {/if}
            </div>
            <div class="row">
              <button class="btn ghost sm" onclick={dismissMigrationNotice}>Dismiss</button>
            </div>
          </div>
        </div>
      {/if}
      {#if run.error}<div class="body tight"><div class="msg err">{run.error}</div></div>{/if}
      {#if cfg.validationError}<div class="body tight"><div class="msg warn">{cfg.validationError}</div></div>{/if}
    </div>

    <div class="card">
      <header>
        Plan
        {#if val}<span class="sub">{val.queries.length} {val.queries.length === 1 ? 'query' : 'queries'}</span>{/if}
      </header>
      <div class="body stack">
        {#if !val}
          <span class="muted">Fill in a query to see exactly what will be searched.</span>
        {:else}
          {#each nonFieldErrors as e (e.field + e.message)}
            <div class="msg err"><strong>{e.field}:</strong> {e.message}</div>
          {/each}
          {#if val.errors.some((e) => e.field === 'queries')}
            <div class="msg err">{val.errors.find((e) => e.field === 'queries')?.message}</div>
          {/if}
          {#each queryWarnings as w (w.field + w.message)}
            <div class="msg warn">{w.message}</div>
          {/each}

          {#if val.queries.length}
            <table class="q">
              <thead><tr><th>Query</th><th>Sent to GitHub as</th></tr></thead>
              <tbody>
                {#each val.queries as q (q.query)}
                  <tr>
                    <td class="ellipsis" title={q.query}>{q.query}</td>
                    <td class="mono selectable">
                      {q.github_query ?? '(invalid filters)'}
                      {#each q.warnings as w (w)}<div class="qwarn">{w}</div>{/each}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}

          {#if Object.keys(val.filters_described).length}
            <div class="described">
              {#each Object.entries(val.filters_described) as [k, v] (k)}
                <span class="tag"><span class="muted">{k}</span> {v}</span>
              {/each}
            </div>
          {/if}

          {#if val.estimate}
            <div class="estimate num">
              <strong>{formatRange(val.estimate.seconds_low, val.estimate.seconds_high)}</strong>
              <span class="muted">
                · ~{formatCount(val.estimate.pages)} page loads · {val.estimate.workers}
                {val.estimate.workers === 1 ? 'worker' : 'workers'} in {val.estimate.chromes}
                {val.estimate.chromes === 1 ? 'Chrome' : 'Chromes'}
              </span>
            </div>
          {/if}
        {/if}
      </div>
    </div>

    {#if run.preview}
      <div class="card">
        <header>
          Preview
          <span class="sub">{run.preview.status === 'done' ? 'result counts from GitHub' : run.preview.status}</span>
        </header>
        <div class="body">
          <table class="q">
            <thead><tr><th>Query</th><th class="r">Matching repos</th></tr></thead>
            <tbody>
              {#each run.preview.queries as q (q.query)}
                <tr>
                  <td class="ellipsis" title={q.github_query}>{q.query}</td>
                  <td class="r num">
                    {q.total === null ? 'unknown' : formatCount(q.total)}
                    {#if q.over_cap}<span class="chip warn" title="GitHub only returns the first 1000 results of a search">capped at 1,000</span>{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}

    {#if showProgress}
      <div class="card">
        <header>
          {run.active ? (run.kind === 'preview' ? 'Preview' : 'Progress') : 'Last result'}
          {#if run.finished && !run.active}
            <span class="chip {run.finished.status === 'done' ? 'ok' : run.finished.status === 'failed' ? 'danger' : 'warn'}">{run.finished.status}</span>
          {/if}
          <span class="sub num">{elapsed !== null ? formatDuration(elapsed) : ''}</span>
        </header>
        <div class="body stack">
          {#if run.active && run.progress}
            {@const p = run.progress}
            <div class="bar"><div class="fill" style:width="{pct}%"></div></div>
            <div class="stats num">
              <div><strong>{formatCount(p.repos_done)}</strong><span class="muted"> / {formatCount(p.repos_total)} repos</span></div>
              <div><strong>{p.queries_done}</strong><span class="muted"> / {p.queries_total} queries</span></div>
              <div><span class="muted">ETA </span><strong>{formatDuration(p.eta_seconds)}</strong></div>
              <div><strong>{formatCount(run.saved)}</strong><span class="muted"> saved</span></div>
            </div>
          {:else if run.active}
            <span class="muted">Starting browsers…</span>
          {:else if run.finished}
            {@const c = run.finished.counts}
            {#if run.finished.error}<div class="msg err">{run.finished.error}</div>{/if}
            <div class="stats num">
              <div><strong>{formatCount(c.repos)}</strong><span class="muted"> repos</span></div>
              <div><strong>{c.queries_done}</strong><span class="muted"> / {c.queries_total} queries</span></div>
              <div><strong>{c.errors}</strong><span class="muted"> errors</span></div>
              {#if c.write_errors}<div><strong>{c.write_errors}</strong><span class="muted"> write errors</span></div>{/if}
            </div>
          {/if}

          {#if run.active && run.workers.length}
            <ul class="workers">
              {#each run.workers as w (w.index)}
                <li>
                  <span class="wid mono">B{w.index + 1}</span>
                  <span class="chip {chipTone[w.state]}">{w.state}</span>
                  <span class="grow ellipsis" title={w.text}>{w.text}</span>
                </li>
              {/each}
            </ul>
          {/if}

          {#if recent.items.length}
            <div class="recent">
              <div class="muted small">Latest saved</div>
              {#each recent.items.slice(-5).reverse() as r (r.full_name)}
                <div class="row repo">
                  <span class="grow ellipsis">{r.full_name}</span>
                  <span class="muted num">{r.language ?? ''}</span>
                  <span class="num">★ {formatCount(r.stars)}</span>
                  {#if r.error}<span class="chip danger" title={r.error}>error</span>{/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}

    <div class="logwrap"><LogPane /></div>
  </div>
</section>

<style>
  .browser-issue {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .run {
    height: 100%;
    display: grid;
    grid-template-columns: minmax(380px, 5fr) minmax(420px, 6fr);
    gap: var(--gap);
    padding: var(--gap);
    min-height: 0;
  }
  .left,
  .right {
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--gap);
    padding-right: 2px;
  }
  .left > .card,
  .right > .card {
    flex: none;
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .two {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .small {
    font-size: var(--fs-sm);
  }
  .err-text {
    color: var(--danger);
    font-size: var(--fs-sm);
  }
  .foot {
    margin-top: 2px;
  }
  .adv > summary {
    cursor: pointer;
    color: var(--text-2);
    font-weight: 500;
  }
  .adv-body {
    margin-top: 10px;
  }
  .actions .body.tight {
    padding-top: 0;
  }

  table.q {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }
  table.q th {
    text-align: left;
    font-weight: 500;
    color: var(--text-3);
    font-size: var(--fs-sm);
    padding: 0 8px 4px 0;
  }
  table.q th.r,
  table.q td.r {
    text-align: right;
  }
  table.q td {
    padding: 4px 8px 4px 0;
    border-top: 1px solid var(--border);
    vertical-align: top;
  }
  table.q td:first-child {
    width: 34%;
  }
  .qwarn {
    color: var(--warn);
    font-family: var(--font);
    font-size: var(--fs-sm);
  }
  .described {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .tag {
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    background: var(--sunken);
    font-size: var(--fs-sm);
  }
  .estimate {
    padding-top: 2px;
  }

  .bar {
    height: 8px;
    border-radius: 4px;
    background: var(--sunken);
    overflow: hidden;
  }
  .bar .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s ease;
  }
  .stats {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 22px;
  }
  .workers {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .workers li {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .wid {
    width: 24px;
    color: var(--text-3);
  }
  .recent .repo {
    padding: 2px 0;
    border-top: 1px solid var(--border);
  }
  .logwrap {
    flex: 1 0 280px;
    min-height: 280px;
    display: flex;
    flex-direction: column;
  }
  .logwrap :global(.pane) {
    flex: 1;
  }
</style>
