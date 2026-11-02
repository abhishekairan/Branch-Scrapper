<script lang="ts">
  // Virtualised results grid: fixed row height, only the visible rows (+ overscan) exist in the DOM, pages are
  // fetched through `grid.ensure` as the user scrolls. All state lives in lib/data.svelte.ts.
  import {
    COLUMN_BY_KEY,
    exactTime,
    languageEntries,
    nextSort,
    relTime,
    stringList,
  } from '../lib/datafilters.ts'
  import type { ColumnDef } from '../lib/datafilters.ts'
  import {
    colWidth,
    rowHeight,
    flt,
    grid,
    moveActive,
    openRow,
    persistUi,
    scheduleSearch,
    sel,
    selectAllMatching,
    selectRange,
    ui,
  } from '../lib/data.svelte.ts'
  import { formatCount } from '../lib/format.ts'

  const HEAD_H = 32
  const CHECK_W = 38
  const OVERSCAN = 8
  const MAX_CHIPS = 12

  let scroller = $state<HTMLDivElement | null>(null)
  let scrollTop = $state(0)
  let viewH = $state(600)
  const now = Date.now()

  const ROW_H = $derived(rowHeight())
  const cols = $derived(ui.columns.map((k) => COLUMN_BY_KEY[k]).filter((c): c is ColumnDef => !!c))
  const totalW = $derived(CHECK_W + cols.reduce((n, c) => n + colWidth(c.key), 0))
  const total = $derived(grid.total ?? 0)
  const first = $derived(Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN))
  const last = $derived(Math.min(total - 1, Math.ceil((scrollTop + viewH - HEAD_H) / ROW_H) + OVERSCAN))
  const indices = $derived(last >= first ? Array.from({ length: last - first + 1 }, (_, i) => first + i) : [])

  $effect(() => {
    void grid.resets
    void grid.total
    grid.ensure(first, Math.max(first, last))
  })

  $effect(() => {
    void grid.resets
    if (scroller) scroller.scrollTop = 0
  })

  // keep the keyboard cursor visible
  $effect(() => {
    const i = sel.active
    if (i < 0 || !scroller) return
    const body = scroller.clientHeight - HEAD_H
    const top = i * ROW_H
    if (top < scroller.scrollTop) scroller.scrollTop = top
    else if (top + ROW_H > scroller.scrollTop + body) scroller.scrollTop = top + ROW_H - body
  })

  const allState = $derived.by(() => {
    const n = sel.names.size
    if (n === 0) return 'none'
    return grid.total !== null && n >= grid.total ? 'all' : 'some'
  })

  function onHeaderCheck(): void {
    if (sel.names.size > 0) sel.clear()
    else void selectAllMatching()
  }

  function sortOf(key: string): { dir: 'asc' | 'desc'; n: number } | null {
    const i = ui.sort.findIndex((s) => s[0] === key)
    if (!ui.sortTouched || i < 0) return null
    return { dir: ui.sort[i][1], n: i + 1 }
  }

  function defaultSortOf(key: string): 'asc' | 'desc' | null {
    return !ui.sortTouched && key === 'stars' && !flt.text.trim() ? 'desc' : null
  }

  function clickHeader(c: ColumnDef, e: MouseEvent): void {
    if (!c.sortable) return
    const base = ui.sortTouched ? ($state.snapshot(ui.sort) as [string, 'asc' | 'desc'][]) : ([['stars', 'desc']] as [string, 'asc' | 'desc'][])
    ui.sort = nextSort(base, c.key, e.shiftKey)
    ui.sortTouched = true
    scheduleSearch(true)
  }

  // ------------------------------------------------------------ resizing
  let drag: { key: string; x: number; w: number } | null = null
  function startResize(c: ColumnDef, e: PointerEvent): void {
    e.stopPropagation()
    e.preventDefault()
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    drag = { key: c.key, x: e.clientX, w: colWidth(c.key) }
  }
  function moveResize(e: PointerEvent): void {
    if (!drag) return
    ui.widths[drag.key] = Math.max(48, Math.round(drag.w + e.clientX - drag.x))
  }
  function endResize(): void {
    if (drag) persistUi()
    drag = null
  }

  // ------------------------------------------------------------ rows
  function rowClick(i: number, e: MouseEvent): void {
    if (e.shiftKey) selectRange(i)
    else if (e.ctrlKey || e.metaKey) {
      const name = grid.rowAt(i)?.full_name
      if (typeof name === 'string') sel.toggle(name)
      sel.anchor = i
    } else openRow(i)
  }

  function checkClick(i: number, name: string, e: MouseEvent): void {
    e.stopPropagation()
    if (e.shiftKey) selectRange(i)
    else {
      sel.toggle(name)
      sel.anchor = i
    }
  }

  function onKey(e: KeyboardEvent): void {
    if (e.target instanceof HTMLInputElement) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      moveActive(1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      moveActive(-1)
    } else if (e.key === 'PageDown') {
      e.preventDefault()
      moveActive(Math.max(1, Math.floor((viewH - HEAD_H) / ROW_H) - 1))
    } else if (e.key === 'PageUp') {
      e.preventDefault()
      moveActive(-Math.max(1, Math.floor((viewH - HEAD_H) / ROW_H) - 1))
    } else if (e.key === ' ' && sel.active >= 0) {
      e.preventDefault()
      const name = grid.rowAt(sel.active)?.full_name
      if (typeof name === 'string') sel.toggle(name)
    }
  }

  function show(v: unknown): string {
    return v === null || v === undefined || v === '' ? '' : String(v)
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="scroller"
  bind:this={scroller}
  bind:clientHeight={viewH}
  onscroll={(e) => (scrollTop = e.currentTarget.scrollTop)}
  onkeydown={onKey}
  tabindex="0"
  role="grid"
  aria-label="Results"
  aria-rowcount={total}
  class:striped={ui.table.striped}
  class:lines={ui.table.colLines}
  class:frozen={ui.table.freezeName}
  class:tall={ui.table.density === 'tall'}
  class:compact={ui.table.density === 'compact'}
>
  <div class="head" style:width="{totalW}px" role="row">
    <div class="cell check" style:width="{CHECK_W}px" role="columnheader">
      <input
        type="checkbox"
        aria-label="Select all matching rows"
        title="Select all rows matching the filters"
        checked={allState === 'all'}
        indeterminate={allState === 'some'}
        disabled={!total || sel.busy}
        onchange={onHeaderCheck}
      />
    </div>
    {#each cols as c (c.key)}
      {@const s = sortOf(c.key)}
      <div
        class="cell hcell"
        class:namecol={c.key === 'full_name'}
        class:num={c.kind === 'num'}
        class:sortable={c.sortable}
        style:width="{colWidth(c.key)}px"
        role="columnheader"
        aria-sort={s ? (s.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
        title={c.sortable ? 'Click to sort, Shift-click to add a secondary sort' : c.label}
        onclick={(e) => clickHeader(c, e)}
        onkeydown={(e) => {
          if (e.key === 'Enter') clickHeader(c, e as unknown as MouseEvent)
        }}
        tabindex={c.sortable ? 0 : -1}
      >
        <span class="ellipsis">{c.label}</span>
        {#if s}
          <span class="arrow" class:on={true}>{s.dir === 'asc' ? '▲' : '▼'}{ui.sort.length > 1 ? s.n : ''}</span>
        {:else if defaultSortOf(c.key)}
          <span class="arrow dim">▼</span>
        {/if}
        <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
        <span
          class="grip"
          onpointerdown={(e) => startResize(c, e)}
          onpointermove={moveResize}
          onpointerup={endResize}
          onpointercancel={endResize}
          onclick={(e) => e.stopPropagation()}
        ></span>
      </div>
    {/each}
  </div>

  <div class="body" style:height="{total * ROW_H}px" style:width="{totalW}px">
    {#each indices as i (i)}
      {@const row = grid.rowAt(i)}
      {@const name = row ? String(row.full_name) : ''}
      <!-- keyboard handling is on the grid itself (arrow keys / space), rows are not separate tab stops -->
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_interactive_supports_focus -->
      <div
        class="rrow"
        class:odd={i % 2 === 1}
        class:picked={!!row && sel.has(name)}
        class:active={sel.active === i}
        class:loading={!row}
        style:top="{i * ROW_H}px"
        style:height="{ROW_H}px"
        role="row"
        aria-rowindex={i + 1}
        aria-selected={sel.active === i}
        onclick={(e) => row && rowClick(i, e)}
      >
        <div class="cell check" style:width="{CHECK_W}px" role="gridcell">
          {#if row}
            <input
              type="checkbox"
              aria-label="Select {name}"
              checked={sel.has(name)}
              onclick={(e) => checkClick(i, name, e)}
            />
          {/if}
        </div>
        {#each cols as c (c.key)}
          <div
            class="cell"
            class:namecol={c.key === 'full_name'}
            class:textcell={c.kind === 'text'}
            class:num={c.kind === 'num'}
            class:center={c.kind === 'bool'}
            style:width="{colWidth(c.key)}px"
            role="gridcell"
          >
            {#if !row}
              {#if c.key === 'full_name'}<span class="skel"></span>{/if}
            {:else if c.kind === 'num'}
              <span class="num ellipsis">{formatCount(row[c.key] as number | null | undefined)}</span>
            {:else if c.kind === 'list'}
              {@const items = stringList(row[c.key])}
              <span class="chips">
                {#each items.slice(0, MAX_CHIPS) as t (t)}<span class="tag">{t}</span>{/each}
                {#if items.length > MAX_CHIPS}<span class="tag more">+{items.length - MAX_CHIPS}</span>{/if}
              </span>
            {:else if c.kind === 'langs'}
              {@const ls = languageEntries(row[c.key])}
              <span class="chips">
                {#each ls.slice(0, 4) as [n, p] (n)}<span class="tag">{n} {Math.round(p)}%</span>{/each}
              </span>
            {:else if c.kind === 'date'}
              <span class="ellipsis" title={exactTime(row[c.key])}>{relTime(row[c.key], now)}</span>
            {:else if c.kind === 'bool'}
              <span>{row[c.key] === true ? '✓' : ''}</span>
            {:else if c.kind === 'name'}
              <span class="ellipsis name" title={name}>{name}</span>
            {:else}
              <span class="ellipsis" title={show(row[c.key])}>{show(row[c.key])}</span>
            {/if}
          </div>
        {/each}
      </div>
    {/each}
  </div>
</div>

<style>
  .scroller {
    position: relative;
    height: 100%;
    overflow: auto;
    background: var(--surface);
    outline: none;
  }
  .scroller:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .head {
    position: sticky;
    top: 0;
    z-index: 3;
    display: flex;
    height: 32px;
    background: var(--surface-2);
    border-bottom: 1px solid var(--border-strong);
  }
  .body {
    position: relative;
  }
  .rrow {
    position: absolute;
    left: 0;
    right: 0;
    display: flex;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    cursor: default;
  }
  .striped .rrow.odd {
    background: var(--surface-2);
  }
  .lines .cell + .cell {
    border-left: 1px solid var(--border);
  }
  .lines .head .cell + .cell {
    border-left-color: var(--border-strong);
  }
  /* frozen Repository column: sticks right after the (always sticky) checkbox column */
  .frozen .cell.namecol {
    position: sticky;
    left: 38px; /* CHECK_W */
    z-index: 2;
    background: inherit;
    box-shadow: 1px 0 0 var(--border-strong);
  }
  .frozen .head .cell.namecol {
    background: var(--surface-2);
  }
  .compact .cell {
    font-size: var(--fs-sm);
  }
  .compact .tag {
    height: 16px;
    line-height: 16px;
  }
  /* tall rows: long text gets two lines instead of being cut at the first */
  .tall .cell.textcell > span {
    white-space: normal;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    line-height: 1.35;
  }
  /* `.scroller` prefix: as specific as `.striped .rrow.odd`, and later, so these win over the stripes */
  .scroller .rrow:hover {
    background: color-mix(in srgb, var(--accent-weak) 55%, var(--surface));
  }
  .scroller .rrow.picked {
    background: var(--accent-weak);
  }
  .scroller .rrow.active {
    background: var(--accent-weak);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .cell {
    flex: none;
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 8px;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    font-size: var(--fs);
  }
  .cell.num {
    justify-content: flex-end;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .cell.center {
    justify-content: center;
    color: var(--ok);
  }
  .cell.check {
    position: sticky;
    left: 0;
    z-index: 2;
    justify-content: center;
    padding: 0;
    background: inherit;
  }
  .head .cell.check {
    background: var(--surface-2);
  }
  .hcell {
    position: relative;
    font-weight: 600;
    color: var(--text-2);
    user-select: none;
  }
  .hcell.sortable {
    cursor: pointer;
  }
  .hcell.sortable:hover {
    background: var(--sunken);
  }
  .arrow {
    font-size: 9px;
    color: var(--accent);
  }
  .arrow.dim {
    color: var(--text-3);
    opacity: 0.6;
  }
  .grip {
    position: absolute;
    top: 0;
    right: 0;
    width: 8px;
    height: 100%;
    cursor: col-resize;
    touch-action: none;
  }
  .grip::after {
    content: '';
    position: absolute;
    right: 3px;
    top: 7px;
    bottom: 7px;
    width: 1px;
    background: var(--border-strong);
  }
  .grip:hover::after {
    background: var(--accent);
    width: 2px;
  }
  .name {
    font-weight: 600;
    color: var(--text);
  }
  .chips {
    display: flex;
    gap: 3px;
    overflow: hidden;
  }
  .tag {
    flex: none;
    height: 18px;
    line-height: 18px;
    padding: 0 6px;
    border-radius: 9px;
    background: var(--sunken);
    color: var(--text-2);
    font-size: var(--fs-xs);
  }
  .tag.more {
    background: transparent;
    color: var(--text-3);
  }
  .skel {
    display: block;
    width: 70%;
    height: 8px;
    border-radius: 4px;
    background: var(--sunken);
  }
  .rrow.loading {
    opacity: 0.7;
  }
</style>
