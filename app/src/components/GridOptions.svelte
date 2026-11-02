<script lang="ts">
  import {
    DEFAULT_GRID_PREFS,
    GRID_COLUMNS,
    gridPrefs,
    resetGridPrefs,
    setGridPrefs,
    type GridColumns,
    type GridDensity,
    type GridShape,
    type GridSpacing,
  } from '../lib/view.svelte.ts'

  let open = $state(false)
  let root: HTMLDivElement | undefined = $state()
  let trigger: HTMLButtonElement | undefined = $state()

  const columns: { id: GridColumns; label: string }[] = GRID_COLUMNS.map((n) => ({ id: n, label: n === 0 ? 'Auto' : String(n) }))
  const densities: { id: GridDensity; label: string }[] = [
    { id: 'compact', label: 'Compact' },
    { id: 'comfortable', label: 'Comfortable' },
    { id: 'large', label: 'Large' },
  ]
  const spacings: { id: GridSpacing; label: string }[] = [
    { id: 'tight', label: 'Tight' },
    { id: 'normal', label: 'Normal' },
    { id: 'roomy', label: 'Roomy' },
  ]
  const shapes: { id: GridShape; label: string; title: string }[] = [
    { id: 'auto', label: 'Auto', title: 'Follows the tile size' },
    { id: '16/9', label: '16:9', title: 'Widescreen' },
    { id: '16/10', label: '16:10', title: 'Laptop screen' },
    { id: '4/3', label: '4:3', title: 'Taller preview' },
  ]

  const changed = $derived(
    (Object.keys(DEFAULT_GRID_PREFS) as (keyof typeof DEFAULT_GRID_PREFS)[]).some(
      (k) => gridPrefs[k] !== DEFAULT_GRID_PREFS[k],
    ),
  )

  function onWindowPointer(e: PointerEvent): void {
    if (open && root && !root.contains(e.target as Node)) open = false
  }
  function onWindowKey(e: KeyboardEvent): void {
    if (open && e.key === 'Escape') {
      open = false
      trigger?.focus()
    }
  }
</script>

<svelte:window onpointerdown={onWindowPointer} onkeydown={onWindowKey} />

<div class="go" bind:this={root}>
  <button
    class="btn"
    class:on={open}
    bind:this={trigger}
    aria-haspopup="dialog"
    aria-expanded={open}
    title="Grid options"
    onclick={() => (open = !open)}
  >
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true">
      <path d="M2.5 4.5h6M11.5 4.5h2M2.5 11.5h2M7.5 11.5h6" /><circle cx="10" cy="4.5" r="1.6" /><circle cx="6" cy="11.5" r="1.6" />
    </svg>
    Grid options
  </button>

  {#if open}
    <div class="pop card" role="dialog" aria-label="Grid options">
      <div class="opt">
        <span class="lbl" id="go-cols">Columns</span>
        <div class="seg" role="group" aria-labelledby="go-cols">
          {#each columns as c (c.id)}
            <button aria-pressed={gridPrefs.columns === c.id} onclick={() => setGridPrefs({ columns: c.id })}>{c.label}</button>
          {/each}
        </div>
      </div>
      <div class="opt">
        <span class="lbl" id="go-size">Tile size</span>
        <div class="seg" role="group" aria-labelledby="go-size">
          {#each densities as d (d.id)}
            <button aria-pressed={gridPrefs.density === d.id} onclick={() => setGridPrefs({ density: d.id })}>{d.label}</button>
          {/each}
        </div>
      </div>
      <div class="opt">
        <span class="lbl" id="go-gap">Spacing</span>
        <div class="seg" role="group" aria-labelledby="go-gap">
          {#each spacings as s (s.id)}
            <button aria-pressed={gridPrefs.spacing === s.id} onclick={() => setGridPrefs({ spacing: s.id })}>{s.label}</button>
          {/each}
        </div>
      </div>
      <div class="opt">
        <span class="lbl" id="go-shape">Preview shape</span>
        <div class="seg" role="group" aria-labelledby="go-shape">
          {#each shapes as s (s.id)}
            <button aria-pressed={gridPrefs.shape === s.id} title={s.title} onclick={() => setGridPrefs({ shape: s.id })}>{s.label}</button>
          {/each}
        </div>
      </div>
      <div class="checks">
        <label><input type="checkbox" checked={gridPrefs.showUrl} onchange={(e) => setGridPrefs({ showUrl: e.currentTarget.checked })} /> Show page URL</label>
        <label><input type="checkbox" checked={gridPrefs.showStatus} onchange={(e) => setGridPrefs({ showStatus: e.currentTarget.checked })} /> Show status caption</label>
        <label><input type="checkbox" checked={gridPrefs.showRam} onchange={(e) => setGridPrefs({ showRam: e.currentTarget.checked })} /> Show RAM</label>
        <label><input type="checkbox" checked={gridPrefs.hideFinished} onchange={(e) => setGridPrefs({ hideFinished: e.currentTarget.checked })} /> Hide finished browsers</label>
      </div>
      <div class="foot">
        <span class="muted hint">Saved on this computer.</span>
        <button class="btn ghost sm" disabled={!changed} onclick={resetGridPrefs}>Reset</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .go {
    position: relative;
  }
  .btn.on {
    background: var(--accent-weak);
    border-color: var(--accent);
    color: var(--accent);
  }
  .pop {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    z-index: 20;
    width: 320px;
    max-height: calc(100vh - 140px);
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    box-shadow: 0 8px 24px rgb(0 0 0 / 0.18);
  }
  .opt {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 5px;
  }
  .lbl {
    font-size: var(--fs-sm);
    color: var(--text-3);
  }
  .seg {
    display: flex;
  }
  .seg button {
    flex: 1;
    padding: 0 6px;
  }
  .checks {
    display: flex;
    flex-direction: column;
    gap: 7px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .checks label {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
  }
  .foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .hint {
    font-size: var(--fs-xs);
  }
</style>
