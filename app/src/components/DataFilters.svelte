<script lang="ts">
  import TagPicker from './TagPicker.svelte'
  import { BOOL_KEYS, BOOL_LABELS, RANGE_KEYS, RANGE_LABELS } from '../lib/datafilters.ts'
  import type { SetMode, SetVal, Tri } from '../lib/datafilters.ts'
  import { flt, grid, lists } from '../lib/data.svelte.ts'
  import { formatCount } from '../lib/format.ts'

  const errs = $derived(grid.fieldErrors)

  let topicMode = $state<SetMode>('any')
  let techMode = $state<SetMode>('any')
  const MODES: SetMode[] = ['any', 'all', 'none']
  const TRI: Tri[] = ['any', 'yes', 'no']

  const queryOptions = $derived.by(() => {
    const counts = new Map<string, number>()
    for (const r of lists.runs) {
      const qs = (r.config as { queries?: unknown }).queries
      if (Array.isArray(qs)) for (const q of qs) if (typeof q === 'string') counts.set(q, (counts.get(q) ?? 0) + 1)
    }
    return [...counts.keys()].sort().map((value) => ({ value, count: 0 }))
  })

  function runLabel(r: { id: number; started_at: string; status: string; counts: Record<string, unknown> }): string {
    const n = typeof r.counts.repos === 'number' ? ` · ${formatCount(r.counts.repos)} repos` : ''
    return `#${r.id} · ${r.started_at.slice(0, 10)} · ${r.status}${n}`
  }

  function onRun(v: string): void {
    flt.runId = v === '' ? null : Number(v)
  }
</script>

