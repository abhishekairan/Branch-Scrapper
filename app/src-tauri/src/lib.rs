mod sidecar;
mod updates;

use std::sync::Arc;
use std::time::Duration;

use sidecar::{Sidecar, SidecarInfo};
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_log::{Target, TargetKind};

/// Connection details for the webview. Waits (up to 25 s) while the sidecar is still starting.
#[tauri::command]
async fn sidecar_info(app: AppHandle) -> Result<SidecarInfo, String> {
  let sidecar = app.state::<Arc<Sidecar>>().inner().clone();
  tauri::async_runtime::spawn_blocking(move || sidecar.wait_info(Duration::from_secs(25)))
    .await
    .map_err(|e| e.to_string())?
}

/// Stop the sidecar (stdin close, 5 s grace, kill) and start a new one with a fresh token.
#[tauri::command]
async fn restart_sidecar(app: AppHandle) -> Result<SidecarInfo, String> {
  let sidecar = app.state::<Arc<Sidecar>>().inner().clone();
  let handle = app.clone();
  tauri::async_runtime::spawn_blocking(move || sidecar.restart(&handle))
    .await
    .map_err(|e| e.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  let app = tauri::Builder::default()
    .plugin(
      tauri_plugin_log::Builder::default()
        .level(log::LevelFilter::Info)
        .targets([
          Target::new(TargetKind::Stdout),
          Target::new(TargetKind::LogDir { file_name: Some("shell".into()) }),
        ])
        .build(),
    )
    .plugin(tauri_plugin_dialog::init())
    .plugin(tauri_plugin_opener::init())
    .plugin(tauri_plugin_updater::Builder::new().build())
    .manage(Sidecar::new())
    .manage(updates::UpdateState::default())
    .invoke_handler(tauri::generate_handler![
      sidecar_info,
      restart_sidecar,
      updates::app_version,
      updates::check_for_update,
      updates::install_update
    ])
    .setup(|app| {
      // Start in the background so the window appears immediately; `sidecar_info` waits for READY.
      let sidecar = app.state::<Arc<Sidecar>>().inner().clone();
      let handle = app.handle().clone();
      std::thread::Builder::new()
        .name("sidecar-start".into())
        .spawn(move || {
          let _ = sidecar.start(&handle);
        })?;
      let handle = app.handle().clone();
      std::thread::Builder::new()
        .name("update-cleanup".into())
        .spawn(move || updates::cleanup_leftovers(&handle))?;
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application");

  app.run(|handle, event| {
    if let RunEvent::Exit = event {
      handle.state::<Arc<Sidecar>>().stop();
    }
  });
}
