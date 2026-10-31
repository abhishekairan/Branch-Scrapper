<script lang="ts">
  // Searchable multi-select: chips for the chosen values, a suggestion list (with counts) as you type.
  import { formatCount } from '../lib/format.ts'

  interface Props {
    options: { value: string; count: number }[]
    selected: string[]
    placeholder?: string
    /** allow Enter to add text that is not in `options` */
    free?: boolean
    label: string
  }
  let { options, selected = $bindable(), placeholder = 'Search…', free = false, label }: Props = $props()

  let q = $state('')
  let open = $state(false)
  let hi = $state(0)

  const matches = $derived.by(() => {
    const needle = q.trim().toLowerCase()
    const taken = new Set(selected.map((s) => s.toLowerCase()))
    const out: { value: string; count: number }[] = []
    for (const o of options) {
      if (taken.has(o.value.toLowerCase())) continue
      if (needle && !o.value.toLowerCase().includes(needle)) continue
      out.push(o)
      if (out.length >= 40) break
    }
    return out
  })

  function add(v: string): void {
    const t = v.trim()
    if (!t) return
    if (!selected.some((s) => s.toLowerCase() === t.toLowerCase())) selected = [...selected, t]
    q = ''
    hi = 0
  }
  function remove(v: string): void {
    selected = selected.filter((s) => s !== v)
  }
  function onKey(e: KeyboardEvent): void {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      open = true
      hi = Math.min(hi + 1, matches.length - 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      hi = Math.max(hi - 1, 0)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (matches[hi]) add(matches[hi].value)
      else if (free) add(q)
    } else if (e.key === 'Escape') {
      open = false
    } else if (e.key === 'Backspace' && !q && selected.length) {
      selected = selected.slice(0, -1)
    }
  }
</script>

<div class="picker">
  {#if selected.length}
    <div class="tags">
      {#each selected as s (s)}
        <span class="tag">{s}<button type="button" aria-label="Remove {s}" onclick={() => remove(s)}>×</button></span>
      {/each}
    </div>
  {/if}
  <input
    class="input"
    type="text"
    spellcheck="false"
    autocomplete="off"
    aria-label={label}
    {placeholder}
    bind:value={q}
    onfocus={() => (open = true)}
    onblur={() => (open = false)}
    oninput={() => {
      open = true
      hi = 0
    }}
    onkeydown={onKey}
  />
  {#if open && matches.length}
    <ul class="list" role="listbox">
      {#each matches as m, i (m.value)}
        <li role="option" aria-selected={i === hi} class:hi={i === hi}>
          <button type="button" tabindex="-1" onmousedown={(e) => e.preventDefault()} onclick={() => add(m.value)}>
            <span class="ellipsis">{m.value}</span>
            {#if m.count > 0}<span class="count num">{formatCount(m.count)}</span>{/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .picker {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .tag {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    max-width: 100%;
    height: 20px;
    padding: 0 2px 0 8px;
    border-radius: 10px;
    background: var(--accent-weak);
    color: var(--accent);
    font-size: var(--fs-xs);
    font-weight: 600;
    white-space: nowrap;
  }
  .tag button {
    border: 0;
    background: transparent;
    color: inherit;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    line-height: 1;
    padding: 0;
  }
  .tag button:hover {
    background: color-mix(in srgb, var(--accent) 20%, transparent);
  }
  .list {
    position: absolute;
    z-index: 20;
    top: 100%;
    left: 0;
    right: 0;
    margin: 2px 0 0;
    padding: 3px;
    list-style: none;
    max-height: 200px;
    overflow: auto;
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.18);
  }
  .list li button {
    width: 100%;
    display: flex;
    justify-content: space-between;
    gap: 8px;
    padding: 3px 6px;
    border: 0;
    border-radius: var(--radius-sm);
    background: transparent;
    text-align: left;
  }
  .list li.hi button,
  .list li button:hover {
    background: var(--accent-weak);
  }
  .count {
    color: var(--text-3);
    font-size: var(--fs-xs);
  }
</style>
