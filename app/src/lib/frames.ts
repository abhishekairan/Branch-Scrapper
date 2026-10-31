// Draws screencast frames on a <canvas>. Latest frame wins: while one JPEG is being decoded, newer frames
// replace the waiting one, so a slow decode never builds a queue. Bitmaps are closed right after drawing.

import type { Frame } from './protocol.ts'

export class FrameRenderer {
  /** page viewport (CSS px) of the last frame: the coordinate space for browser.input */
  pageWidth = 0
  pageHeight = 0
  /** performance.now() of the last drawn frame, 0 = none yet */
  lastDrawn = 0
  onDraw: (() => void) | null = null
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D | null
  private waiting: Frame | null = null
  private busy = false
  private dead = false

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d', { alpha: false })
  }

  push(frame: Frame): void {
    if (this.dead) return
    this.waiting = frame // replaces any frame that has not been decoded yet
    if (!this.busy) void this.pump()
  }

  clear(): void {
    this.waiting = null
    this.lastDrawn = 0
    this.pageWidth = this.pageHeight = 0
    if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
  }

  destroy(): void {
    this.dead = true
    this.waiting = null
    this.onDraw = null
  }

  private async pump(): Promise<void> {
    this.busy = true
    try {
      while (this.waiting && !this.dead) {
        const frame = this.waiting
        this.waiting = null
        let bitmap: ImageBitmap | null = null
        try {
          bitmap = await createImageBitmap(new Blob([frame.jpeg as BlobPart], { type: 'image/jpeg' }))
        } catch {
          continue // a corrupt frame: skip it
        }
        if (this.dead || !this.ctx) {
          bitmap.close()
          return
        }
        if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
          this.canvas.width = bitmap.width
          this.canvas.height = bitmap.height
        }
        this.ctx.drawImage(bitmap, 0, 0)
        bitmap.close()
        this.pageWidth = frame.width
        this.pageHeight = frame.height
        this.lastDrawn = performance.now()
        this.onDraw?.()
      }
    } finally {
      this.busy = false
    }
  }
}
