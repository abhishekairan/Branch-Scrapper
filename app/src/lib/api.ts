// Typed WebSocket client for the sidecar. Framework-free and Tauri-free on purpose: the connection
// details come from `getInfo()` (the app passes one that asks the Rust shell), and tests/scripts can pass
// their own `createSocket` (e.g. to set an Origin header in Node).
//
// Keep erasable TypeScript only (see protocol.ts).

import {
  RPC,
  RpcError,
  parseFrame,
  type EventName,
  type Events,
  type Frame,
  type MethodName,
  type Methods,
  type ViewResult,
} from './protocol.ts'

export type ConnState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ConnectInfo {
  port: number
  token: string
}

export interface ClientOptions {
  /** Called before every connection attempt: the sidecar may have been restarted (new port + token). */
  getInfo: () => Promise<ConnectInfo>
  createSocket?: (url: string) => WebSocket
  /** Default per-call timeout. */
  timeoutMs?: number
  authTimeoutMs?: number
  backoffMinMs?: number
  backoffMaxMs?: number
}

export interface CallOptions {
  /** Milliseconds; `null` = wait forever (e.g. run.preview_counts). */
  timeoutMs?: number | null
}

interface Pending {
  resolve: (v: unknown) => void
  reject: (e: Error) => void
  timer: ReturnType<typeof setTimeout> | null
  method: string
}

interface ViewSet {
  indices: number[]
  fps: number
  maxWidth: number
}

type Listener<T> = (payload: T) => void

/** Per-method timeouts that differ from the default. */
const TIMEOUTS: Partial<Record<MethodName, number | null>> = {
  'run.preview_counts': null, // takes as long as GitHub does; run.stop cancels it
  'run.start': 60_000,
  'export.write': 10 * 60_000,
  'data.import_json': 5 * 60_000,
  'storage.move': 10 * 60_000, // copies the database; a big one on a slow disk takes a while
}

export class SidecarClient {
  state: ConnState = 'connecting'
  /** Reason for the last failed attempt (shown in the UI while reconnecting). */
  lastError = ''
  /** Number of successful connections so far; > 1 means we reconnected at least once. */
  connections = 0
  private opts: Required<Omit<ClientOptions, 'createSocket'>> & Pick<ClientOptions, 'createSocket'>
  private ws: WebSocket | null = null
  private nextId = 1
  private pending = new Map<number, Pending>()
  private listeners = new Map<string, Set<Listener<never>>>()
  private stateListeners = new Set<(s: ConnState) => void>()
  private frameListeners = new Set<Listener<Frame>>()
  private running = false
  private wake: (() => void) | null = null
  private view: ViewSet | null = null

  constructor(opts: ClientOptions) {
    this.opts = {
      timeoutMs: 15_000,
      authTimeoutMs: 6_000,
      backoffMinMs: 400,
      backoffMaxMs: 8_000,
      ...opts,
    }
  }

  // ------------------------------------------------------------ lifecycle

  /** Connect and keep reconnecting (with backoff) until `close()`. Idempotent. */
  start(): void {
    if (this.running) return
    this.running = true
    void this.loop()
  }

  /** Stop reconnecting and close the socket. */
  close(): void {
    this.running = false
    this.setState('closed')
    this.wake?.()
    const ws = this.ws
    if (ws) {
      try {
        ws.close(1000, 'client closing')
      } catch {
        /* already closed */
      }
    }
  }

  /** Skip the current backoff wait and try now (e.g. right after the shell restarted the sidecar). */
  retryNow(): void {
    this.wake?.()
  }

  get isOpen(): boolean {
    return this.state === 'open' && this.ws !== null
  }

