<script lang="ts">
  import { clock, conn, run, totalRam } from '../lib/app.svelte.ts'
  import { formatBytes, formatCount, formatDuration } from '../lib/format.ts'

  const connLabel = $derived(
    conn.sidecarDown
      ? 'Backend stopped'
      : conn.state === 'open'
        ? 'Backend connected'
        : conn.state === 'reconnecting'
          ? 'Reconnecting…'
          : conn.state === 'closed'
            ? 'Disconnected'
            : 'Connecting…',
  )
  const connTone = $derived(
    conn.sidecarDown || conn.state === 'closed' ? 'danger' : conn.state === 'open' ? 'ok' : 'warn',
  )

  const runLabel = $derived(
    run.active
      ? run.stopping
        ? 'Stopping…'
        : run.kind === 'preview'
          ? 'Previewing counts'
          : 'Running'
      : run.finished
        ? run.finished.status === 'done'
          ? 'Finished'
          : run.finished.status === 'stopped'
            ? 'Stopped'
            : 'Failed'
        : 'Idle',
  )
  const runTone = $derived(
    run.active
      ? 'accent'
      : run.finished?.status === 'failed'
        ? 'danger'
        : run.finished?.status === 'done'
          ? 'ok'
          : run.finished?.status === 'stopped'
            ? 'warn'
            : '',
  )

  const pct = $derived.by(() => {
    const p = run.progress
    if (!p || !p.repos_total) return null
    return Math.min(100, Math.round((p.repos_done / p.repos_total) * 100))
  })
  const elapsed = $derived(run.active ? (clock.now - run.startedAt) / 1000 : (run.finished?.counts.elapsed ?? null))
  const ram = $derived(totalRam())
</script>

<header>
  <span class="chip {connTone}" title={conn.error || undefined}>{connLabel}</span>

  <span class="sep"></span>

  <span class="chip {runTone}">{runLabel}</span>
  {#if run.active || run.finished}
    {#if run.progress || run.finished}
      {@const p = run.progress}
      <div class="progress" role="progressbar" aria-valuenow={pct ?? 0} aria-valuemin="0" aria-valuemax="100">
        <div class="fill" class:indeterminate={run.active && pct === null} style:width={pct === null ? undefined : `${pct}%`}></div>
      </div>
      <span class="num txt">
        {#if p}
          {formatCount(p.repos_done)}<span class="muted"> / {formatCount(p.repos_total)} repos</span>
          <span class="muted"> · {p.queries_done}/{p.queries_total} queries</span>
          {#if run.active && p.eta_seconds !== null}<span class="muted"> · ETA {formatDuration(p.eta_seconds)}</span>{/if}
        {:else if run.finished}
          {formatCount(run.finished.counts.repos)}<span class="muted"> repos</span>
        {/if}
        {#if elapsed !== null}<span class="muted"> · {formatDuration(elapsed)}</span>{/if}
      </span>
    {:else}
      <span class="muted txt">starting browsers…</span>
    {/if}
  {/if}

  <span class="grow"></span>

  {#if run.pool}
    <span class="num txt" title="Sum over every Chrome process of this run (private memory when the OS reports it)">
      Chrome: {run.pool.chrome_count}
      {run.pool.chrome_count === 1 ? 'instance' : 'instances'}
      <span class="muted">· {run.pool.process_count} processes ·</span>
      <strong>{formatBytes(ram)}</strong>
    </span>
  {:else}
    <span class="muted txt">Chrome: not running</span>
  {/if}
</header>

<style>
  header {
    grid-area: bar;
    display: flex;
    align-items: center;
    gap: 10px;
    height: var(--bar-h);
    padding: 0 14px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    min-width: 0;
  }
  .sep {
    width: 1px;
    height: 18px;
    background: var(--border);
    margin: 0 2px;
  }
  .txt {
    font-size: var(--fs-sm);
    white-space: nowrap;
  }
  .progress {
    width: 160px;
    height: 6px;
    border-radius: 3px;
    background: var(--sunken);
    overflow: hidden;
    flex: none;
  }
  .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s ease;
  }
  .fill.indeterminate {
    width: 30%;
    animation: slide 1.4s ease-in-out infinite;
  }
  @keyframes slide {
    from {
      margin-left: -30%;
    }
    to {
      margin-left: 100%;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .fill.indeterminate {
      animation: none;
      width: 100%;
      opacity: 0.4;
    }
  }
</style>
