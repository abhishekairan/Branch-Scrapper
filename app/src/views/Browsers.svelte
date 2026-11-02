<script lang="ts">
  import { onMount } from 'svelte'
  import BrowserCanvas from '../components/BrowserCanvas.svelte'
  import BrowserTile from '../components/BrowserTile.svelte'
  import GridOptions from '../components/GridOptions.svelte'
  import Icon from '../components/Icon.svelte'
  import WorkerChip from '../components/WorkerChip.svelte'
  import {
    activity,
    controlError,
    controlState,
    go,
    resumeScraping,
    run,
    takeControl,
  } from '../lib/app.svelte.ts'
  import { formatBytes } from '../lib/format.ts'
  import { gridPrefs, setGridPrefs, setLayout, view, type Layout } from '../lib/view.svelte.ts'

  const FINISHED = ['done', 'stopped', 'failed']
  const tiles = $derived(gridPrefs.hideFinished ? run.workers.filter((w) => !FINISHED.includes(w.state)) : run.workers)

  onMount(() => {
    view.screenActive = true
    return () => {
      view.screenActive = false
    }
  })

  // keep the selected worker valid when the worker count changes
  $effect(() => {
    if (run.workers.length && view.selected >= run.workers.length) view.selected = 0
  })

  const layouts: { id: Layout; label: string; icon: 'grid' | 'single' | 'list' }[] = [
    { id: 'grid', label: 'Grid', icon: 'grid' },
    { id: 'single', label: 'Single', icon: 'single' },
    { id: 'list', label: 'List', icon: 'list' },
  ]

  function expand(i: number): void {
    view.selected = i
    setLayout('single')
  }

  const sel = $derived(run.workers[view.selected])
  const ctl = $derived(sel ? controlState(sel.index) : 'off')
  const canControl = $derived(
    !!sel && run.active && !['done', 'stopped', 'failed', 'starting', 'recovering'].includes(sel.state),
  )
  const feed = $derived(activity.byWorker[view.selected] ?? [])

  let feedBox: HTMLDivElement | undefined = $state()
  $effect(() => {
    void feed.length
    if (feedBox) feedBox.scrollTop = feedBox.scrollHeight
  })

  function time(t: number): string {
    return new Date(t).toLocaleTimeString([], { hour12: false })
  }
</script>

