<script lang="ts">
  import { onMount } from 'svelte'
  import { init, nav } from './lib/app.svelte.ts'
  import { initTheme } from './lib/theme.svelte.ts'
  import { initView } from './lib/view.svelte.ts'
  import { initZoom } from './lib/zoom.svelte.ts'
  import ConnBanner from './components/ConnBanner.svelte'
  import Nav from './components/Nav.svelte'
  import StatusBar from './components/StatusBar.svelte'
  import UpdateBanner from './components/UpdateBanner.svelte'
  import UpdateNotes from './components/UpdateNotes.svelte'
  import About from './views/About.svelte'
  import Browsers from './views/Browsers.svelte'
  import Data from './views/Data.svelte'
  import Run from './views/Run.svelte'
  import Settings from './views/Settings.svelte'

  onMount(() => {
    initTheme()
    initZoom()
    initView()
    init()
  })
</script>

<div class="shell">
  <Nav />
  <StatusBar />
  <div class="banners">
    <ConnBanner />
    <UpdateBanner />
  </div>
  <main>
    {#if nav.route === 'run'}
      <Run />
    {:else if nav.route === 'browsers'}
      <Browsers />
    {:else if nav.route === 'settings'}
      <Settings />
    {:else if nav.route === 'about'}
      <About />
    {:else}
      <Data />
    {/if}
  </main>
</div>
<UpdateNotes />

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
  .banners {
    grid-area: banner;
    min-width: 0;
  }
  main {
    grid-area: main;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
  }
</style>
