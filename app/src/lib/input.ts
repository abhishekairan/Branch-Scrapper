// Pure helpers that turn DOM-ish input into `browser.input` events. No DOM access, so they can be tested in Node.

import type { InputEvent, KeyInput, MouseInput } from './protocol.ts'

export interface Rect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * The largest box of aspect `aspect` (width / height) that fits inside `w` x `h` (letterbox / "contain").
 * Sizes are floored to whole CSS px so the box never overflows its container by a fraction.
 */
export function fitContain(w: number, h: number, aspect: number): { width: number; height: number } {
  if (!(w > 0) || !(h > 0) || !(aspect > 0)) return { width: 0, height: 0 }
  let width = w
  let height = w / aspect
  if (height > h) {
    height = h
    width = h * aspect
  }
  return { width: Math.max(1, Math.floor(width)), height: Math.max(1, Math.floor(height)) }
}

/**
 * Map a client (viewport) coordinate over a box that shows a page of `pageW` x `pageH` CSS px, letterboxed
 * ("contain") and centred, to page coordinates. Returns null when the point is in the letterbox bars.
 *
 * `frameAspect` (width / height of the picture actually drawn) decides the letterbox; it defaults to the page's
 * own aspect. Pass the bitmap's aspect when it can differ slightly from the page's (rounding of the scaled
 * frame): the picture is what the user sees, the page size is only the coordinate space that is sent.
 */
export function toPagePoint(
  clientX: number,
  clientY: number,
  rect: Rect,
  pageW: number,
  pageH: number,
  frameAspect: number = pageW / pageH,
): { x: number; y: number } | null {
  if (rect.width <= 0 || rect.height <= 0 || pageW <= 0 || pageH <= 0 || !(frameAspect > 0)) return null
  // exact (unfloored) fit: the same geometry the canvas is laid out with, up to sub-pixel rounding
  let shownW = rect.width
  let shownH = rect.width / frameAspect
  if (shownH > rect.height) {
    shownH = rect.height
    shownW = rect.height * frameAspect
  }
  const offX = rect.left + (rect.width - shownW) / 2
  const offY = rect.top + (rect.height - shownH) / 2
  const fx = (clientX - offX) / shownW
  const fy = (clientY - offY) / shownH
  if (fx < 0 || fy < 0 || fx > 1 || fy > 1) return null
  const x = fx * pageW
  const y = fy * pageH
  return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 }
}

/** CDP modifier bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8. */
export function modifierMask(e: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }): number {
  return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0)
}

const BUTTONS: Record<number, 'left' | 'middle' | 'right'> = { 0: 'left', 1: 'middle', 2: 'right' }

export function mouseButton(button: number): 'left' | 'middle' | 'right' {
  return BUTTONS[button] ?? 'left'
}

export function mouseEvent(
  action: MouseInput['action'],
  pt: { x: number; y: number },
  extra: Partial<Omit<MouseInput, 'type' | 'action' | 'x' | 'y'>> = {},
): MouseInput {
  return { type: 'mouse', action, x: pt.x, y: pt.y, ...extra }
}

interface KeyLike {
  key: string
  code: string
  keyCode: number
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
}

/** Keys that should never reach the page (browser/app shortcuts we do not want to forward). */
export function shouldForwardKey(e: KeyLike): boolean {
  if (e.key === 'Dead' || e.key === 'Process' || e.key === 'Unidentified') return false
  return true
}

/**
 * keydown / keyup -> CDP key event. A printable key (or Enter) is sent with `text` so Chrome types the
 * character; other keys carry only key/code/vk. Ctrl/Alt/Meta combinations never carry text.
 */
export function keyEvent(action: 'down' | 'up', e: KeyLike): KeyInput {
  const out: KeyInput = { type: 'key', action, key: e.key, code: e.code, vk: e.keyCode }
  const mods = modifierMask(e)
  if (mods) out.modifiers = mods
  if (action === 'down' && !e.ctrlKey && !e.altKey && !e.metaKey) {
    if (e.key.length === 1) out.text = e.key
    else if (e.key === 'Enter') out.text = '\r'
    else if (e.key === 'Tab') out.text = '\t'
  }
  return out
}

export function pasteEvent(text: string): InputEvent {
  return { type: 'text', text }
}
