<script lang="ts">
  import { onMount } from 'svelte'
  import { init, nav } from './lib/app.svelte.ts'
  import { initView } from './lib/view.svelte.ts'
  import ConnBanner from './components/ConnBanner.svelte'
  import Nav from './components/Nav.svelte'
  import StatusBar from './components/StatusBar.svelte'
  import About from './views/About.svelte'
  import Browsers from './views/Browsers.svelte'
  import Data from './views/Data.svelte'
  import Run from './views/Run.svelte'

  onMount(() => {
    initView()
    init()
  })
</script>

<div class="shell">
  <Nav />
  <StatusBar />
  <ConnBanner />
  <main>
    {#if nav.route === 'run'}
      <Run />
    {:else if nav.route === 'browsers'}
      <Browsers />
    {:else if nav.route === 'about'}
      <About />
    {:else}
      <Data />
    {/if}
  </main>
</div>

<style>
  .shell {
    height: 100%;
    display: grid;
    grid-template-columns: var(--nav-w) minmax(0, 1fr);
    grid-template-rows: var(--bar-h) auto minmax(0, 1fr);
    grid-template-areas:
      'nav bar'
      'nav banner'
      'nav main';
  }
  main {
    grid-area: main;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
  }
</style>
