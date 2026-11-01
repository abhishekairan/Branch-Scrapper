<script lang="ts">
  import {
    DEFAULT_GRID_PREFS,
    gridPrefs,
    resetGridPrefs,
    setGridPrefs,
    type GridColumns,
    type GridDensity,
  } from '../lib/view.svelte.ts'

  let open = $state(false)
  let root: HTMLDivElement | undefined = $state()
  let trigger: HTMLButtonElement | undefined = $state()

  const columns: { id: GridColumns; label: string }[] = [
    { id: 0, label: 'Auto' },
    { id: 1, label: '1' },
    { id: 2, label: '2' },
    { id: 3, label: '3' },
    { id: 4, label: '4' },
  ]
  const densities: { id: GridDensity; label: string }[] = [
    { id: 'compact', label: 'Compact' },
    { id: 'comfortable', label: 'Comfortable' },
    { id: 'large', label: 'Large' },
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
      <div class="checks">
        <label><input type="checkbox" checked={gridPrefs.showUrl} onchange={(e) => setGridPrefs({ showUrl: e.currentTarget.checked })} /> Show page URL</label>
        <label><input type="checkbox" checked={gridPrefs.showStatus} onchange={(e) => setGridPrefs({ showStatus: e.currentTarget.checked })} /> Show status caption</label>
        <label><input type="checkbox" checked={gridPrefs.showRam} onchange={(e) => setGridPrefs({ showRam: e.currentTarget.checked })} /> Show RAM</label>
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
    width: 300px;
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
