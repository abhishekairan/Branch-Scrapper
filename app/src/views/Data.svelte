<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import DataDrawer from '../components/DataDrawer.svelte'
  import DataFilters from '../components/DataFilters.svelte'
  import DataGrid from '../components/DataGrid.svelte'
  import { conn, errMessage, go } from '../lib/app.svelte.ts'
  import {
    COLUMNS,
    DEFAULT_COLUMNS,
    activeChips,
    clearAll,
    clearChip,
    searchWords,
  } from '../lib/datafilters.ts'
  import type { ExportFormat, ExportScope } from '../lib/datafilters.ts'
  import {
    acceptConfirm,
    askDeleteDataset,
    askDeleteSelected,
    confirmBox,
    datasetModified,
    datasetNameTaken,
    dismissNotice,
    doExport,
    doImport,
    ds,
    drawer,
    exporting,
    flt,
    grid,
    importing,
    initDataEvents,
    live,
    loadDataset,
    loadMeta,
    lists,
    notices,
    persistUi,
    querySig,
    refreshNow,
    saveDataset,
    scheduleSearch,
    sel,
    effectiveSort,
    syncOnConnect,
    ui,
    unloadDataset,
    DEFAULT_TABLE_PREFS,
    setTablePrefs,
  } from '../lib/data.svelte.ts'
  import type { RowDensity, TablePrefs } from '../lib/data.svelte.ts'
  import { formatCount } from '../lib/format.ts'

  const chips = $derived(activeChips(flt))
  const manyWords = $derived(searchWords(flt.text).length > 1)
  const total = $derived(grid.total)
  const loadedDataset = $derived(lists.datasets.find((d) => d.id === ds.loadedId) ?? null)
  const modified = $derived(datasetModified())
  const relevance = $derived(effectiveSort()?.[0]?.[0] === 'relevance')
  const noData = $derived(total === 0 && chips.length === 0 && (lists.dbRows ?? 0) === 0)
  const noMatch = $derived(total === 0 && !noData)

  onMount(() => {
    initDataEvents()
    void loadMeta()
  })

  // load everything on (re)connect
  $effect(() => {
    void conn.state
    untrack(syncOnConnect)
  })

  // any filter / sort edit re-runs the query after 250 ms
  $effect(() => {
    const sig = querySig()
    if (conn.state !== 'open') return
    if (sig !== untrack(() => grid.sig)) untrack(() => scheduleSearch(false))
  })

  // ------------------------------------------------------------ popovers
  type Pop = 'datasets' | 'save' | 'columns' | 'table' | 'export' | null
  let pop = $state<Pop>(null)
  function toggle(p: Exclude<Pop, null>): void {
    pop = pop === p ? null : p
    if (pop === 'save') {
      saveName = loadedDataset?.name ?? ''
      overwriteAsk = false
      saveError = ''
    }
  }
  function onWindowDown(e: PointerEvent): void {
    if (pop && !(e.target as HTMLElement).closest('[data-pop]')) pop = null
  }
  function onWindowKey(e: KeyboardEvent): void {
    if (e.key === 'Escape' && pop) pop = null
  }

  // ------------------------------------------------------------ datasets
  let saveName = $state('')
  let overwriteAsk = $state(false)
  let saveError = $state('')
  let saving = $state(false)

  async function submitSave(e: Event): Promise<void> {
    e.preventDefault()
    const name = saveName.trim()
    if (!name || saving) return
    if (datasetNameTaken(name) && !overwriteAsk) {
      overwriteAsk = true
      return
    }
    saving = true
    saveError = ''
    try {
      await saveDataset(name)
      pop = null
    } catch (err) {
      saveError = errMessage(err)
    } finally {
      saving = false
    }
  }

  // ------------------------------------------------------------ columns
  const hiddenCols = $derived(COLUMNS.filter((c) => !ui.columns.includes(c.key)))
  function toggleCol(key: string): void {
    if (key === 'full_name') return
    if (ui.columns.includes(key)) ui.columns = ui.columns.filter((k) => k !== key)
    else ui.columns = [...ui.columns, key]
    persistUi()
  }
  function moveCol(key: string, d: -1 | 1): void {
    const i = ui.columns.indexOf(key)
    const j = i + d
    if (i < 1 || j < 1 || j >= ui.columns.length) return
    const next = [...ui.columns]
    ;[next[i], next[j]] = [next[j], next[i]]
    ui.columns = next
    persistUi()
  }
  function resetCols(): void {
    ui.columns = [...DEFAULT_COLUMNS]
    ui.widths = {}
    persistUi()
  }
  const colLabel = (k: string): string => COLUMNS.find((c) => c.key === k)?.label ?? k

  // ------------------------------------------------------------ table display
  const densities: { id: RowDensity; label: string }[] = [
    { id: 'compact', label: 'Compact' },
    { id: 'regular', label: 'Regular' },
    { id: 'tall', label: 'Tall' },
  ]
  const tableChanged = $derived(
    (Object.keys(DEFAULT_TABLE_PREFS) as (keyof TablePrefs)[]).some((k) => ui.table[k] !== DEFAULT_TABLE_PREFS[k]),
  )

  // ------------------------------------------------------------ export
  let expFormat = $state<ExportFormat>('csv')
  function runExport(scope: ExportScope): void {
    pop = null
    void doExport(scope, expFormat)
  }

  function toggleSidebar(): void {
    ui.sidebar = !ui.sidebar
    persistUi()
  }

  const rowsWord = (n: number): string => (n === 1 ? 'result' : 'results')
