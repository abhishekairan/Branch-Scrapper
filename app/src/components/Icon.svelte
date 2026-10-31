<script lang="ts">
  type Name = 'run' | 'browsers' | 'data' | 'grid' | 'single' | 'list' | 'expand' | 'hand' | 'play' | 'stop' | 'eye' | 'external'
  let { name, size = 16 }: { name: Name; size?: number } = $props()

  // 16x16 stroke icons, drawn for this app.
  const paths: Record<Name, string> = {
    run: 'M3 2.5v11l9.5-5.5z',
    browsers: 'M2 3.5h12v9H2zM2 6.5h12M4 5h.01M6 5h.01',
    data: 'M3 4.5c0-1 2.2-1.8 5-1.8s5 .8 5 1.8-2.2 1.8-5 1.8-5-.8-5-1.8zM3 4.5v7c0 1 2.2 1.8 5 1.8s5-.8 5-1.8v-7M3 8c0 1 2.2 1.8 5 1.8S13 9 13 8',
    grid: 'M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9H7v4.5H2.5zM9 9h4.5v4.5H9z',
    single: 'M2.5 3h11v10h-11z',
    list: 'M2.5 4h11M2.5 8h11M2.5 12h11',
    expand: 'M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9',
    hand: 'M5.5 8V3.8a1 1 0 0 1 2 0V7m0-3.7a1 1 0 0 1 2 0V7m0-2.5a1 1 0 0 1 2 0V9.5c0 2.5-1.6 4-4 4-1.8 0-2.8-.8-3.7-2.2L2.9 9.2a1 1 0 0 1 1.6-1.2z',
    play: 'M4 2.5v11l9-5.5z',
    stop: 'M3.5 3.5h9v9h-9z',
    eye: 'M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8zM8 9.8a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0 0 3.6z',
    external: 'M9 2.5h4.5V7M13.5 2.5 7.5 8.5M11.5 9v4h-9V4h4',
  }
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 16 16"
  fill="none"
  stroke="currentColor"
  stroke-width="1.4"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  <path d={paths[name]} />
</svg>
