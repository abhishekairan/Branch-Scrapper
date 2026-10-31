<script lang="ts">
  import type { Snippet } from 'svelte'

  interface Props {
    label: string
    hint?: string
    errors?: string[]
    warnings?: string[]
    /** put the control to the right of the label (checkboxes, small numbers) */
    inline?: boolean
    children: Snippet
  }
  let { label, hint = '', errors = [], warnings = [], inline = false, children }: Props = $props()
</script>

<div class="field" class:inline>
  <div class="label">
    <span>{label}</span>
    {#if hint && !inline}<span class="hint">{hint}</span>{/if}
  </div>
  <div class="control">{@render children()}</div>
  {#if inline && hint}<div class="hint under">{hint}</div>{/if}
  {#each errors as e (e)}<div class="note err">{e}</div>{/each}
  {#each warnings as w (w)}<div class="note warn">{w}</div>{/each}
</div>

<style>
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .field.inline {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    column-gap: 12px;
    row-gap: 2px;
  }
  .label {
    display: flex;
    align-items: baseline;
    gap: 8px;
    color: var(--text-2);
    font-weight: 500;
  }
  .hint {
    color: var(--text-3);
    font-weight: 400;
    font-size: var(--fs-sm);
  }
  .hint.under {
    grid-column: 1 / -1;
  }
  .note {
    grid-column: 1 / -1;
    font-size: var(--fs-sm);
  }
  .note.err {
    color: var(--danger);
  }
  .note.warn {
    color: var(--warn);
  }
</style>
