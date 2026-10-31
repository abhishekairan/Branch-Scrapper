// Sends browser.input events in order, one request at a time (the server handles requests concurrently, so
// without this a quick key down/up pair could be applied out of order). Mouse moves are coalesced: while a
// request is in flight only the newest pending move is kept.

import { api } from './client.ts'
import type { InputEvent } from './protocol.ts'

export class InputPump {
  onError: ((message: string) => void) | null = null
  private index: number
  private queue: InputEvent[] = []
  private busy = false
  private closed = false

  constructor(index: number) {
    this.index = index
  }

  send(event: InputEvent): void {
    if (this.closed) return
    if (event.type === 'mouse' && event.action === 'move') {
      const last = this.queue[this.queue.length - 1]
      if (last && last.type === 'mouse' && last.action === 'move') {
        this.queue[this.queue.length - 1] = event
        return
      }
    }
    this.queue.push(event)
    if (!this.busy) void this.drain()
  }

  close(): void {
    this.closed = true
    this.queue = []
  }

  private async drain(): Promise<void> {
    this.busy = true
    try {
      while (this.queue.length && !this.closed) {
        const event = this.queue.shift()!
        try {
          await api.call('browser.input', { index: this.index, event }, { timeoutMs: 5000 })
        } catch (e) {
          this.queue = []
          this.onError?.(e instanceof Error ? e.message : String(e))
        }
      }
    } finally {
      this.busy = false
    }
  }
}
