<script lang="ts">
  import { run } from '../lib/app.svelte.ts'
  import { formatBytes } from '../lib/format.ts'
  import { dismissUpdate, installNow, upd } from '../lib/updates.svelte.ts'

  const busy = $derived(upd.phase !== 'idle')
  // an update that was found is offered until "Later"; an install that is running or failed stays visible
  const show = $derived(
    upd.supported && ((upd.available !== null && !upd.dismissed) || busy || upd.installError !== ''),
  )
  const pct = $derived(
    upd.total && upd.total > 0 ? Math.min(100, Math.round((upd.downloaded / upd.total) * 100)) : null,
  )
  const progressText = $derived(
    `${formatBytes(upd.downloaded)}${upd.total ? ` of ${formatBytes(upd.total)}` : ''}${pct !== null ? ` (${pct}%)` : ''}`,
  )
  const runBlocks = $derived(run.active)
</script>

{#if show}
  <div class="banner" role="status" aria-live="polite">
    <div class="grow">
      {#if upd.phase === 'downloading'}
        <strong>Downloading Branch Scrapper {upd.available?.version}…</strong>
        <span class="detail num">{progressText}</span>
      {:else if upd.phase === 'stopping'}
        <strong>Closing the backend before installing…</strong>
      {:else if upd.phase === 'installing'}
        <strong>Starting the installer…</strong>
        <span class="detail">Branch Scrapper restarts by itself when it is done. Your data is kept.</span>
      {:else if upd.installError}
        <strong>The update was not installed.</strong>
        <span class="detail">{upd.installError}</span>
      {:else if upd.available}
        <strong>Branch Scrapper {upd.available.version} is available.</strong>
        {#if runBlocks}<span class="detail">Stop the current run to install it.</span>{/if}
      {/if}
    </div>

    {#if upd.phase === 'downloading'}
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct ?? 0}>
        <div class="fill" class:indeterminate={pct === null} style:width={pct === null ? undefined : `${pct}%`}></div>
      </div>
    {:else if !busy}
      {#if upd.available}
        <button class="btn sm ghost" onclick={() => (upd.notesOpen = true)}>What's new</button>
        <button
          class="btn sm primary"
          disabled={runBlocks}
          title={runBlocks ? 'A run is active. Stop it first, then install.' : undefined}
          onclick={() => void installNow()}
        >
          {upd.installError ? 'Try again' : 'Install & restart'}
        </button>
      {/if}
      <button class="btn sm ghost" onclick={dismissUpdate}>{upd.installError ? 'Dismiss' : 'Later'}</button>
    {/if}
  </div>
{/if}

<style>
  .banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 14px;
    background: var(--info-weak);
    color: var(--info);
    border-bottom: 1px solid color-mix(in srgb, var(--info) 30%, transparent);
    font-size: var(--fs-sm);
  }
  .detail {
    opacity: 0.85;
    margin-left: 6px;
  }
  .progress {
    width: 180px;
    height: 6px;
    border-radius: 3px;
    background: var(--sunken);
    overflow: hidden;
    flex: none;
  }
  .fill {
    height: 100%;
    background: var(--info);
    transition: width 0.2s ease;
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
