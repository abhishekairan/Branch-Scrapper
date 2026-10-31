<script lang="ts">
  import { tick } from 'svelte'
  import { clearLogs, LOG_CAP, logs } from '../lib/app.svelte.ts'
  import type { LogLevel } from '../lib/protocol.ts'

  let box: HTMLDivElement | undefined = $state()
  let follow = $state(true)
  let filter = $state<'all' | LogLevel>('all')

  const RANK: Record<LogLevel, number> = { debug: 0, info: 1, warning: 2, error: 3 }

  // At most this many rows are in the DOM; the buffer itself is capped at LOG_CAP.
  const SHOWN = 600
  const lines = $derived.by(() => {
    const all = filter === 'all' ? logs.lines : logs.lines.filter((l) => RANK[l.level] >= RANK[filter as LogLevel])
    return all.length > SHOWN ? all.slice(all.length - SHOWN) : all
  })

  function time(t: number): string {
    const d = new Date(t)
    return d.toLocaleTimeString([], { hour12: false })
  }

  $effect(() => {
    void lines // follow the newest line
    if (follow && box) {
      const el = box
      void tick().then(() => (el.scrollTop = el.scrollHeight))
    }
  })

  function onScroll(): void {
    if (!box) return
    follow = box.scrollHeight - box.scrollTop - box.clientHeight < 24
  }
</script>

<div class="pane">
  <div class="tools">
    <span class="title">Log</span>
    <span class="muted count">{logs.lines.length >= LOG_CAP ? `last ${LOG_CAP}` : logs.lines.length} lines</span>
    <span class="grow"></span>
    <select class="select sm" bind:value={filter} aria-label="Minimum level">
      <option value="all">All levels</option>
      <option value="info">Info and up</option>
      <option value="warning">Warnings and errors</option>
      <option value="error">Errors only</option>
    </select>
    <button class="btn ghost sm" onclick={clearLogs}>Clear</button>
  </div>
  <div class="lines mono selectable" bind:this={box} onscroll={onScroll} role="log" aria-live="off">
    {#each lines as l (l.id)}
      <div class="line {l.level}"><span class="t">{time(l.t)}</span><span class="x">{l.text}</span></div>
    {:else}
      <div class="empty muted">Nothing logged yet. Events from a run appear here.</div>
    {/each}
  </div>
  {#if !follow}
    <button
      class="btn sm jump"
      onclick={() => {
        follow = true
        if (box) box.scrollTop = box.scrollHeight
      }}>Jump to newest</button
    >
  {/if}
</div>

<style>
  .pane {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    overflow: hidden;
  }
  .tools {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
  }
  .title {
    font-weight: 600;
  }
  .count {
    font-size: var(--fs-sm);
  }
  .select.sm {
    width: auto;
    min-height: 24px;
    padding: 0 6px;
    font-size: var(--fs-sm);
  }
  .lines {
    flex: 1;
    overflow: auto;
    background: var(--log-bg);
    padding: 4px 0;
  }
  .line {
    display: flex;
    gap: 10px;
    padding: 1px 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .line:hover {
    background: var(--sunken);
  }
  .t {
    color: var(--text-3);
    flex: none;
  }
  .line.debug .x {
    color: var(--text-3);
  }
  .line.info .x {
    color: var(--text);
  }
  .line.warning .x {
    color: var(--warn);
  }
  .line.error .x {
    color: var(--danger);
  }
  .empty {
    padding: 14px;
    font-family: var(--font);
  }
  .jump {
    position: absolute;
    right: 14px;
    bottom: 12px;
    box-shadow: var(--shadow);
  }
</style>
