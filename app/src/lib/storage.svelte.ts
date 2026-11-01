// Settings > Storage: where the data folder is, and moving it (storage.move / storage.reset).
//
// Flow: pick a folder -> dry run (nothing changes, the backend validates and says copy or switch) -> the user
// confirms from -> to -> storage.move copies, verifies and writes the pointer file -> the backend is restarted
// through the shell -> reconnect. The old folder is never touched by any of it.

import { api } from './client.ts'
import { RpcError } from './protocol.ts'
import type { MovePlan, StorageInfo } from './protocol.ts'
import { conn, discardConfigDraft, errMessage, restartBackend } from './app.svelte.ts'
import { inTauri, pickFolder } from './tauri.ts'

export type Step = 'idle' | 'checking' | 'confirm-move' | 'confirm-reset' | 'working' | 'restarting'

export const sto = $state({
  info: null as StorageInfo | null,
  /** full path of data.db (shown in the file manager by "Open folder") */
  dbPath: '',
  step: 'idle' as Step,
  plan: null as MovePlan | null,
  /** shown in the Storage card until the next action */
  error: '',
  /** success text after a change ('' = none) */
  done: '',
  /** the backend must be restarted by hand (plain browser dev harness: there is no shell to do it) */
  needsRestart: false,
  warnings: [] as string[],
})

export function busy(): boolean {
  return sto.step === 'checking' || sto.step === 'working' || sto.step === 'restarting'
}

/** Re-read app.info (path, size, source). Called when the screen opens and after every change. */
export async function refreshStorage(): Promise<void> {
  try {
    const info = await api.call('app.info')
    sto.info = info.storage
    sto.dbPath = info.db_path
    conn.dataDir = info.data_dir
  } catch (e) {
    sto.error = errMessage(e)
  }
}

function clearMessages(): void {
  sto.error = ''
  sto.done = ''
  sto.warnings = []
}

export function cancelChange(): void {
  if (busy()) return
  sto.step = 'idle'
  sto.plan = null
}

/** "Change location…": native folder picker, then a dry run; the confirm step shows what would happen. */
export async function chooseFolder(): Promise<void> {
  if (busy()) return
  clearMessages()
  let dir: string | null
  try {
    dir = await pickFolder({ title: 'Choose the new data folder', defaultPath: conn.dataDir || undefined })
  } catch (e) {
    sto.error = `Could not open the folder dialog: ${errMessage(e)}`
    return
  }
  if (!dir) return
  sto.step = 'checking'
  try {
    sto.plan = await api.call('storage.move', { path: dir, dry_run: true })
    sto.step = 'confirm-move'
  } catch (e) {
    sto.error = errMessage(e)
    sto.step = 'idle'
  }
}

export function askReset(): void {
  if (busy()) return
  clearMessages()
  sto.step = 'confirm-reset'
}

/** After the backend agreed to the change: restart it through the shell and reconnect. */
async function restartAndReconnect(): Promise<void> {
  discardConfigDraft() // the config of the new folder is loaded fresh; the old draft must not be autosaved onto it
  if (!inTauri) {
    sto.needsRestart = true
    sto.step = 'idle'
    return
  }
  sto.step = 'restarting'
  await restartBackend() // reports problems through conn.error
  if (conn.error) {
    sto.error = `The new location is saved, but the backend did not restart: ${conn.error}`
    sto.step = 'idle'
    return
  }
  try {
    await api.waitOpen(30_000)
  } catch (e) {
    sto.error = `The new location is saved, but reconnecting failed: ${errMessage(e)}`
    sto.step = 'idle'
    return
  }
  await refreshStorage()
  sto.step = 'idle'
}

export async function confirmMove(): Promise<void> {
  const plan = sto.plan
  if (!plan || sto.step !== 'confirm-move') return
  sto.step = 'working'
  sto.error = ''
  try {
    const r = await api.call('storage.move', { path: plan.to })
    sto.warnings = r.warnings ?? []
    sto.done =
      r.mode === 'switch'
        ? `Now using the data that was already in ${r.to}. Your previous data stays in ${r.from}.`
        : `Data copied to ${r.to} and now in use. Your previous data stays in ${r.from}; you can delete it yourself once you have checked.`
  } catch (e) {
    sto.error = e instanceof RpcError ? e.message : errMessage(e)
    sto.step = 'idle'
    sto.plan = null
    await refreshStorage()
    return
  }
  sto.plan = null
  await restartAndReconnect()
}

export async function confirmReset(): Promise<void> {
  if (sto.step !== 'confirm-reset') return
  sto.step = 'working'
  sto.error = ''
  try {
    const r = await api.call('storage.reset')
    sto.done = `Back on the default folder ${r.data_dir}. The data in ${r.kept} is kept, not deleted or moved.`
  } catch (e) {
    sto.error = errMessage(e)
    sto.step = 'idle'
    await refreshStorage()
    return
  }
  await restartAndReconnect()
}
