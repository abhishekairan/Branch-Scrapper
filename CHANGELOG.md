# Changelog

All notable changes are documented here. Format loosely follows Keep a Changelog; versions follow SemVer.

## [0.1.2] - Unreleased

Versions before 0.1.2 cannot update themselves: v0.1.1 users install 0.1.2 by hand once; automatic updates start
from 0.1.2.

### Added
- **In-app updates** (Tauri updater plugin, minisign-signed). The app checks GitHub's
  `releases/latest/download/latest.json` after the backend connects, at most once per 24 h, unless turned off in
  **Settings > Updates** (also: current version, last-checked time, **Check now**, result). A bar under the status
  bar offers **What's new** (release notes as plain text), **Install & restart** and **Later**; nothing installs
  without a click, and not while a run is active. Install = download with progress, signature check, clean
  shutdown of the backend (its Chromes end with it), then the NSIS installer in passive mode, which relaunches the
  app. Offline, rate-limit and signature errors give a calm message and never block the app. Data is untouched.
  - The shell does this itself (`src-tauri/src/updates.rs`: `check_for_update`, `install_update`); the endpoint can
    be replaced for tests with `BRANCH_SCRAPPER_UPDATE_URL` (the signature check still applies).
  - Config `check_updates` (bool, default true), kept in localStorage and `config.json` like `ui_theme`.
  - `requireSignedVersion` is on: an update is rejected unless its signature was made for the announced version.
- **Release pipeline:** `tauri build` produces the `.sig` (`bundle.createUpdaterArtifacts`); the workflow renames the
  installer to `BranchScrapper_<version>_x64-setup.exe`, generates `latest.json` with `packaging/make_latest_json.py`
  (unit-tested) and, with SignPath enabled, re-signs the code-signed installer for the updater. See
  `packaging/README.md`.
- **Settings screen:** System / Light / Dark theme (title bar follows) and data folder viewing and moving
  (copy, check, switch; the old folder is kept).
- **Grid options** on the Browsers screen: columns (Auto, 1-4), tile size (Compact, Comfortable, Large), and
  showing the page URL, status caption and RAM.

### Changed
- **Single view:** the live preview is letterboxed to fit its area at the browser window's aspect ratio and is
  requested at the size it is drawn (it no longer overflows or stretches when the window is resized).
- **Windows taskbar:** the scraper's headed, off-screen Chrome windows are kept out of the taskbar and Alt-Tab
  (`hide_taskbar_icons`, default on; `--show-taskbar-icons` turns it off).

## [0.1.1] - Unreleased

### Changed
- **The product is now called Branch Scrapper** (it was "Ghost Scraper" in v0.1.0). New window title, installer
  name (`Branch Scrapper_0.1.1_x64-setup.exe`), executable (`branch-scrapper.exe`), sidecar
  (`branch-scrapper-sidecar.exe`), app identifier (`com.branchscrapper.desktop`) and default data folder
  (`%APPDATA%\BranchScrapper`). The Python package (`ghscraper`) and the `GHOSTSCRAPER_*` environment variable
  names are unchanged.
- Exported file names are now `branch-scrapper-<scope>-<date>.<json|csv>`.

### Added
- **Data migration:** on first start the sidecar copies `%APPDATA%\GhostScraper` to `%APPDATA%\BranchScrapper`
  (copy only, SQLite WAL checkpointed first, atomic rename, nothing deleted; a `MIGRATED_TO.txt` note is left
  in the old folder). A one-time dismissible notice on the Run screen reports it; `app.info` has a new
  `migration` field. Not done when `GHOSTSCRAPER_HOME` or `--data-dir` is set.
- **Installer upgrade:** the installer silently uninstalls a per-user Ghost Scraper installation before
  installing (`bundle.windows.nsis.installerHooks`), keeping its data.

- Promo video with a synthesized soundtrack (`marketing/video/`), embedded in the README.

### Known changes
- The new app identifier gives the app a new WebView2 profile, so UI preferences kept in the browser storage
  (for example the Browsers layout) are reset once.

## [0.1.0] - Unreleased (first public release)

First desktop release. Builds on the earlier command-line scraper (SeleniumBase UC mode).

### Added
- Tauri 2 + Svelte 5 desktop app with three screens:
  - **Run**: queries and filters with validation, preview of match counts, start/stop, progress and log.
  - **Browsers**: live tiles (grid, single, list layouts) that stream only while visible; Take control to
    click and type in a worker's browser.
  - **Data**: full-text search (SQLite FTS5), filters, saved datasets, detail drawer, JSON/CSV export, import
    of JSON results from the old CLI.
- **About** screen with version, licence, project links and optional support links.
- Python sidecar with a token-authenticated localhost WebSocket API, spawned and supervised by the shell.
- SQLite store with a filter compiler; one row per repo (latest scrape wins).
- Windows per-user NSIS installer (PyInstaller onedir sidecar; no Python required). Measured: installer
  18.1 MiB, installed 51.3 MB.
- `LICENSE` (AGPL-3.0) and `THIRD_PARTY_NOTICES.md` shipped with the installer.

### Changed
- Browser engine: SeleniumBase replaced by Zendriver 0.17.1 behind a `BrowserSlot`/`BrowserPool` interface,
  with an asyncio scheduler.
- Workers are browser contexts shared inside few Chrome processes (less RAM); isolated Chrome per worker
  remains an option.
- Chrome windows are headed but parked off-screen; Chrome processes are placed in a Windows job object so they
  are killed when the app exits.
- CSV export guards against spreadsheet formula injection.
- Licence is AGPL-3.0 (Zendriver is AGPL-3.0 and is bundled in the installer).

### Known limitations
- Only Windows is tested. Linux and macOS are not supported yet.
- Not yet tested: recovery from a real Chrome crash, logged-in Chrome profiles, Edge and Brave.
- The installer is unsigned; Windows SmartScreen warns on first run.
