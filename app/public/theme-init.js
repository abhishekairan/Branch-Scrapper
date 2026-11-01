// Runs synchronously in <head>, before anything is painted: apply the saved theme so there is no flash of the
// wrong one. Classic script (not inline) because the app's CSP is script-src 'self'.
// Keep the storage key in step with src/lib/theme.svelte.ts. 'system' (or nothing saved) = no attribute.
try {
  var t = localStorage.getItem('bs.theme')
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t)
} catch (e) {
  /* storage unavailable: follow the system */
}
