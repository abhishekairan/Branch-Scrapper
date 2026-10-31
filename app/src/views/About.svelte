<script lang="ts">
  import Icon from '../components/Icon.svelte'
  import { openExternal } from '../lib/tauri.ts'
  import {
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    BMC_URL,
    ISSUES_URL,
    LICENSE_NAME,
    LICENSE_URL,
    PAYPAL_URL,
    RELEASES_URL,
    REPO_URL,
    UPI_ID,
  } from '../lib/support.ts'

  let copied = $state(false)
  let copyFailed = $state(false)
  let timer: ReturnType<typeof setTimeout> | undefined

  function open(url: string): void {
    void openExternal(url).catch(() => {})
  }

  async function copyUpi(): Promise<void> {
    try {
      await navigator.clipboard.writeText(UPI_ID)
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

  const links = [
    { label: 'Source code', url: REPO_URL },
    { label: 'Report an issue', url: ISSUES_URL },
    { label: 'Releases', url: RELEASES_URL },
    { label: `${LICENSE_NAME} licence`, url: LICENSE_URL },
  ]
  const donate = [
    { label: 'Buy Me a Coffee', url: BMC_URL },
    { label: 'PayPal', url: PAYPAL_URL },
  ]
</script>

<div class="screen">
  <div class="col">
    <section class="card">
      <header>About</header>
      <div class="body stack">
        <div>
          <h1>{APP_NAME}</h1>
          <p class="muted">Version {APP_VERSION}</p>
        </div>
        <p>{APP_DESCRIPTION}</p>
        <p class="muted">
          Free software, licensed under the {LICENSE_NAME}. Forks and redistributed copies must stay open source
          under the same licence.
        </p>
        <div class="row wrap">
          {#each links as l (l.label)}
            <button class="btn" onclick={() => open(l.url)}>
              {l.label} <Icon name="external" size={13} />
            </button>
          {/each}
        </div>
      </div>
    </section>

    <section class="card">
      <header>Support development</header>
      <div class="body stack">
        <p class="muted">
          Branch Scrapper is free and has no ads or tracking. If it saves you time, you can chip in; it is entirely
          optional.
        </p>
        <div class="row wrap">
          {#each donate as d (d.label)}
            <button class="btn" onclick={() => open(d.url)}>
              {d.label} <Icon name="external" size={13} />
            </button>
          {/each}
        </div>
        <div class="row upi">
          <span class="muted">UPI</span>
          <code class="mono selectable grow ellipsis" title={UPI_ID}>{UPI_ID}</code>
          <button class="btn sm" onclick={() => void copyUpi()}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        {#if copyFailed}
          <p class="msg warn">Could not access the clipboard. Select the ID above and copy it manually.</p>
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
    max-width: 560px;
    display: flex;
    flex-direction: column;
    gap: var(--gap);
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  h1 {
    font-size: var(--fs-xl);
    letter-spacing: -0.01em;
  }
  .wrap {
    flex-wrap: wrap;
  }
  .upi {
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }
</style>
