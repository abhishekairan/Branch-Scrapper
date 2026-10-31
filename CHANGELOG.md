# Changelog

All notable changes are documented here. Format loosely follows Keep a Changelog; versions follow SemVer.

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
