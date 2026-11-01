<script lang="ts">
  import { conn, restartBackend } from '../lib/app.svelte.ts'
  import { inTauri } from '../lib/tauri.ts'
  import { upd } from '../lib/updates.svelte.ts'

  // while an update installs, the backend is shut down on purpose: that is not an error to report
  const show = $derived(conn.state !== 'open' && upd.phase === 'idle')
</script>

{#if show}
  <div class="banner" role="status">
    <div class="grow">
      {#if conn.sidecarDown}
        <strong>The Python backend is not running.</strong>
      {:else if conn.state === 'connecting'}
        <strong>Starting the Python backend…</strong>
      {:else}
        <strong>Lost the connection to the backend. Trying again…</strong>
      {/if}
      {#if conn.error}<span class="detail"> {conn.error}</span>{/if}
    </div>
    {#if inTauri && (conn.sidecarDown || conn.state === 'reconnecting')}
      <button class="btn sm" disabled={conn.restarting} onclick={restartBackend}>
        {conn.restarting ? 'Restarting…' : 'Restart backend'}
      </button>
    {/if}
  </div>
{/if}

<style>
  .banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 14px;
    background: var(--warn-weak);
    color: var(--warn);
    border-bottom: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
    font-size: var(--fs-sm);
  }
  .detail {
    opacity: 0.85;
  }
</style>
