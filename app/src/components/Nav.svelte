<script lang="ts">
  import { conn, go, nav, type Route } from '../lib/app.svelte.ts'
  import Icon from './Icon.svelte'

  const items: { route: Route; label: string; icon: 'run' | 'browsers' | 'data' }[] = [
    { route: 'run', label: 'Run', icon: 'run' },
    { route: 'browsers', label: 'Browsers', icon: 'browsers' },
    { route: 'data', label: 'Data', icon: 'data' },
  ]
</script>

<nav aria-label="Main">
  <div class="brand">
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="var(--accent)" />
      <path
        d="M16 6c-4.4 0-8 3.4-8 7.6V25l2.7-2.2L13.3 25l2.7-2.2L18.7 25l2.6-2.2L24 25V13.6C24 9.4 20.4 6 16 6z"
        fill="var(--on-accent)"
      />
      <circle cx="12.8" cy="14" r="1.8" fill="var(--accent)" />
      <circle cx="19.2" cy="14" r="1.8" fill="var(--accent)" />
    </svg>
    <span>Branch Scrapper</span>
  </div>

  <ul>
    {#each items as it (it.route)}
      <li>
        <button
          class:active={nav.route === it.route}
          aria-current={nav.route === it.route ? 'page' : undefined}
          onclick={() => go(it.route)}
        >
          <Icon name={it.icon} />
          <span>{it.label}</span>
        </button>
      </li>
    {/each}
  </ul>

  <button
    class="about"
    class:active={nav.route === 'about'}
    aria-current={nav.route === 'about' ? 'page' : undefined}
    onclick={() => go('about')}
  >
    <Icon name="info" />
    <span>About</span>
  </button>

  <div class="foot muted">
    {#if conn.version}<span>sidecar v{conn.version}</span>{/if}
    {#if conn.testMode}<span class="test">test mode (fake browsers)</span>{/if}
  </div>
</nav>

<style>
  nav {
    grid-area: nav;
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 10px 8px;
    min-height: 0;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 4px 8px 14px;
    font-weight: 650;
    font-size: var(--fs-lg);
    letter-spacing: -0.01em;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  li button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    height: 32px;
    padding: 0 10px;
    border: 0;
    border-radius: var(--radius);
    background: transparent;
    color: var(--text-2);
    font-weight: 500;
    text-align: left;
  }
  li button:hover,
  .about:hover {
    background: var(--sunken);
    color: var(--text);
  }
  li button.active,
  .about.active {
    background: var(--accent-weak);
    color: var(--accent);
  }
  .about {
    margin-top: auto;
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    height: 32px;
    padding: 0 10px;
    border: 0;
    border-radius: var(--radius);
    background: transparent;
    color: var(--text-2);
    font-weight: 500;
    text-align: left;
  }
  .foot {
    margin-top: 0;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: var(--fs-xs);
  }
  .test {
    color: var(--warn);
    font-weight: 600;
  }
</style>