  /** Resolves when the connection is open; rejects after `timeoutMs`. */
  waitOpen(timeoutMs = 30_000): Promise<void> {
    if (this.isOpen) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        off()
        reject(new RpcError(RPC.NOT_CONNECTED, 'not connected to the sidecar'))
      }, timeoutMs)
      const off = this.onState((s) => {
        if (s === 'open') {
          clearTimeout(timer)
          off()
          resolve()
        }
      })
    })
  }

  private setState(s: ConnState): void {
    if (this.state === s) return
    this.state = s
    for (const fn of [...this.stateListeners]) this.safe(() => fn(s))
  }

  private async loop(): Promise<void> {
    let attempt = 0
    while (this.running) {
      this.setState(this.connections === 0 ? 'connecting' : 'reconnecting')
      let authed = false
      try {
        const info = await this.opts.getInfo()
        authed = await this.connectOnce(info)
      } catch (e) {
        this.lastError = e instanceof Error ? e.message : String(e)
      }
      if (!this.running) break
      if (authed) attempt = 0
      this.setState('reconnecting')
      attempt += 1
      const delay =
        Math.min(this.opts.backoffMaxMs, this.opts.backoffMinMs * 2 ** (attempt - 1)) + Math.random() * 250
      await new Promise<void>((resolve) => {
        const t = setTimeout(() => {
          this.wake = null
          resolve()
        }, delay)
        this.wake = () => {
          clearTimeout(t)
          this.wake = null
          resolve()
        }
      })
    }
  }

  /** One connection. Resolves (with whether auth succeeded) when the socket closes. */
  private connectOnce(info: ConnectInfo): Promise<boolean> {
    return new Promise((resolve) => {
      const url = `ws://127.0.0.1:${info.port}`
      let ws: WebSocket
      try {
        ws = this.opts.createSocket ? this.opts.createSocket(url) : new WebSocket(url)
      } catch (e) {
        this.lastError = e instanceof Error ? e.message : String(e)
        resolve(false)
        return
      }
      ws.binaryType = 'arraybuffer'
      let authed = false
      const authTimer = setTimeout(() => {
        this.lastError = 'authentication timed out'
        ws.close()
      }, this.opts.authTimeoutMs)

      ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: info.token }))
      ws.onerror = () => {
        if (!authed) this.lastError = `cannot connect to the sidecar on port ${info.port}`
      }
      ws.onmessage = (ev: MessageEvent) => {
        if (typeof ev.data === 'string') {
          let msg: Record<string, unknown>
          try {
            msg = JSON.parse(ev.data)
          } catch {
            return
          }
          if (!authed) {
            if (msg.method === 'auth.ok') {
              authed = true
              clearTimeout(authTimer)
              this.ws = ws
              this.connections += 1
              this.lastError = ''
              this.setState('open')
              this.emit('auth.ok', msg.params)
              void this.resendView()
            }
            return
          }
          this.onJson(msg)
        } else if (authed) {
          const frame = parseFrame(ev.data as ArrayBuffer)
          if (frame) for (const fn of [...this.frameListeners]) this.safe(() => fn(frame))
        }
      }
      ws.onclose = (ev: CloseEvent) => {
        clearTimeout(authTimer)
        if (!authed && ev.code === 4401) this.lastError = 'the sidecar rejected the token'
        else if (!authed && !this.lastError) this.lastError = `connection closed (${ev.code})`
        if (this.ws === ws) {
          this.ws = null
          this.failAll(new RpcError(RPC.CONNECTION_CLOSED, 'connection to the sidecar closed'))
        }
        resolve(authed)
      }
    })
  }

  private failAll(err: Error): void {
    const all = [...this.pending.values()]
    this.pending.clear()
    for (const p of all) {
      if (p.timer) clearTimeout(p.timer)
      p.reject(err)
    }
  }

  // ------------------------------------------------------------ inbound

  private onJson(msg: Record<string, unknown>): void {
    if (typeof msg.method === 'string' && msg.id === undefined) {
      // Notification. May arrive BEFORE the reply of the call that caused it; listeners must not
      // depend on that ordering.
      this.emit(msg.method, msg.params)
      return
    }
    const id = msg.id
    if (typeof id !== 'number') return
    const p = this.pending.get(id)
    if (!p) return // timed out already
    this.pending.delete(id)
    if (p.timer) clearTimeout(p.timer)
    const err = msg.error as { code: number; message: string; data?: unknown } | undefined
    if (err) p.reject(new RpcError(err.code, err.message, err.data))
    else p.resolve(msg.result)
  }

  private emit(name: string, payload: unknown): void {
    const set = this.listeners.get(name)
    if (!set) return
    for (const fn of [...set]) this.safe(() => (fn as Listener<unknown>)(payload))
  }

  private safe(fn: () => void): void {
    try {
      fn()
    } catch (e) {
      console.error('[sidecar client] listener threw', e)
    }
  }

  // ------------------------------------------------------------ subscriptions

  /** Subscribe to a server event. Returns the unsubscribe function. */
  on<K extends EventName>(event: K, fn: Listener<Events[K]>): () => void {
    let set = this.listeners.get(event)
    if (!set) this.listeners.set(event, (set = new Set()))
    set.add(fn as Listener<never>)
    return () => set.delete(fn as Listener<never>)
  }

  onState(fn: (s: ConnState) => void): () => void {
    this.stateListeners.add(fn)
    return () => this.stateListeners.delete(fn)
  }

  /** Subscribe to every decoded screencast frame (all slots). Latest-frame-wins is the listener's job. */
  onFrame(fn: Listener<Frame>): () => void {
    this.frameListeners.add(fn)
    return () => this.frameListeners.delete(fn)
  }

  // ------------------------------------------------------------ outbound

  /** JSON-RPC request with an id and a timeout. */
  call<M extends MethodName>(
    method: M,
    params?: Methods[M]['params'],
    opts: CallOptions = {},
  ): Promise<Methods[M]['result']> {
    const ws = this.ws
    if (!ws || this.state !== 'open') {
      return Promise.reject(new RpcError(RPC.NOT_CONNECTED, 'not connected to the sidecar'))
    }
    const id = this.nextId++
    const timeoutMs = opts.timeoutMs !== undefined ? opts.timeoutMs : (TIMEOUTS[method] ?? this.opts.timeoutMs)
    return new Promise((resolve, reject) => {
      const timer =
        timeoutMs === null
          ? null
          : setTimeout(() => {
              this.pending.delete(id)
              reject(new RpcError(RPC.TIMEOUT, `${method} timed out after ${Math.round(timeoutMs / 1000)} s`))
            }, timeoutMs)
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer, method })
      try {
        ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params: params ?? {} }))
      } catch (e) {
        this.pending.delete(id)
        if (timer) clearTimeout(timer)
        reject(new RpcError(RPC.CONNECTION_CLOSED, e instanceof Error ? e.message : String(e)))
      }
    })
  }

  /** Request without an id: executed, no reply, errors are not reported. */
  notify<M extends MethodName>(method: M, params?: Methods[M]['params']): boolean {
    if (!this.ws || this.state !== 'open') return false
    try {
      this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params: params ?? {} }))
      return true
    } catch {
      return false
    }
  }

  // ------------------------------------------------------------ screencast view set

  /**
   * Replace this client's streaming set (server side: the whole set is replaced on every call). The latest
   * set is remembered and sent again after a reconnect, so callers only have to call this when it changes.
   */
  async setView(indices: number[], fps = 2, maxWidth = 480): Promise<ViewResult | null> {
    this.view = { indices: [...indices], fps, maxWidth }
    if (!this.isOpen) return null
    return this.call('browser.view', { indices, fps, max_width: maxWidth })
  }

  private async resendView(): Promise<void> {
    const v = this.view
    // An empty set is the server's default for a new connection, nothing to restore.
    if (!v || v.indices.length === 0) return
    try {
      await this.call('browser.view', { indices: v.indices, fps: v.fps, max_width: v.maxWidth })
    } catch (e) {
      console.warn('[sidecar client] could not restore the view set', e)
    }
  }
}
