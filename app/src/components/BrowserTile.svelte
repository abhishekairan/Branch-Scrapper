<script lang="ts">
  import type { WorkerView } from '../lib/app.svelte.ts'
  import { formatBytes } from '../lib/format.ts'
  import BrowserCanvas from './BrowserCanvas.svelte'
  import Icon from './Icon.svelte'
  import WorkerChip from './WorkerChip.svelte'

  let { worker, onexpand }: { worker: WorkerView; onexpand: () => void } = $props()
</script>

<article class="tile">
  <header>
    <span class="wid mono">B{worker.index + 1}</span>
    <WorkerChip {worker} />
    <span class="grow"></span>
    <span class="ram num muted" title="This worker's share of its Chrome's memory">{formatBytes(worker.ram)}</span>
    <button class="btn ghost sm icon" title="Open in the large view" aria-label="Open in the large view" onclick={onexpand}>
      <Icon name="expand" size={14} />
    </button>
  </header>
  <div class="status ellipsis" title={worker.text}>{worker.text}</div>
  <div class="stage"><BrowserCanvas index={worker.index} /></div>
  <footer class="muted">
    <span class="grow ellipsis mono" title={worker.url ?? ''}>{worker.url ?? '—'}</span>
    {#if worker.pages !== null}<span class="num">{worker.pages} pages</span>{/if}
  </footer>
</article>

<style>
  .tile {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 10px 8px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    min-width: 0;
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
    aspect-ratio: 16 / 10;
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
