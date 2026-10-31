// Small display formatters.

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '–'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '–'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  const h = Math.floor(m / 60)
  return `${h}h ${String(m % 60).padStart(2, '0')}m`
}

/** "about 3–6 min" style range for the time estimate. */
export function formatRange(lowSeconds: number, highSeconds: number): string {
  const f = (s: number) => {
    if (s < 90) return `${Math.max(1, Math.round(s))} s`
    if (s < 5400) return `${Math.round(s / 60)} min`
    return `${(s / 3600).toFixed(1)} h`
  }
  const a = f(lowSeconds)
  const b = f(highSeconds)
  return a === b ? `about ${a}` : `${a} – ${b}`
}

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return '–'
  return n.toLocaleString('en-US')
}