<section class="screen">
  <div class="toolbar">
    <div class="seg" role="group" aria-label="Layout">
      {#each layouts as l (l.id)}
        <button aria-pressed={view.layout === l.id} onclick={() => setLayout(l.id)}>
          <span class="seg-in"><Icon name={l.icon} size={14} /> {l.label}</span>
        </button>
      {/each}
    </div>

    {#if view.layout === 'grid'}
      <GridOptions />
    {/if}

    {#if view.layout === 'single' && run.workers.length > 1}
      <div class="seg" role="group" aria-label="Worker">
        {#each run.workers as w (w.index)}
          <button aria-pressed={view.selected === w.index} onclick={() => (view.selected = w.index)}>B{w.index + 1}</button>
        {/each}
      </div>
    {/if}

    <span class="grow"></span>
    <span class="muted hint">
      {#if view.layout === 'list'}
        No video in the list view.
      {:else if view.streaming.length}
        Streaming {view.streaming.map((i) => `B${i + 1}`).join(', ')} · only visible browsers are streamed
      {:else}
        Nothing is streaming. Browsers cost nothing while they are not on screen.
      {/if}
    </span>
  </div>

  {#if run.workers.length === 0}
    <div class="empty">
      <div class="card">
        <div class="body">
          <h2>No browsers yet</h2>
          <p class="muted">Browsers appear here while a run or a preview is active.</p>
          <p><button class="btn primary" onclick={() => go('run')}><Icon name="run" /> Go to Run</button></p>
        </div>
      </div>
    </div>
  {:else if view.layout === 'grid'}
    <div class="grid {gridPrefs.density} gap-{gridPrefs.spacing}" class:fixed={gridPrefs.columns > 0} style:--cols={gridPrefs.columns || null}>
      {#each tiles as w (w.index)}
        <BrowserTile worker={w} onexpand={() => expand(w.index)} />
      {:else}
        <p class="muted allhidden">
          All {run.workers.length} browsers have finished.
          <button class="btn ghost sm" onclick={() => setGridPrefs({ hideFinished: false })}>Show them</button>
        </p>
      {/each}
    </div>
  {:else if view.layout === 'list'}
    <div class="list card">
      <table>
        <thead>
          <tr><th>Worker</th><th>State</th><th>Status</th><th>Page</th><th class="r">Pages</th><th class="r">RAM</th><th></th></tr>
        </thead>
        <tbody>
          {#each run.workers as w (w.index)}
            <tr>
              <td class="mono strong">B{w.index + 1}</td>
              <td><WorkerChip worker={w} /></td>
              <td class="ellipsis" title={w.text}>{w.text}</td>
              <td class="ellipsis mono muted" title={w.url ?? ''}>{w.url ?? '—'}</td>
              <td class="r num">{w.pages ?? '—'}</td>
              <td class="r num">{formatBytes(w.ram)}</td>
              <td class="r"><button class="btn ghost sm" onclick={() => expand(w.index)}>Open</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else if sel}
    {#key view.selected}
      <div class="single">
        <div class="main">
          <div class="controls card">
            <div class="body row">
              <span class="wid mono strong">B{sel.index + 1}</span>
              <WorkerChip worker={sel} />
              <span class="grow ellipsis status" title={sel.text}>
                {#if ctl === 'pausing'}Finishing the current page…
                {:else if ctl === 'active'}You are in control. Click the page, then type. Scraping is paused on this worker.
                {:else}{sel.text}{/if}
              </span>
              {#if ctl === 'off'}
                <button class="btn" disabled={!canControl} onclick={() => takeControl(sel.index)}
                  title={canControl ? 'Pause this worker and control its browser yourself' : 'Available while the worker is running'}>
                  <Icon name="hand" /> Take control
                </button>
              {:else}
                <button class="btn primary" onclick={() => resumeScraping(sel.index)}>
                  <Icon name="play" /> {ctl === 'pausing' ? 'Cancel' : 'Resume scraping'}
                </button>
              {/if}
            </div>
            {#if controlError.text}<div class="body tight"><div class="msg err">{controlError.text}</div></div>{/if}
          </div>
          <div class="stage card"><BrowserCanvas index={sel.index} interactive trackWidth /></div>
        </div>

        <aside class="side card">
          <header>Worker B{sel.index + 1}</header>
          <dl class="body">
            <dt>State</dt><dd><WorkerChip worker={sel} /></dd>
            <dt>Status</dt><dd class="selectable">{sel.text}</dd>
            <dt>Page</dt><dd class="mono selectable wrap">{sel.url ?? '—'}</dd>
            <dt>Pages done</dt><dd class="num">{sel.pages ?? '—'}</dd>
            <dt>RAM</dt><dd class="num">{formatBytes(sel.ram)}</dd>
          </dl>
          <header class="sub-h">Activity</header>
          <div class="feed mono selectable" bind:this={feedBox}>
            {#each feed as a, i (i)}
              <div class="fl {a.level}"><span class="muted">{time(a.t)}</span> {a.text}</div>
            {:else}
              <div class="muted pad">No activity yet.</div>
            {/each}
          </div>
        </aside>
      </div>
    {/key}
  {/if}
</section>

<style>
  .screen {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: var(--gap);
    padding: var(--gap);
    min-height: 0;
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: none;
  }
  .seg-in {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .hint {
    font-size: var(--fs-sm);
  }
  .empty {
    flex: 1;
    display: grid;
    place-items: center;
  }
  .empty .card {
    text-align: center;
    max-width: 380px;
  }
  .empty h2 {
    font-size: var(--fs-lg);
    margin-bottom: 4px;
  }
  .empty p {
    margin-bottom: 12px;
  }

  .grid {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: grid;
    --min-tile: 340px;
    grid-template-columns: repeat(auto-fill, minmax(var(--min-tile), 1fr));
    gap: var(--gap);
    align-content: start;
    padding-bottom: 2px;
  }
  .grid.compact {
    --min-tile: 280px;
  }
  .grid.large {
    --min-tile: 460px;
  }
  .grid.fixed {
    grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
  }
  .grid.gap-tight {
    gap: 6px;
  }
  .grid.gap-roomy {
    gap: 20px;
  }
  .allhidden {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 24px 4px;
  }

  .list {
    overflow: auto;
    min-height: 0;
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th {
    text-align: left;
    font-weight: 500;
    color: var(--text-3);
    font-size: var(--fs-sm);
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    max-width: 280px;
  }
  tr:last-child td {
    border-bottom: 0;
  }
  th.r,
  td.r {
    text-align: right;
  }
  .strong {
    font-weight: 700;
  }

  .single {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    gap: var(--gap);
  }
  .main {
    display: flex;
    flex-direction: column;
    gap: var(--gap);
    min-width: 0;
    min-height: 0;
  }
  .controls .body.tight {
    padding-top: 0;
  }
  .status {
    color: var(--text-2);
  }
  .stage {
    position: relative;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
  .side {
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }
  dl {
    display: grid;
    grid-template-columns: 88px 1fr;
    gap: 6px 8px;
    margin: 0;
  }
  dt {
    color: var(--text-3);
  }
  dd {
    margin: 0;
    min-width: 0;
  }
  .wrap {
    word-break: break-all;
  }
  .sub-h {
    border-top: 1px solid var(--border);
  }
  .feed {
    flex: 1;
    overflow: auto;
    padding: 6px 0;
    background: var(--log-bg);
  }
  .fl {
    padding: 1px 12px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .fl.warning {
    color: var(--warn);
  }
  .fl.error {
    color: var(--danger);
  }
  .pad {
    padding: 10px 12px;
    font-family: var(--font);
  }
</style>
