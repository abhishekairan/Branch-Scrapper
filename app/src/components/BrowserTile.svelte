<script lang="ts">
  import type { WorkerView } from '../lib/app.svelte.ts'
  import { formatBytes } from '../lib/format.ts'
  import { gridPrefs } from '../lib/view.svelte.ts'
  import BrowserCanvas from './BrowserCanvas.svelte'
  import Icon from './Icon.svelte'
  import WorkerChip from './WorkerChip.svelte'

  let { worker, onexpand }: { worker: WorkerView; onexpand: () => void } = $props()
</script>

<article class="tile {gridPrefs.density}" style:--shape={gridPrefs.shape === 'auto' ? null : gridPrefs.shape}>
  <header>
    <span class="wid mono">B{worker.index + 1}</span>
    <WorkerChip {worker} />
    <span class="grow"></span>
    {#if gridPrefs.showRam}
      <span class="ram num muted" title="This worker's share of its Chrome's memory">{formatBytes(worker.ram)}</span>
    {/if}
    <button class="btn ghost sm icon" title="Open in the large view" aria-label="Open in the large view" onclick={onexpand}>
      <Icon name="expand" size={14} />
    </button>
  </header>
  {#if gridPrefs.showStatus}
    <div class="status ellipsis" title={worker.text}>{worker.text}</div>
  {/if}
  <div class="stage"><BrowserCanvas index={worker.index} trackTile /></div>
  {#if gridPrefs.showUrl || worker.pages !== null}
    <footer class="muted">
      {#if gridPrefs.showUrl}
        <span class="grow ellipsis mono" title={worker.url ?? ''}>{worker.url ?? '—'}</span>
      {:else}
        <span class="grow"></span>
      {/if}
      {#if worker.pages !== null}<span class="num">{worker.pages} pages</span>{/if}
    </footer>
  {/if}
</article>

<style>
  /* density: tile padding + preview shape (the preview is always letterboxed, never cropped) */
  .tile {
    --pad-y: 8px;
    --pad-x: 10px;
    --tile-gap: 6px;
    --preview-ratio: 16 / 10;
    display: flex;
    flex-direction: column;
    gap: var(--tile-gap);
    padding: var(--pad-y) var(--pad-x);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    min-width: 0;
  }
  .tile.compact {
    --pad-y: 6px;
    --pad-x: 8px;
    --tile-gap: 4px;
    --preview-ratio: 16 / 9;
  }
  .tile.large {
    --pad-y: 12px;
    --pad-x: 14px;
    --tile-gap: 8px;
    --preview-ratio: 3 / 2;
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .wid {
    font-weight: 700;
    color: var(--text-2);
  }
  .icon {
    width: 24px;
    padding: 0;
  }
  .status {
    font-size: var(--fs-sm);
    color: var(--text-2);
    min-height: 18px;
  }
  .stage {
    position: relative;
    aspect-ratio: var(--shape, var(--preview-ratio));
    border-radius: var(--radius);
    overflow: hidden;
    border: 1px solid var(--border);
  }
  footer {
    display: flex;
    gap: 8px;
    font-size: var(--fs-xs);
    min-height: 16px;
  }
</style>
