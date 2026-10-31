<script lang="ts">
  import { cfg, errorsFor } from '../lib/app.svelte.ts'
  import type { FilterMeta, FilterValue } from '../lib/protocol.ts'
  import Field from './Field.svelte'

  const MATCH_FIELDS = ['name', 'description', 'topics', 'readme']

  const placeholders: Record<string, string> = {
    list: 'comma separated',
    range: 'e.g. 100+   >=100   10..50',
    date: 'e.g. 30d..   >=2024-01-01   2024-01-01..2024-06-30',
    raw: 'GitHub syntax, e.g. stars:>5',
  }

  /** groups in registry order, each with its filters */
  const groups = $derived.by(() => {
    const out: { name: string; items: FilterMeta[] }[] = []
    for (const f of cfg.meta?.filters ?? []) {
      let g = out.find((x) => x.name === f.group)
      if (!g) out.push((g = { name: f.group, items: [] }))
      g.items.push(f)
    }
    return out
  })

  function value(key: string): FilterValue | undefined {
    return cfg.draft?.filters[key]
  }
  function text(key: string): string {
    const v = value(key)
    return typeof v === 'string' ? v : ''
  }
  function set(key: string, v: FilterValue): void {
    if (cfg.draft) cfg.draft.filters[key] = v
  }
  function matchHas(key: string, field: string): boolean {
    const v = value(key)
    return Array.isArray(v) && v.includes(field)
  }
  function toggleMatch(key: string, field: string, on: boolean): void {
    const cur = value(key)
    const list = Array.isArray(cur) ? cur : []
    set(key, on ? [...list.filter((x) => x !== field), field] : list.filter((x) => x !== field))
  }
  function active(key: string): boolean {
    const v = value(key)
    return !(v === undefined || v === '' || v === false || (Array.isArray(v) && v.length === 0))
  }
  function activeCount(items: FilterMeta[]): number {
    return items.filter((f) => active(f.key)).length
  }
  function groupHasError(items: FilterMeta[]): boolean {
    return items.some((f) => errorsFor(`filters.${f.key}`).length > 0)
  }
  function clearAll(): void {
    if (cfg.draft) cfg.draft.filters = {}
  }
  const total = $derived(groups.reduce((n, g) => n + activeCount(g.items), 0))
</script>

<div class="filters">
  {#each errorsFor('filters') as e (e)}<div class="msg err">{e}</div>{/each}

  {#each groups as g (g.name)}
    <details open={['Scope', 'Numbers'].includes(g.name) || activeCount(g.items) > 0 || groupHasError(g.items)}>
      <summary>
        <span class="gname">{g.name}</span>
        {#if activeCount(g.items)}<span class="count">{activeCount(g.items)}</span>{/if}
        {#if groupHasError(g.items)}<span class="chip danger">check</span>{/if}
      </summary>
      <div class="grid">
        {#each g.items as f (f.key)}
          {@const errs = errorsFor(`filters.${f.key}`)}
          {#if f.kind === 'flag'}
            <Field label={f.label} hint={f.help} inline errors={errs}>
              <input
                type="checkbox"
                checked={value(f.key) === true}
                onchange={(e) => set(f.key, e.currentTarget.checked)}
                aria-label={f.label}
              />
            </Field>
          {:else if f.kind === 'match'}
            <div class="wide">
              <Field label={f.label} hint={f.help} errors={errs}>
                <div class="checks">
                  {#each MATCH_FIELDS as m (m)}
                    <label>
                      <input type="checkbox" checked={matchHas(f.key, m)} onchange={(e) => toggleMatch(f.key, m, e.currentTarget.checked)} />
                      {m}
                    </label>
                  {/each}
                </div>
              </Field>
            </div>
          {:else if f.kind === 'choice'}
            <Field label={f.label} hint={f.help} errors={errs}>
              <select class="select" value={text(f.key)} onchange={(e) => set(f.key, e.currentTarget.value)}>
                <option value="">Any</option>
                {#each f.choices as [val, qual] (val)}
                  <option value={val}>{val} ({qual})</option>
                {/each}
              </select>
            </Field>
          {:else}
            <div class:wide={f.kind === 'raw'}>
              <Field label={f.negate ? `${f.label}` : f.label} hint={f.help} errors={errs}>
                <input
                  class="input"
                  class:bad={errs.length > 0}
                  type="text"
                  spellcheck="false"
                  autocomplete="off"
                  placeholder={placeholders[f.kind] ?? ''}
                  value={text(f.key)}
                  oninput={(e) => set(f.key, e.currentTarget.value)}
                />
              </Field>
            </div>
          {/if}
        {/each}
      </div>
    </details>
  {/each}

  {#if total > 0}
    <div class="clear"><button class="btn ghost sm" onclick={clearAll}>Clear all filters ({total})</button></div>
  {/if}
</div>

<style>
  .filters {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  details {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }
  summary {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 10px;
    cursor: pointer;
    font-weight: 600;
    color: var(--text-2);
    list-style: none;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  summary::before {
    content: '▸';
    font-size: 10px;
    color: var(--text-3);
    width: 10px;
    transition: transform 0.1s;
  }
  details[open] > summary::before {
    transform: rotate(90deg);
  }
  .count {
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: 9px;
    background: var(--accent);
    color: var(--on-accent);
    font-size: var(--fs-xs);
    line-height: 18px;
    text-align: center;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 14px;
    padding: 8px 10px 12px;
  }
  .wide {
    grid-column: 1 / -1;
  }
  .checks {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
  }
  .checks label {
    display: inline-flex;
    gap: 5px;
    align-items: center;
  }
  .clear {
    display: flex;
    justify-content: flex-end;
  }
</style>
