<script lang="ts">
  import { onMount } from 'svelte'
  import { controlError, controlState, run } from '../lib/app.svelte.ts'
  import { FrameRenderer } from '../lib/frames.ts'
  import { InputPump } from '../lib/inputpump.ts'
  import {
    fitContain,
    keyEvent,
    modifierMask,
    mouseButton,
    mouseEvent,
    pasteEvent,
    shouldForwardKey,
    toPagePoint,
  } from '../lib/input.ts'
  import { onSlotFrame, reportTileWidth, reportVisible, view } from '../lib/view.svelte.ts'

  interface Props {
    index: number
    /** forward mouse/keyboard to the browser while the worker is paused by the user */
    interactive?: boolean
    /** report the drawn width so the sidecar can pick max_width (Single layout) */
    trackWidth?: boolean
    /** same, for a Grid tile */
    trackTile?: boolean
  }
  let { index, interactive = false, trackWidth = false, trackTile = false }: Props = $props()

  let canvas: HTMLCanvasElement | undefined = $state()
  let box: HTMLDivElement | undefined = $state()
  let drawn = $state(false)
  // The preview is letterboxed in JS instead of with `width/height: 100%` + object-fit: the canvas gets an
  // explicit pixel size (the largest box of the frame's aspect that fits), so it can never be taller or wider
  // than the area it lives in, whatever the frame size or the window size.
  let boxW = $state(0)
  let boxH = $state(0)
  let aspect = $state(16 / 10) // width / height of the last drawn frame
  const fit = $derived(fitContain(boxW, boxH, aspect))
  let renderer: FrameRenderer | null = null
  let pump: InputPump | null = null
  let lastMove = 0
  let lastPt: { x: number; y: number } | null = null

  // Input is only ever sent while the worker is paused AND has reached the "paused" state.
  const live = $derived(interactive && controlState(index) === 'active')

  const overlay = $derived(
    drawn
      ? ''
      : !run.active
        ? 'No active run'
        : view.waiting.includes(index)
          ? 'Starting browser…'
          : view.streaming.includes(index)
            ? 'Waiting for the first frame…'
            : 'Not streaming',
  )

  // tell the view how wide the picture is really drawn, so the sidecar sends a frame of that width (x dpr)
  $effect(() => {
    if (fit.width <= 0) return
    if (trackWidth) view.singleCssWidth = fit.width
    if (trackTile) reportTileWidth(index, fit.width)
  })

  // a new run starts from a clean canvas
  $effect(() => {
    void run.startedAt
    renderer?.clear()
    drawn = false
  })

  onMount(() => {
    const cv = canvas!
    const bx = box!
    renderer = new FrameRenderer(cv)
    renderer.onDraw = () => {
      drawn = true
      if (cv.width > 0 && cv.height > 0) aspect = cv.width / cv.height
    }
    const offFrames = onSlotFrame(index, (f) => renderer?.push(f))

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) reportVisible(index, e.isIntersecting)
      },
      { threshold: 0.05 },
    )
    io.observe(bx)

    const ro = new ResizeObserver((entries) => {
      const r = entries[entries.length - 1].contentRect
      boxW = r.width
      boxH = r.height
    })
    ro.observe(bx)

    pump = new InputPump(index)
    pump.onError = (m) => (controlError.text = m)

    // wheel must be non-passive so the page behind the canvas does not scroll
    const onWheel = (e: WheelEvent) => {
      if (!live) return
      e.preventDefault()
      const p = point(e)
      if (p) pump?.send(mouseEvent('wheel', p, { delta_x: e.deltaX, delta_y: e.deltaY, modifiers: modifierMask(e) }))
    }
    cv.addEventListener('wheel', onWheel, { passive: false })

    return () => {
      cv.removeEventListener('wheel', onWheel)
      offFrames()
      io.disconnect()
      ro.disconnect()
      reportVisible(index, false)
      pump?.close()
      renderer?.destroy()
      renderer = null
    }
  })

  function point(e: MouseEvent): { x: number; y: number } | null {
    if (!canvas || !box || !renderer || canvas.height <= 0) return null
    // the whole box is the letterbox area; points in the bars (outside the drawn frame) map to null and are ignored
    return toPagePoint(
      e.clientX,
      e.clientY,
      box.getBoundingClientRect(),
      renderer.pageWidth,
      renderer.pageHeight,
      canvas.width / canvas.height,
    )
  }

  function onMove(e: MouseEvent): void {
    if (!live) return
    const now = performance.now()
    if (now - lastMove < 30) return
    lastMove = now
    const p = point(e)
    if (p) {
      lastPt = p
      pump?.send(mouseEvent('move', p, { modifiers: modifierMask(e) }))
    }
  }

  function onDown(e: MouseEvent): void {
    if (!live) return
    e.preventDefault()
    canvas?.focus()
    const p = point(e)
    if (!p) return
    lastPt = p
    pump?.send(
      mouseEvent('down', p, { button: mouseButton(e.button), click_count: e.detail || 1, modifiers: modifierMask(e) }),
    )
    // the button may be released outside the canvas
    window.addEventListener(
      'mouseup',
      (up) => {
        if (!live) return
        const q = point(up) ?? lastPt
        if (q) pump?.send(mouseEvent('up', q, { button: mouseButton(up.button), click_count: up.detail || 1, modifiers: modifierMask(up) }))
      },
      { once: true },
    )
  }

  function onKey(action: 'down' | 'up', e: KeyboardEvent): void {
    if (!live || !shouldForwardKey(e)) return
    e.preventDefault()
    pump?.send(keyEvent(action, e))
  }

  function onPaste(e: ClipboardEvent): void {
    if (!live) return
    e.preventDefault()
    const text = e.clipboardData?.getData('text/plain')
    if (text) pump?.send(pasteEvent(text))
  }
</script>

<div class="box" class:live bind:this={box}>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <canvas
    bind:this={canvas}
    width="16"
    height="10"
    tabindex={interactive ? 0 : -1}
    aria-label={`Browser ${index + 1}`}
    style:width={fit.width ? `${fit.width}px` : '0'}
    style:height={fit.height ? `${fit.height}px` : '0'}
    onmousemove={onMove}
    onmousedown={onDown}
    onkeydown={(e) => onKey('down', e)}
    onkeyup={(e) => onKey('up', e)}
    onpaste={onPaste}
    oncontextmenu={(e) => live && e.preventDefault()}
  ></canvas>
  {#if overlay}<div class="overlay">{overlay}</div>{/if}
</div>

<style>
  /* Fills its (positioned) parent without contributing to the parent's size, so the frame's pixel size can
     never stretch the layout; the canvas is centred inside it at an explicit, aspect-correct size. */
  .box {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    background: var(--sunken);
    overflow: hidden;
  }
  canvas {
    display: block;
    flex: none;
    outline: none;
  }
  .box.live {
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  .box.live canvas {
    cursor: default;
  }
  .overlay {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: var(--text-3);
    font-size: var(--fs-sm);
    pointer-events: none;
  }
</style>
