<script lang="ts">
  // Right-hand detail drawer for the selected row (data.get = the full record).
  import Icon from './Icon.svelte'
  import { exactTime, languageEntries, relTime, stringList } from '../lib/datafilters.ts'
  import { copyText, drawer } from '../lib/data.svelte.ts'
  import { openExternal } from '../lib/tauri.ts'
  import { formatCount } from '../lib/format.ts'

  const rec = $derived(drawer.record)

  const STATS: [string, string][] = [
    ['stars', 'Stars'],
    ['forks', 'Forks'],
    ['watchers', 'Watchers'],
    ['contributors', 'Contributors'],
    ['commits', 'Commits'],
    ['releases', 'Releases'],
    ['branches', 'Branches'],
    ['tags', 'Tags'],
    ['open_issues', 'Open issues'],
    ['open_pull_requests', 'Open PRs'],
  ]
  const DATES: [string, string][] = [
    ['created_at', 'Created'],
    ['updated_at', 'Updated'],
    ['last_commit_at', 'Last commit'],
    ['scraped_at', 'Scraped'],
  ]
  const FLAGS: [string, string][] = [
    ['archived', 'Archived'],
    ['is_fork', 'Fork'],
    ['is_template', 'Template'],
  ]
  // shown in dedicated sections above; everything else goes to "All fields"
  const SHOWN = new Set([
    'full_name', 'url', 'description', 'languages', 'topics', 'tech_stack', 'matched_queries', 'query',
    ...STATS.map((s) => s[0]), ...DATES.map((s) => s[0]), ...FLAGS.map((s) => s[0]),
  ])

  const langs = $derived(languageEntries(rec?.languages))
  const topics = $derived(stringList(rec?.topics))
  const tech = $derived(stringList(rec?.tech_stack))
  const queries = $derived(stringList(rec?.matched_queries))
  const others = $derived(
    Object.entries(rec ?? {}).filter(([k, v]) => !SHOWN.has(k) && k !== 'raw_json' && v !== null && v !== undefined && v !== ''),
  )
  const url = $derived(typeof rec?.url === 'string' && rec.url ? rec.url : drawer.name ? `https://github.com/${drawer.name}` : '')

  function hue(name: string): number {
    let h = 0
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360
    return h
  }

  let copied = $state(false)
  async function copy(): Promise<void> {
    copied = await copyText(url)
    if (copied) setTimeout(() => (copied = false), 1500)
  }

  function fmt(v: unknown): string {
    if (typeof v === 'object') return JSON.stringify(v)
    return String(v)
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<aside class="drawer" aria-label="Repository details" onkeydown={(e) => e.key === 'Escape' && drawer.close()}>
  <header>
    <div class="grow">
      <div class="title selectable ellipsis" title={drawer.name}>{drawer.name}</div>
    </div>
    <button class="btn ghost sm" onclick={() => drawer.close()} aria-label="Close details" title="Close (Esc)">✕</button>
  </header>

  <div class="scroll">
    {#if drawer.error}
      <div class="msg err">{drawer.error}</div>
    {:else if !rec}
      <div class="muted">Loading…</div>
    {:else}
      <section>
        <div class="linkrow">
          <Icon name="external" size={14} />
          <span class="url selectable ellipsis" title={url}>{url}</span>
          <button class="btn sm" onclick={() => openExternal(url)}>Open</button>
          <button class="btn sm" onclick={copy}>{copied ? 'Copied' : 'Copy link'}</button>
        </div>
        {#if rec.description}<p class="desc selectable">{rec.description}</p>{/if}
        {#if rec.website}
          <p class="muted selectable ellipsis" title={String(rec.website)}>
            Website:
            {#if /^https:\/\//i.test(String(rec.website))}
              <a href={String(rec.website)} onclick={(e) => { e.preventDefault(); openExternal(String(rec.website)) }}>{rec.website}</a>
            {:else}
              {rec.website}
            {/if}
          </p>
        {/if}
        {#if rec.error}<div class="msg err">{rec.error}</div>{/if}
      </section>

      <section class="stats">
        {#each STATS as [k, label] (k)}
          {#if rec[k] !== null && rec[k] !== undefined}
            <div class="stat"><span class="v num">{formatCount(rec[k] as number)}</span><span class="l">{label}</span></div>
          {/if}
        {/each}
      </section>

      {#if rec.language || langs.length}
        <section>
          <h3>Languages</h3>
          {#if langs.length}
            <div class="bar" role="img" aria-label="Language breakdown">
              {#each langs as [n, p] (n)}
                <span style:width="{Math.max(p, 0.5)}%" style:background="hsl({hue(n)} 55% 50%)" title="{n} {p.toFixed(1)}%"></span>
              {/each}
            </div>
            <ul class="legend">
              {#each langs as [n, p] (n)}
                <li><i style:background="hsl({hue(n)} 55% 50%)"></i>{n} <span class="muted num">{p.toFixed(1)}%</span></li>
              {/each}
            </ul>
          {:else}
            <div>{rec.language}</div>
          {/if}
        </section>
      {/if}

      {#if topics.length}
        <section>
          <h3>Topics</h3>
          <div class="chips">{#each topics as t (t)}<span class="chip accent nodot">{t}</span>{/each}</div>
        </section>
      {/if}
      {#if tech.length}
        <section>
          <h3>Tech stack</h3>
          <div class="chips">{#each tech as t (t)}<span class="chip info nodot">{t}</span>{/each}</div>
        </section>
      {/if}
      {#if queries.length}
        <section>
          <h3>Found by</h3>
          <div class="chips">{#each queries as t (t)}<span class="chip nodot">{t}</span>{/each}</div>
        </section>
      {/if}

      <section>
        <h3>Dates</h3>
        <dl>
          {#each DATES as [k, label] (k)}
            {#if rec[k]}
              <dt>{label}</dt>
              <dd class="selectable" title={exactTime(rec[k])}>{relTime(rec[k])} <span class="muted">· {exactTime(rec[k])}</span></dd>
            {/if}
          {/each}
        </dl>
      </section>

      <section>
        <h3>Flags</h3>
        <div class="chips">
          {#each FLAGS as [k, label] (k)}
            <span class="chip nodot" class:warn={rec[k] === true}>{label}: {rec[k] === true ? 'yes' : rec[k] === false ? 'no' : '–'}</span>
          {/each}
        </div>
      </section>

      {#if others.length}
        <section>
          <h3>Other fields</h3>
          <dl>
            {#each others as [k, v] (k)}
              <dt>{k}</dt>
              <dd class="selectable mono">{fmt(v)}</dd>
            {/each}
          </dl>
        </section>
      {/if}
    {/if}
  </div>
</aside>

<style>
  .drawer {
    width: 380px;
    flex: none;
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--surface);
    border-left: 1px solid var(--border);
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px 8px 14px;
    border-bottom: 1px solid var(--border);
  }
  .title {
    font-weight: 600;
    font-size: var(--fs-lg);
  }
  .scroll {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 12px 14px 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  h3 {
    font-size: var(--fs-xs);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-3);
    font-weight: 600;
  }
  .linkrow {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-2);
  }
  .url {
    flex: 1;
    min-width: 0;
    font-family: var(--mono);
    font-size: 12px;
  }
  .desc {
    line-height: 1.5;
  }
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    gap: 6px;
  }
  .stat {
    display: flex;
    flex-direction: column;
    padding: 6px 8px;
    border-radius: var(--radius);
    background: var(--surface-2);
    border: 1px solid var(--border);
  }
  .stat .v {
    font-weight: 600;
    font-size: var(--fs-lg);
  }
  .stat .l {
    color: var(--text-3);
    font-size: var(--fs-xs);
  }
  .bar {
    display: flex;
    height: 10px;
    border-radius: 5px;
    overflow: hidden;
    background: var(--sunken);
  }
  .bar span {
    display: block;
    height: 100%;
  }
  .legend {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 2px 12px;
    font-size: var(--fs-sm);
  }
  .legend i {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 5px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .chip.nodot::before {
    display: none;
  }
  dl {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: 4px 12px;
    margin: 0;
  }
  dt {
    color: var(--text-3);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
</style>
