<script lang="ts">
  import { parseNotes } from '../lib/notes.ts'
  import { upd } from '../lib/updates.svelte.ts'

  const blocks = $derived(parseNotes(upd.available?.body))
  let closeBtn: HTMLButtonElement | undefined = $state()

  function close(): void {
    upd.notesOpen = false
  }

  $effect(() => {
    if (upd.notesOpen) closeBtn?.focus()
  })
</script>

<svelte:window onkeydown={(e) => upd.notesOpen && e.key === 'Escape' && close()} />

{#if upd.notesOpen && upd.available}
  <div class="scrim" role="presentation" onpointerdown={(e) => e.target === e.currentTarget && close()}>
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="notes-title">
      <header>
        <h2 id="notes-title">What's new in {upd.available.version}</h2>
        <span class="muted">you have {upd.available.currentVersion}</span>
      </header>
      <div class="notes selectable">
        {#each blocks as b, i (i)}
          {#if b.kind === 'h'}
            <h3>{b.text}</h3>
          {:else if b.kind === 'li'}
            <p class="li">{b.text}</p>
          {:else}
            <p>{b.text}</p>
          {/if}
        {:else}
          <p class="muted">This release has no notes.</p>
        {/each}
      </div>
      <footer>
        <button class="btn" bind:this={closeBtn} onclick={close}>Close</button>
      </footer>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    background: var(--overlay);
  }
  .dialog {
    width: min(560px, calc(100vw - 48px));
    max-height: calc(100vh - 96px);
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-lg);
    box-shadow: 0 12px 40px rgb(0 0 0 / 0.3);
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 14px 16px 8px;
  }
  h2 {
    margin: 0;
    font-size: var(--fs-lg);
  }
  .notes {
    padding: 4px 16px 12px;
    overflow-y: auto;
    font-size: var(--fs);
    line-height: 1.5;
  }
  .notes h3 {
    margin: 12px 0 4px;
    font-size: var(--fs);
  }
  .notes p {
    margin: 4px 0;
    overflow-wrap: anywhere;
  }
  .notes p.li {
    padding-left: 14px;
    text-indent: -10px;
  }
  .notes p.li::before {
    content: '• ';
  }
  footer {
    display: flex;
    justify-content: flex-end;
    padding: 10px 16px 14px;
    border-top: 1px solid var(--border);
  }
</style>
