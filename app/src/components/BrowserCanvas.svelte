<script lang="ts">
  import { onMount } from 'svelte'
  import { controlError, controlState, run } from '../lib/app.svelte.ts'
  import { FrameRenderer } from '../lib/frames.ts'
  import { InputPump } from '../lib/inputpump.ts'
  import {
    keyEvent,
    modifierMask,
    mouseButton,
    mouseEvent,
    pasteEvent,
    shouldForwardKey,
    toPagePoint,
  } from '../lib/input.ts'
  import { onSlotFrame, reportVisible, view } from '../lib/view.svelte.ts'

  interface Props {
    index: number
    /** forward mouse/keyboard to the browser while the worker is paused by the user */
    interactive?: boolean
    /** report this box' width so the sidecar can pick max_width (Single layout) */
    trackWidth?: boolean
  }
  let { index, interactive = false, trackWidth = false }: Props = $props()

  let canvas: HTMLCanvasElement | undefined = $state()
  let box: HTMLDivElement | undefined = $state()
  let drawn = $state(false)
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
    }
    const offFrames = onSlotFrame(index, (f) => renderer?.push(f))

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) reportVisible(index, e.isIntersecting)
      },
      { threshold: 0.05 },
    )
    io.observe(bx)

    let ro: ResizeObserver | null = null
    if (trackWidth) {
      ro = new ResizeObserver((entries) => {
        view.singleCssWidth = entries[0].contentRect.width
      })
      ro.observe(bx)
    }

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
      ro?.disconnect()
      reportVisible(index, false)
      pump?.close()
      renderer?.destroy()
      renderer = null
    }
  })

  function point(e: MouseEvent): { x: number; y: number } | null {
    if (!canvas || !renderer) return null
    return toPagePoint(e.clientX, e.clientY, canvas.getBoundingClientRect(), renderer.pageWidth, renderer.pageHeight)
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
  .box {
    position: relative;
    width: 100%;
    height: 100%;
    background: var(--sunken);
    overflow: hidden;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
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
