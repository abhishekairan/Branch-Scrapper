<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import { conn, run, setCheckUpdates, setThemeMode } from '../lib/app.svelte.ts'
  import { checkNow, installNow, upd } from '../lib/updates.svelte.ts'
  import { formatBytes, formatCount } from '../lib/format.ts'
  import {
    askReset,
    busy,
    cancelChange,
    chooseFolder,
    confirmMove,
    confirmReset,
    refreshStorage,
    sto,
  } from '../lib/storage.svelte.ts'
  import { inTauri, revealInFolder } from '../lib/tauri.ts'
  import { THEMES, theme } from '../lib/theme.svelte.ts'
  import type { ThemeMode } from '../lib/theme.svelte.ts'
  import { ZOOM_STEPS, formatZoom, setZoom, stepZoom, zoom } from '../lib/zoom.svelte.ts'

  const THEME_LABEL: Record<ThemeMode, string> = { system: 'System', light: 'Light', dark: 'Dark' }

  let themeError = $state('')
  let updatesError = $state('')
  let copied = $state(false)
  let copyFailed = $state(false)
  let openError = $state('')
  let timer: ReturnType<typeof setTimeout> | undefined

  const info = $derived(sto.info)
  const locked = $derived(run.active)
  const working = $derived(busy())
  const dataDir = $derived(conn.dataDir || '…')

  onMount(() => () => clearTimeout(timer))

  // (re)load whenever the connection is (re)established, e.g. after the backend restarted
  $effect(() => {
    if (conn.state === 'open') untrack(() => void refreshStorage())
  })

  async function pickTheme(mode: ThemeMode): Promise<void> {
    themeError = await setThemeMode(mode)
  }

  async function pickCheckUpdates(on: boolean): Promise<void> {
    updatesError = await setCheckUpdates(on)
  }

  async function copyPath(): Promise<void> {
    try {
      await navigator.clipboard.writeText(conn.dataDir)
      copied = true
      copyFailed = false
    } catch {
      copied = false
      copyFailed = true
    }
    clearTimeout(timer)
    timer = setTimeout(() => {
      copied = false
      copyFailed = false
    }, 2500)
  }

  async function openFolder(): Promise<void> {
    openError = ''
    try {
      await revealInFolder(sto.dbPath)
    } catch (e) {
      openError = `Could not open the folder: ${e instanceof Error ? e.message : String(e)}`
    }
  }

  function repoCount(counts: Record<string, number> | null): string {
    const n = counts?.repos
    return n === undefined ? 'no repositories' : `${formatCount(n)} ${n === 1 ? 'repository' : 'repositories'}`
  }
</script>