</script>

<svelte:window onpointerdown={onWindowDown} onkeydown={onWindowKey} />

<section class="data">
  <!-- ============================================================ top bar -->
  <div class="top">
    <button class="btn" aria-pressed={ui.sidebar} onclick={toggleSidebar} title="Show or hide the filters">
      Filters{#if chips.length}<span class="badge">{chips.length}</span>{/if}
    </button>

    <div class="search grow">
      <input
        class="input"
        type="text"
        spellcheck="false"
        placeholder="Search names, descriptions, topics, websites…"
        aria-label="Search"
        bind:value={flt.text}
      />
      {#if grid.fieldErrors.text}<div class="err pop-err">{grid.fieldErrors.text}</div>{/if}
    </div>
    <div
      class="seg wordmode"
      role="group"
      aria-label="How search words combine"
      title={manyWords ? '' : 'Type several words, then choose whether a result needs all of them or any one'}
    >
      <button
        aria-pressed={flt.textMode === 'all'}
        title="Results contain every word"
        onclick={() => (flt.textMode = 'all')}>All words</button>
      <button
        aria-pressed={flt.textMode === 'any'}
        title="Results contain at least one of the words"
        onclick={() => (flt.textMode = 'any')}>Any word</button>
    </div>

    <!-- dataset picker -->
    <div class="pop-wrap" data-pop>
      <button class="btn" onclick={() => toggle('datasets')} aria-haspopup="listbox" aria-expanded={pop === 'datasets'}>
        {loadedDataset ? loadedDataset.name : 'Datasets'}
        {#if loadedDataset && modified}<span class="chip warn nodot">modified</span>{/if}
        <span class="caret">▾</span>
      </button>
      {#if pop === 'datasets'}
        <div class="popover wide">
          {#if lists.datasets.length === 0}
            <div class="muted pad">No saved datasets yet. Set up filters and choose “Save as dataset”.</div>
          {:else}
            <ul class="dslist">
              {#each lists.datasets as d (d.id)}
                <li class:cur={d.id === ds.loadedId}>
                  <button
                    class="dsname"
                    onclick={() => {
                      loadDataset(d)
                      pop = null
                    }}
                  >
                    <span class="ellipsis">{d.name}</span>
                    {#if d.id === ds.loadedId && modified}<span class="chip warn nodot">modified</span>{/if}
                  </button>
                  <button class="btn ghost sm" aria-label="Delete dataset {d.name}" title="Delete" onclick={() => askDeleteDataset(d)}>✕</button>
                </li>
              {/each}
            </ul>
          {/if}
          {#if ds.loadedId !== null}
            <div class="pad foot">
              <button
                class="btn ghost sm"
                onclick={() => {
                  unloadDataset()
                  pop = null
                }}>Detach from “{loadedDataset?.name ?? ''}”</button>
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <!-- save as dataset -->
    <div class="pop-wrap" data-pop>
      <button class="btn" onclick={() => toggle('save')} aria-expanded={pop === 'save'}>Save as dataset</button>
      {#if pop === 'save'}
        <form class="popover" onsubmit={submitSave}>
          <label class="lbl" for="ds-name">Dataset name</label>
          <!-- svelte-ignore a11y_autofocus -->
          <input id="ds-name" class="input" type="text" autofocus maxlength="80" placeholder="e.g. Python scrapers 1k+" bind:value={saveName} oninput={() => (overwriteAsk = false)} />
          <div class="muted small">Saves the filters, visible columns and sort.</div>
          {#if overwriteAsk}
            <div class="msg warn">A dataset named “{saveName.trim()}” already exists. Replace it?</div>
          {/if}
          {#if saveError}<div class="msg err">{saveError}</div>{/if}
          <div class="row end">
            <button type="button" class="btn ghost sm" onclick={() => (pop = null)}>Cancel</button>
            <button class="btn primary sm" type="submit" disabled={!saveName.trim() || saving}>
              {overwriteAsk ? 'Overwrite' : 'Save'}
            </button>
          </div>
        </form>
      {/if}
    </div>

    <!-- columns -->
    <div class="pop-wrap" data-pop>
      <button class="btn" onclick={() => toggle('columns')} aria-expanded={pop === 'columns'}>Columns</button>
      {#if pop === 'columns'}
        <div class="popover cols">
          <div class="scroll">
            {#each ui.columns as k, i (k)}
              <div class="colrow">
                <input type="checkbox" checked disabled={k === 'full_name'} id="c-{k}" onchange={() => toggleCol(k)} />
                <label for="c-{k}" class="grow ellipsis">{colLabel(k)}</label>
                <button class="btn ghost sm" aria-label="Move {colLabel(k)} left" disabled={i < 2} onclick={() => moveCol(k, -1)}>▲</button>
                <button class="btn ghost sm" aria-label="Move {colLabel(k)} right" disabled={i < 1 || i >= ui.columns.length - 1} onclick={() => moveCol(k, 1)}>▼</button>
              </div>
            {/each}
            {#if hiddenCols.length}<div class="sep">Hidden</div>{/if}
            {#each hiddenCols as c (c.key)}
              <div class="colrow">
                <input type="checkbox" id="c-{c.key}" onchange={() => toggleCol(c.key)} />
                <label for="c-{c.key}" class="grow ellipsis muted">{c.label}</label>
              </div>
            {/each}
          </div>
          <div class="pad foot"><button class="btn ghost sm" onclick={resetCols}>Reset columns</button></div>
        </div>
      {/if}
    </div>

    <!-- table display options -->
    <div class="pop-wrap" data-pop>
      <button class="btn" onclick={() => toggle('table')} aria-expanded={pop === 'table'} aria-haspopup="dialog">Table</button>
      {#if pop === 'table'}
        <div class="popover" role="dialog" aria-label="Table options">
          <div class="lbl" id="tbl-rows">Row height</div>
          <div class="seg" role="group" aria-labelledby="tbl-rows">
            {#each densities as d (d.id)}
              <button type="button" aria-pressed={ui.table.density === d.id} onclick={() => setTablePrefs({ density: d.id })}>{d.label}</button>
            {/each}
          </div>
          <p class="muted hint">Tall rows show two lines of long text, such as descriptions.</p>
          <label class="check"><input type="checkbox" checked={ui.table.striped} onchange={(e) => setTablePrefs({ striped: e.currentTarget.checked })} /> Striped rows</label>
          <label class="check"><input type="checkbox" checked={ui.table.colLines} onchange={(e) => setTablePrefs({ colLines: e.currentTarget.checked })} /> Column lines</label>
          <label class="check"><input type="checkbox" checked={ui.table.freezeName} onchange={(e) => setTablePrefs({ freezeName: e.currentTarget.checked })} /> Keep Repository column in view</label>
          <div class="foot row-between">
            <span class="muted hint">Saved on this computer.</span>
            <button class="btn ghost sm" disabled={!tableChanged} onclick={() => setTablePrefs({ ...DEFAULT_TABLE_PREFS })}>Reset</button>
          </div>
        </div>
      {/if}
    </div>

    <button class="btn" onclick={() => void doImport()} disabled={importing.busy} title="Load results saved by the old command-line scraper">
      {importing.busy ? 'Importing…' : 'Import old results'}
    </button>

    <!-- export -->
    <div class="pop-wrap right" data-pop>
      <button class="btn primary" onclick={() => toggle('export')} disabled={exporting.busy} aria-expanded={pop === 'export'}>
        {exporting.busy ? 'Exporting…' : 'Export'} <span class="caret">▾</span>
      </button>
      {#if pop === 'export'}
        <div class="popover">
          <div class="lbl">Format</div>
          <div class="seg" role="group" aria-label="Export format">
            <button type="button" aria-pressed={expFormat === 'csv'} onclick={() => (expFormat = 'csv')}>CSV (Excel)</button>
            <button type="button" aria-pressed={expFormat === 'json'} onclick={() => (expFormat = 'json')}>JSON</button>
          </div>
          <div class="lbl">What to export</div>
          <button class="btn menuitem" onclick={() => runExport('view')} disabled={!total}>
            <span>Current filtered view</span><span class="muted num">{formatCount(total ?? 0)}</span>
          </button>
          <button class="btn menuitem" onclick={() => runExport('selected')} disabled={sel.names.size === 0}>
            <span>Selected rows</span><span class="muted num">{formatCount(sel.names.size)}</span>
          </button>
          <button class="btn menuitem" onclick={() => runExport('all')} disabled={!lists.dbRows}>
            <span>Whole database</span><span class="muted num">{formatCount(lists.dbRows ?? 0)}</span>
          </button>
          {#if expFormat === 'csv'}<div class="muted small">CSV has the visible columns in grid order.</div>{/if}
        </div>
      {/if}
    </div>
  </div>

  <div class="mainrow">
    <!-- ============================================================ filter sidebar -->
    {#if ui.sidebar}
      <aside class="side" aria-label="Filters">
        <div class="side-head">
          <strong>Filters</strong>
          <span class="grow"></span>
          <button class="btn ghost sm" disabled={chips.length === 0} onclick={() => clearAll(flt)}>Clear all</button>
        </div>
        <div class="side-body"><DataFilters /></div>
      </aside>
    {/if}

    <!-- ============================================================ results -->
    <div class="center">
      {#if chips.length}
        <div class="chipbar">
          {#each chips as c (c.id)}
            <span class="fchip">
              <span class="ellipsis" title={c.label}>{c.label}</span>
              <button aria-label="Remove filter: {c.label}" onclick={() => clearChip(flt, c.id)}>×</button>
            </span>
          {/each}
          <button class="btn ghost sm" onclick={() => clearAll(flt)}>Clear all</button>
        </div>
      {/if}

      {#if notices.current}
        {@const n = notices.current}
        <div class="msg notice" class:err={n.kind === 'err'} class:warn={n.kind === 'warn'} class:info={n.kind === 'info' || n.kind === 'ok'} role="status">
          <div class="row">
            <span class="grow selectable">{n.text}</span>
            <button class="btn ghost sm" aria-label="Dismiss" onclick={dismissNotice}>✕</button>
          </div>
          {#if n.lines?.length}
            <ul class="lines selectable">{#each n.lines as l (l)}<li class="ellipsis" title={l}>{l}</li>{/each}</ul>
          {/if}
        </div>
      {/if}

      {#if sel.names.size > 0}
        <div class="selbar">
          <strong>{formatCount(sel.names.size)} selected</strong>
          <button class="btn sm" onclick={() => runExport('selected')} disabled={exporting.busy}>Export selected</button>
          <button class="btn sm danger" onclick={askDeleteSelected}>Delete selected</button>
          <button class="btn ghost sm" onclick={() => sel.clear()}>Clear selection</button>
        </div>
      {/if}

      <div class="gridwrap">
        {#if live.newCount > 0}
          <button class="newpill btn primary" onclick={refreshNow}>
            {formatCount(live.newCount)} new {live.newCount === 1 ? 'result' : 'results'} — refresh
          </button>
        {/if}

        {#if grid.error}
          <div class="state"><div class="msg err">{grid.error}</div></div>
        {:else if Object.keys(grid.fieldErrors).length && total === null}
          <div class="state"><div class="msg err">Some filter values are not valid. See the messages in the filters.</div></div>
        {:else if noData}
          <div class="state">
            <div class="card">
              <div class="body">
                <h2>No data yet</h2>
                <p class="muted">Run a scrape to fill this table, or load results saved by the old command-line scraper.</p>
                <div class="row center-row">
                  <button class="btn primary" onclick={() => go('run')}>Go to Run</button>
                  <button class="btn" onclick={() => void doImport()} disabled={importing.busy}>Import old results</button>
                </div>
              </div>
            </div>
          </div>
        {:else if noMatch}
          <div class="state">
            <div class="card">
              <div class="body">
                <h2>No matches</h2>
                <p class="muted">Nothing in the database fits these filters.</p>
                <div class="row center-row"><button class="btn primary" onclick={() => clearAll(flt)}>Clear all filters</button></div>
              </div>
            </div>
          </div>
        {:else if total === null}
          <div class="state muted">{conn.state === 'open' ? 'Loading…' : 'Waiting for the backend…'}</div>
        {/if}

        <div class="gridbox" class:hidden={total === null || total === 0}>
          <DataGrid />
        </div>
      </div>

      <div class="status muted">
        {#if total !== null}
          <span class="num">{formatCount(total)} {rowsWord(total)}</span>
          {#if chips.length && lists.dbRows}<span>of {formatCount(lists.dbRows)} in the database</span>{/if}
        {/if}
        {#if grid.loading}<span>· loading…</span>{/if}
        {#if relevance}<span>· sorted by relevance</span>{/if}
        <span class="grow"></span>
        <span class="hint">↑ ↓ move · Space select · Shift-click header: extra sort</span>
      </div>
    </div>

    <!-- ============================================================ detail drawer -->
    {#if drawer.name}<DataDrawer />{/if}
  </div>

  <!-- ============================================================ confirm -->
  {#if confirmBox.open}
    <div class="overlay" role="presentation">
      <div class="card dialog" role="alertdialog" aria-modal="true" aria-labelledby="cf-title">
        <header id="cf-title">{confirmBox.title}</header>
        <div class="body stack">
          <p>{confirmBox.body}</p>
          {#if confirmBox.error}<div class="msg err">{confirmBox.error}</div>{/if}
          <div class="row end">
            <button class="btn" onclick={() => (confirmBox.open = false)} disabled={confirmBox.busy}>Cancel</button>
            <button class="btn" class:danger={confirmBox.danger} class:primary={!confirmBox.danger} onclick={() => void acceptConfirm()} disabled={confirmBox.busy}>
              {confirmBox.busy ? 'Working…' : confirmBox.action}
            </button>
          </div>
        </div>
      </div>
    </div>
  {/if}
</section>

<style>
  .data {
    position: relative;
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .top {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .top .btn[aria-pressed='true'] {
    background: var(--accent-weak);
    border-color: var(--accent);
    color: var(--accent);
  }
  .search {
    position: relative;
    min-width: 160px;
  }
  .wordmode {
    flex: none;
  }
  .wordmode button {
    white-space: nowrap;
  }
  .badge {
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    background: var(--accent);
    color: var(--on-accent);
    font-size: 10px;
    line-height: 16px;
    text-align: center;
  }
  .caret {
    font-size: 9px;
    opacity: 0.7;
  }
  .pop-wrap {
    position: relative;
  }
  .popover {
    position: absolute;
    z-index: 30;
    top: calc(100% + 4px);
    left: 0;
    width: 280px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px;
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-lg);
    box-shadow: 0 10px 28px rgb(0 0 0 / 0.22);
  }
  .pop-wrap.right .popover {
    left: auto;
    right: 0;
  }
  .popover.wide {
    width: 320px;
    padding: 4px;
  }
  .popover.cols {
    width: 270px;
    padding: 6px;
  }
  .pad {
    padding: 8px;
  }
  .foot {
    border-top: 1px solid var(--border);
    padding: 6px 4px 2px;
  }
  .lbl {
    color: var(--text-3);
    font-size: var(--fs-sm);
    font-weight: 500;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
  }
  .row-between {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .popover .seg button {
    flex: 1;
  }
  .small {
    font-size: var(--fs-sm);
  }
  .menuitem {
    justify-content: space-between;
    width: 100%;
  }
  .row.end {
    justify-content: flex-end;
  }
  .dslist {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 280px;
    overflow: auto;
  }
  .dslist li {
    display: flex;
    align-items: center;
    border-radius: var(--radius);
  }
  .dslist li.cur {
    background: var(--accent-weak);
  }
  .dsname {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 8px;
    border: 0;
    background: transparent;
    text-align: left;
    border-radius: var(--radius);
  }
  .dsname:hover {
    background: var(--sunken);
  }
  .scroll {
    max-height: 340px;
    overflow: auto;
  }
  .colrow {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 4px;
  }
  .sep {
    margin: 6px 4px 2px;
    color: var(--text-3);
    font-size: var(--fs-xs);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .chip.nodot::before {
    display: none;
  }
  .err {
    color: var(--danger);
    font-size: var(--fs-sm);
  }
  .pop-err {
    position: absolute;
    top: 100%;
    left: 4px;
  }

  .mainrow {
    flex: 1;
    min-height: 0;
    display: flex;
  }
  .side {
    width: 292px;
    flex: none;
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--surface);
    border-right: 1px solid var(--border);
  }
  .side-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .side-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
  .center {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .chipbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .fchip {
    display: inline-flex;
    align-items: center;
    max-width: 380px;
    height: 22px;
    padding: 0 2px 0 9px;
    gap: 2px;
    border-radius: 11px;
    background: var(--accent-weak);
    color: var(--accent);
    font-size: var(--fs-sm);
    font-weight: 600;
  }
  .fchip button {
    flex: none;
    width: 18px;
    height: 18px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: inherit;
    line-height: 1;
  }
  .fchip button:hover {
    background: color-mix(in srgb, var(--accent) 22%, transparent);
  }
  .notice {
    margin: 8px 12px 0;
  }
  .lines {
    margin: 6px 0 0;
    padding-left: 18px;
    font-family: var(--mono);
    font-size: 11.5px;
    max-height: 110px;
    overflow: auto;
  }
  .selbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: var(--accent-weak);
    border-bottom: 1px solid var(--border);
  }
  .gridwrap {
    position: relative;
    flex: 1;
    min-height: 0;
  }
  .gridbox {
    position: absolute;
    inset: 0;
  }
  .gridbox.hidden {
    visibility: hidden;
  }
  .state {
    position: absolute;
    inset: 0;
    z-index: 2;
    display: grid;
    place-items: center;
    padding: 24px;
    text-align: center;
  }
  .state .card {
    max-width: 420px;
  }
  .state h2 {
    font-size: var(--fs-xl);
    margin-bottom: 4px;
  }
  .center-row {
    justify-content: center;
    margin-top: 12px;
  }
  .newpill {
    position: absolute;
    z-index: 6;
    top: 44px;
    left: 50%;
    transform: translateX(-50%);
    border-radius: 15px;
    box-shadow: 0 4px 14px rgb(0 0 0 / 0.25);
  }
  .status {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 26px;
    padding: 0 12px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    font-size: var(--fs-sm);
  }
  .hint {
    font-size: var(--fs-xs);
  }

  .overlay {
    position: absolute;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    background: var(--overlay);
  }
  .dialog {
    width: min(420px, calc(100% - 32px));
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
</style>
