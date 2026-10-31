// The app-wide sidecar client singleton (connection details come from the Tauri shell).

import { SidecarClient } from './api.ts'
import { getSidecarInfo } from './tauri.ts'

export const api = new SidecarClient({ getInfo: getSidecarInfo })
