<script lang="ts">
  import { controlState } from '../lib/app.svelte.ts'
  import type { WorkerView } from '../lib/app.svelte.ts'
  import type { WorkerState } from '../lib/protocol.ts'

  let { worker }: { worker: WorkerView } = $props()

  const tone: Record<WorkerState, string> = {
    starting: 'info',
    searching: 'info',
    repo: 'accent',
    idle: '',
    paused: 'warn',
    recovering: 'warn',
    done: 'ok',
    stopped: '',
    failed: 'danger',
    other: '',
  }

  const ctl = $derived(controlState(worker.index))
  const label = $derived(ctl === 'active' ? 'paused by you' : ctl === 'pausing' ? 'pausing…' : worker.state)
  const cls = $derived(ctl !== 'off' ? 'warn' : tone[worker.state])
</script>

<span class="chip {cls}">{label}</span>
