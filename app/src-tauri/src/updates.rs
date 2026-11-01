//! In-app updates (Tauri updater plugin, driven from our own commands).
//!
//! Why our own commands instead of the plugin's JS API:
//! * the update endpoint can be replaced at run time with `BRANCH_SCRAPPER_UPDATE_URL` (used by the update tests).
//!   The signature check against the public key embedded in `tauri.conf.json` still applies, so a replaced
//!   endpoint cannot make the app install anything that was not signed with our private key;
//! * the Python sidecar must be stopped cleanly *before* the installer starts. On Windows `Update::install`
//!   launches the NSIS installer and then ends this process with `std::process::exit(0)`, which skips
//!   `RunEvent::Exit` (where the sidecar is normally stopped).
//!
//! Flow: `check_for_update` keeps the found `Update` in `UpdateState`; `install_update` downloads it (the plugin
//! verifies the minisign signature when the download completes), stops the sidecar, then installs. The installer
//! runs in passive mode (progress bar, no questions) and relaunches the app itself (`/R`).
//! The plugin's own JS-facing commands (`plugin:updater|*`) are registered but no capability grants them.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, State, Url};
use tauri_plugin_updater::{Error as UpdaterError, Update, UpdaterExt};

use crate::sidecar::Sidecar;

/// Replaces the update endpoint of `tauri.conf.json` (tests; the signature check still applies).
const ENDPOINT_ENV: &str = "BRANCH_SCRAPPER_UPDATE_URL";
const CHECK_TIMEOUT: Duration = Duration::from_secs(20);
/// A stalled connection must not hang the update forever; this is the limit for the whole download.
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(15 * 60);

#[derive(Default)]
pub struct UpdateState {
    /// The update found by the last check (`None` when up to date or not checked yet).
    pending: Mutex<Option<Update>>,
    /// An install is in progress; a second one is refused.
    installing: Mutex<bool>,
}

/// What the UI shows about an update. `body` is the release notes and is **untrusted text**: the UI renders it
/// as plain text only.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub current_version: String,
    pub date: Option<String>,
    pub body: Option<String>,
}

/// Progress of `install_update`, sent over a channel. `phase`: `downloading`, `stopping` (the backend is being
/// shut down) or `installing` (the installer is about to start; the app exits right after).
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallEvent {
    pub phase: &'static str,
    pub downloaded: u64,
    pub total: Option<u64>,
}

/// Version of the running shell (from `tauri.conf.json`).
#[tauri::command]
pub fn app_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

/// Ask the update endpoint whether a newer version exists. `Ok(None)` = up to date. Errors are calm,
/// user-readable sentences.
#[tauri::command]
pub async fn check_for_update(
    app: AppHandle,
    state: State<'_, UpdateState>,
) -> Result<Option<UpdateInfo>, String> {
    let mut builder = app.updater_builder().timeout(CHECK_TIMEOUT);
    if let Some(url) = endpoint_override()? {
        log::warn!("update check against {url} ({ENDPOINT_ENV} is set)");
        builder = builder.endpoints(vec![url]).map_err(friendly)?;
    }
    let updater = builder.build().map_err(friendly)?;
    match updater.check().await {
        Ok(Some(update)) => {
            log::info!("update available: {} (running {})", update.version, update.current_version);
            let info = UpdateInfo {
                version: update.version.clone(),
                current_version: update.current_version.clone(),
                date: update.date.map(|d| d.to_string()),
                body: update.body.clone(),
            };
            *state.pending.lock().unwrap() = Some(update);
            Ok(Some(info))
        }
        Ok(None) => {
            log::info!("no update available");
            *state.pending.lock().unwrap() = None;
            Ok(None)
        }
        Err(e) => Err(friendly(e)),
    }
}

/// Download the update found by `check_for_update`, stop the sidecar, run the installer. On success this does
/// not return: the installer is started and the process exits (the installer relaunches the app). On failure
/// the sidecar is started again so the app stays usable.
#[tauri::command]
pub async fn install_update(
    app: AppHandle,
    state: State<'_, UpdateState>,
    on_event: Channel<InstallEvent>,
) -> Result<(), String> {
    let update = state
        .pending
        .lock()
        .unwrap()
        .clone()
        .ok_or("There is no update to install. Check for updates first.")?;
    {
        let mut busy = state.installing.lock().unwrap();
        if *busy {
            return Err("An update is already being installed.".into());
        }
        *busy = true;
    }
    let result = download_and_install(&app, update, &on_event).await;
    *state.installing.lock().unwrap() = false;
    result
}