<div class="screen">
  <div class="col">
    <section class="card">
      <header>Theme</header>
      <div class="body stack">
        <div class="seg" role="group" aria-label="Theme">
          {#each THEMES as t (t)}
            <button aria-pressed={theme.mode === t} onclick={() => void pickTheme(t)}>{THEME_LABEL[t]}</button>
          {/each}
        </div>
        <p class="muted">System follows your operating system's light or dark setting. The choice is remembered.</p>
        {#if themeError}<p class="msg warn">{themeError}</p>{/if}
      </div>
    </section>

    <section class="card">
      <header>Interface size</header>
      <div class="body stack">
        <div class="row wrap">
          <button class="btn sm" aria-label="Smaller" title="Smaller (Ctrl + -)" disabled={!zoom.supported || zoom.factor <= ZOOM_STEPS[0]} onclick={() => void stepZoom(-1)}>−</button>
          <div class="seg" role="group" aria-label="Interface size">
            {#each ZOOM_STEPS as z (z)}
              <button aria-pressed={zoom.factor === z} disabled={!zoom.supported} onclick={() => void setZoom(z)}>{formatZoom(z)}</button>
            {/each}
          </div>
          <button class="btn sm" aria-label="Larger" title="Larger (Ctrl + =)" disabled={!zoom.supported || zoom.factor >= ZOOM_STEPS[ZOOM_STEPS.length - 1]} onclick={() => void stepZoom(1)}>+</button>
        </div>
        {#if zoom.supported}
          <p class="muted">
            Makes text, buttons and the browser previews larger or smaller. Shortcuts: Ctrl + = larger, Ctrl + −
            smaller, Ctrl + 0 back to 100%. Remembered on this computer.
          </p>
        {:else}
          <p class="muted">Available in the installed app. In a web browser, use the browser's own zoom.</p>
        {/if}
      </div>
    </section>

    <section class="card">
      <header>Updates</header>
      <div class="body stack">
        <div class="kv">
          <span class="muted">Installed version</span>
          <span class="num">{upd.currentVersion || conn.version || '…'}</span>
          <span class="muted">Last checked</span>
          <span>{upd.lastChecked ? new Date(upd.lastChecked).toLocaleString() : 'never'}</span>
        </div>

        {#if upd.supported}
          <label class="check">
            <input
              type="checkbox"
              checked={upd.auto}
              onchange={(e) => void pickCheckUpdates(e.currentTarget.checked)}
            />
            Automatically check for updates
          </label>
          <p class="muted">
            When on, Branch Scrapper looks for a new version when it starts, at most once a day. It only contacts
            GitHub and sends none of your data. Updates are installed only when you choose, and are checked against
            a signature before they run.
          </p>
          {#if updatesError}<p class="msg warn">{updatesError}</p>{/if}

          <div class="row wrap">
            <button class="btn" disabled={upd.checking || upd.phase !== 'idle'} onclick={() => void checkNow(true)}>
              {upd.checking ? 'Checking…' : 'Check now'}
            </button>
            {#if upd.result === 'available' && upd.available}
              <button class="btn ghost" onclick={() => (upd.notesOpen = true)}>What's new</button>
              <button
                class="btn primary"
                disabled={locked || upd.phase !== 'idle'}
                onclick={() => void installNow()}
              >
                Install {upd.available.version} & restart
              </button>
            {/if}
          </div>

          {#if upd.result === 'available' && upd.available}
            <p class="msg info" role="status">
              Version {upd.available.version} is available.
              {#if locked}Stop the current run to install it.{/if}
              {#if upd.phase !== 'idle'}Installing: see the bar at the top.{/if}
            </p>
          {:else if upd.result === 'current'}
            <p class="msg info" role="status">You have the latest version.</p>
          {:else if upd.result === 'error'}
            <p class="msg warn" role="status">{upd.error} You can try again with "Check now".</p>
          {/if}
          {#if upd.installError}<p class="msg warn">{upd.installError}</p>{/if}
        {:else}
          <p class="muted">Updates are available in the installed app.</p>
        {/if}
      </div>
    </section>

    <section class="card">
      <header>Storage</header>
      <div class="body stack">
        <div class="stack tight">
          <span class="label">Data folder</span>
          <div class="row path">
            <code class="mono selectable grow" title={dataDir}>{dataDir}</code>
            {#if inTauri && sto.dbPath}
              <button class="btn sm" onclick={() => void openFolder()}>Open folder</button>
            {/if}
            <button class="btn sm" onclick={() => void copyPath()}>{copied ? 'Copied' : 'Copy path'}</button>
          </div>
          <p class="muted">
            Holds the database of scraped repositories, your search settings and the logs.
            {#if info}Database size: <span class="num">{formatBytes(info.db_bytes)}</span>.{/if}
          </p>
          {#if copyFailed}
            <p class="msg warn">Could not access the clipboard. Select the path above and copy it manually.</p>
          {/if}
          {#if openError}<p class="msg warn">{openError}</p>{/if}
        </div>

        {#if info?.problem}
          <p class="msg warn">
            The folder you chose earlier is not being used: {info.problem}. Branch Scrapper is using the default
            folder instead.
          </p>
        {/if}
        {#if info && !info.can_change}
          <p class="msg info">
            The data folder is fixed by the {info.source === 'arg' ? '--data-dir option' : 'GHOSTSCRAPER_HOME variable'}
            this app was started with, so it cannot be changed here.
          </p>
        {/if}
        {#if info?.pending_restart && sto.step === 'idle' && !sto.needsRestart}
          <p class="msg warn">
            A change to {info.pending_restart} is saved and takes effect when the backend restarts.
          </p>
        {/if}

        {#if sto.step === 'confirm-move' && sto.plan}
          <div class="confirm stack" role="group" aria-label="Confirm the new data folder">
            <div class="fromto">
              <span class="muted">From</span>
              <code class="mono selectable">{sto.plan.from}</code>
              <span class="muted">To</span>
              <code class="mono selectable">{sto.plan.to}</code>
            </div>
            {#if sto.plan.mode === 'copy'}
              <p>
                Your data (database, settings and logs, about {formatBytes(sto.plan.bytes_to_copy)}) will be
                <strong>copied</strong> to the new folder and checked. The current folder is kept exactly as it is;
                you can delete it yourself once you have made sure everything is there.
              </p>
            {:else}
              <p>
                That folder already holds Branch Scrapper data ({repoCount(sto.plan.existing)}). It will be
                <strong>used as it is</strong>: nothing is copied into it or changed. The data you have now stays
                in the current folder.
              </p>
            {/if}
            <p class="muted">The app restarts its backend to switch; this takes a few seconds.</p>
            <div class="row">
              <button class="btn primary" disabled={working} onclick={() => void confirmMove()}>
                {sto.plan.mode === 'copy' ? 'Copy and switch' : 'Switch to this folder'}
              </button>
              <button class="btn" disabled={working} onclick={cancelChange}>Cancel</button>
            </div>
          </div>
        {:else if sto.step === 'confirm-reset' && info}
          <div class="confirm stack" role="group" aria-label="Confirm going back to the default folder">
            <div class="fromto">
              <span class="muted">From</span>
              <code class="mono selectable">{dataDir}</code>
              <span class="muted">To</span>
              <code class="mono selectable">{info.default_dir}</code>
            </div>
            <p>
              Branch Scrapper will use the default folder again. The data in your custom folder is
              <strong>kept</strong>: nothing is deleted or moved, and it is not copied back.
            </p>
            {#if info.default_has_data}
              <p class="msg warn">
                The default folder still contains the data from before you moved it, so that older data is what you
                will see.
              </p>
            {/if}
            <div class="row">
              <button class="btn primary" disabled={working} onclick={() => void confirmReset()}>
                Use the default folder
              </button>
              <button class="btn" disabled={working} onclick={cancelChange}>Cancel</button>
            </div>
          </div>
        {:else}
          <div class="row wrap">
            <button
              class="btn"
              disabled={working || locked || !info?.can_change}
              onclick={() => void chooseFolder()}
            >
              {sto.step === 'checking' ? 'Checking…' : 'Change location…'}
            </button>
            {#if info?.custom || info?.problem}
              <button class="btn" disabled={working || locked || !info?.can_change} onclick={askReset}>
                Use the default location
              </button>
            {/if}
          </div>
          {#if locked && info?.can_change}
            <p class="muted">A run is active. Stop it before changing the data folder.</p>
          {/if}
        {/if}

        {#if sto.step === 'working'}
          <p class="msg info" role="status">Copying and checking your data. Please keep the app open…</p>
        {:else if sto.step === 'restarting'}
          <p class="msg info" role="status">Restarting the backend on the new folder…</p>
        {/if}
        {#if sto.error}<p class="msg err" role="alert">{sto.error}</p>{/if}
        {#each sto.warnings as w (w)}
          <p class="msg warn">{w}</p>
        {/each}
        {#if sto.done}
          <p class="msg info" role="status">{sto.done}</p>
        {/if}
        {#if sto.needsRestart}
          <p class="msg warn">Restart Branch Scrapper for the change to take effect.</p>
        {/if}
      </div>
    </section>
  </div>
</div>

<style>
  .screen {
    height: 100%;
    overflow-y: auto;
    padding: var(--gap);
  }
  .col {
    max-width: 640px;
    display: flex;
    flex-direction: column;
    gap: var(--gap);
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .stack.tight {
    gap: 6px;
  }
  .label {
    font-weight: 600;
  }
  .kv {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 4px 16px;
    align-items: baseline;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
  }
  .wrap {
    flex-wrap: wrap;
  }
  .seg {
    align-self: flex-start;
  }
  .path {
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }
  .path code {
    overflow-wrap: anywhere; /* a long path wraps instead of hiding its end */
  }
  .confirm {
    padding: 12px;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    background: var(--surface-2);
  }
  .fromto {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 4px 10px;
    align-items: baseline;
  }
  .fromto code {
    overflow-wrap: anywhere;
  }
</style>