{#snippet setFilter(key: 'topics' | 'tech', label: string, opts: { value: string; count: number }[], mode: SetMode, setMode: (m: SetMode) => void)}
  {@const v = flt[key] as SetVal}
  <div class="seg small" role="group" aria-label="{label} match mode">
    {#each MODES as m (m)}
      <button type="button" aria-pressed={mode === m} onclick={() => setMode(m)}>
        {m}{v[m].length ? ` (${v[m].length})` : ''}
      </button>
    {/each}
  </div>
  <TagPicker
    {label}
    options={opts}
    bind:selected={flt[key][mode]}
    placeholder={mode === 'any' ? 'Has at least one of…' : mode === 'all' ? 'Has all of…' : 'Has none of…'}
    free
  />
  {#if errs[key]}<div class="err">{errs[key]}</div>{/if}
{/snippet}

<div class="filters">
  <details open>
    <summary>Numbers</summary>
    <div class="grid2">
      {#each RANGE_KEYS as k (k)}
        <label for="r-{k}-min">{RANGE_LABELS[k]}</label>
        <input id="r-{k}-min" class="input num" class:bad={!!errs[k]} type="text" inputmode="numeric" placeholder="min" bind:value={flt.ranges[k].min} />
        <input class="input num" class:bad={!!errs[k]} type="text" inputmode="numeric" placeholder="max" aria-label="{RANGE_LABELS[k]} max" bind:value={flt.ranges[k].max} />
        {#if errs[k]}<div class="err span">{errs[k]}</div>{/if}
      {/each}
    </div>
  </details>

  <details open>
    <summary>Language{flt.language.any.length ? ` (${flt.language.any.length})` : ''}</summary>
    <div class="stack">
      <TagPicker label="Language" options={lists.facets?.languages ?? []} bind:selected={flt.language.any} placeholder="Any of these languages…" />
      <div class="row">
        <label for="lang-pct">at least</label>
        <input id="lang-pct" class="input num pct" class:bad={!!errs.language} type="text" inputmode="decimal" placeholder="0" bind:value={flt.language.minPct} />
        <span class="muted">% of the code</span>
      </div>
      {#if errs.language}<div class="err">{errs.language}</div>{/if}
    </div>
  </details>

  <details open>
    <summary>Topics{(['any', 'all', 'none'] as const).some((m) => flt.topics[m].length) ? ' •' : ''}</summary>
    <div class="stack">{@render setFilter('topics', 'Topics', lists.facets?.topics ?? [], topicMode, (m) => (topicMode = m))}</div>
  </details>

  <details>
    <summary>Tech stack{(['any', 'all', 'none'] as const).some((m) => flt.tech[m].length) ? ' •' : ''}</summary>
    <div class="stack">{@render setFilter('tech', 'Tech stack', lists.facets?.tech ?? [], techMode, (m) => (techMode = m))}</div>
  </details>

  <details>
    <summary>License{flt.license.length ? ` (${flt.license.length})` : ''}</summary>
    <div class="stack">
      <TagPicker label="License" options={lists.facets?.licenses ?? []} bind:selected={flt.license} placeholder="Any of these licenses…" />
      {#if errs.license}<div class="err">{errs.license}</div>{/if}
    </div>
  </details>

  <details>
    <summary>Dates</summary>
    <div class="stack">
      <div class="lbl">Created</div>
      <div class="row">
        <input class="input" class:bad={!!errs.created} type="date" aria-label="Created from" bind:value={flt.created.from} />
        <span class="muted">to</span>
        <input class="input" class:bad={!!errs.created} type="date" aria-label="Created to" bind:value={flt.created.to} />
      </div>
      {#if errs.created}<div class="err">{errs.created}</div>{/if}
      <div class="lbl">Last commit</div>
      <div class="row">
        <input class="input" class:bad={!!errs.pushed} type="date" aria-label="Last commit from" bind:value={flt.pushed.from} />
        <span class="muted">to</span>
        <input class="input" class:bad={!!errs.pushed} type="date" aria-label="Last commit to" bind:value={flt.pushed.to} />
      </div>
      {#if errs.pushed}<div class="err">{errs.pushed}</div>{/if}
    </div>
  </details>

  <details>
    <summary>Flags</summary>
    <div class="stack">
      {#each BOOL_KEYS as k (k)}
        <div class="row between">
          <span>{BOOL_LABELS[k]}</span>
          <div class="seg small" role="group" aria-label={BOOL_LABELS[k]}>
            {#each TRI as t (t)}
              <button type="button" aria-pressed={flt.tri[k] === t} onclick={() => (flt.tri[k] = t)}>{t}</button>
            {/each}
          </div>
        </div>
        {#if errs[k]}<div class="err">{errs[k]}</div>{/if}
      {/each}
    </div>
  </details>

  <details>
    <summary>Found by{flt.query.length || flt.runId !== null ? ' •' : ''}</summary>
    <div class="stack">
      <div class="lbl">Search query</div>
      <TagPicker label="Found by query" options={queryOptions} bind:selected={flt.query} placeholder="Queries that found the repo…" />
      {#if errs.query}<div class="err">{errs.query}</div>{/if}
      <div class="lbl">Run</div>
      <select class="select" value={flt.runId === null ? '' : String(flt.runId)} onchange={(e) => onRun(e.currentTarget.value)} aria-label="Run">
        <option value="">Any run</option>
        {#each lists.runs as r (r.id)}
          <option value={String(r.id)}>{runLabel(r)}</option>
        {/each}
      </select>
      {#if errs.run_id}<div class="err">{errs.run_id}</div>{/if}
    </div>
  </details>
</div>

<style>
  .filters {
    display: flex;
    flex-direction: column;
  }
  details {
    border-bottom: 1px solid var(--border);
    padding: 6px 12px 8px;
  }
  summary {
    cursor: pointer;
    font-weight: 600;
    color: var(--text-2);
    padding: 3px 0;
  }
  details[open] > summary {
    margin-bottom: 6px;
  }
  .grid2 {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr);
    gap: 5px 6px;
    align-items: center;
  }
  .grid2 label {
    color: var(--text-2);
    font-size: var(--fs-sm);
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .lbl {
    color: var(--text-3);
    font-size: var(--fs-sm);
  }
  .row.between {
    justify-content: space-between;
  }
  .pct {
    width: 64px;
  }
  .err {
    color: var(--danger);
    font-size: var(--fs-sm);
  }
  .span {
    grid-column: 1 / -1;
  }
  .seg.small button {
    height: 22px;
    padding: 0 8px;
    font-size: var(--fs-sm);
    text-transform: capitalize;
  }
</style>