async fn download_and_install(
    app: &AppHandle,
    mut update: Update,
    on_event: &Channel<InstallEvent>,
) -> Result<(), String> {
    update.timeout = Some(DOWNLOAD_TIMEOUT);
    let send = |phase: &'static str, downloaded: u64, total: Option<u64>| {
        let _ = on_event.send(InstallEvent { phase, downloaded, total });
    };

    // 1. download + signature check (the sidecar keeps running meanwhile)
    send("downloading", 0, None);
    let mut downloaded: u64 = 0;
    let bytes = update
        .download(
            |chunk, total| {
                downloaded += chunk as u64;
                send("downloading", downloaded, total);
            },
            || {},
        )
        .await
        .map_err(friendly)?;
    log::info!("update {} downloaded ({} bytes) and verified", update.version, bytes.len());

    // 2. stop the sidecar: stdin closed, it stops its run and exits; Chrome dies with its job object
    send("stopping", downloaded, None);
    let sidecar = app.state::<Arc<Sidecar>>().inner().clone();
    {
        let sidecar = sidecar.clone();
        tauri::async_runtime::spawn_blocking(move || sidecar.stop())
            .await
            .map_err(|e| e.to_string())?;
    }

    // 3. install: on Windows this starts the installer and exits the process
    send("installing", downloaded, None);
    let installing = update.clone();
    let outcome = tauri::async_runtime::spawn_blocking(move || installing.install(&bytes))
        .await
        .map_err(|e| e.to_string())
        .and_then(|r| r.map_err(friendly));

    // Only reached when the installer could not be started: bring the backend back.
    let message = outcome.err().unwrap_or_else(|| "The installer did not start.".into());
    log::error!("update install failed: {message}");
    let handle = app.clone();
    let _ = tauri::async_runtime::spawn_blocking(move || sidecar.restart(&handle)).await;
    Err(message)
}

/// `Update::install` unpacks the installer to `%TEMP%\<app>-<version>-updater-<random>` and the process exits right
/// after starting it, so nothing ever removes that folder (about 20 MB per update). Called at start-up: remove the
/// folders earlier updates left behind. Best effort: a folder younger than two minutes is left alone (the installer
/// that just upgraded us may still be running from it), and one that cannot be removed is skipped.
pub fn cleanup_leftovers(app: &AppHandle) {
    let prefix = format!("{}-", app.package_info().name);
    let Ok(entries) = std::fs::read_dir(std::env::temp_dir()) else { return };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !(name.starts_with(&prefix) && name.contains("-updater-")) {
            continue;
        }
        let path = entry.path();
        let old_enough = entry
            .metadata()
            .ok()
            .filter(|m| m.is_dir())
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.elapsed().ok())
            .is_some_and(|age| age > Duration::from_secs(120));
        if !old_enough {
            continue;
        }
        match std::fs::remove_dir_all(&path) {
            Ok(()) => log::info!("removed the leftover update folder {}", path.display()),
            Err(e) => log::info!("could not remove {}: {e}", path.display()),
        }
    }
}

fn endpoint_override() -> Result<Option<Url>, String> {
    match std::env::var(ENDPOINT_ENV) {
        Ok(v) if !v.trim().is_empty() => Url::parse(v.trim())
            .map(Some)
            .map_err(|e| format!("{ENDPOINT_ENV} is not a valid URL: {e}")),
        _ => Ok(None),
    }
}

/// A calm sentence for the UI; the technical error goes to the log.
fn friendly(e: UpdaterError) -> String {
    log::warn!("updater error: {e}");
    match e {
        UpdaterError::Reqwest(_) => {
            "Could not reach the update server. Check your internet connection and try again.".into()
        }
        UpdaterError::ReleaseNotFound => "The update server did not return usable release information \
            (it may be busy or rate-limited). Try again in a few minutes."
            .into(),
        UpdaterError::Network(msg) => format!("The update could not be downloaded ({msg}). Try again later."),
        UpdaterError::Minisign(_)
        | UpdaterError::Base64(_)
        | UpdaterError::SignatureUtf8(_)
        | UpdaterError::SignedVersionMismatch { .. }
        | UpdaterError::MissingSignedVersion => "The downloaded update failed its signature check, so it was \
            not installed. Your installed version is unchanged."
            .into(),
        UpdaterError::TargetNotFound(_) | UpdaterError::TargetsNotFound(_) => {
            "This release has no build for your system.".into()
        }
        UpdaterError::InsecureTransportProtocol => {
            "The update address must use https, so it was not used.".into()
        }
        other => format!("The update failed: {other}"),
    }
}
