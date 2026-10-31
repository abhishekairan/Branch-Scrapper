# Building the Windows installer

Result: `Branch Scrapper_0.1.1_x64-setup.exe`, a per-user NSIS installer (no admin rights). It carries the Tauri
shell and the Python sidecar frozen with PyInstaller (onedir), so the target machine needs no Python.
The installed app needs Google Chrome, Microsoft Edge or Brave (Edge ships with Windows) and WebView2
(preinstalled on Windows 11; the installer downloads the bootstrapper if it is missing).

## Prerequisites (build machine)

* Windows 10/11 x64, Git Bash or PowerShell
* Python 3.12 (a venv in the repo root)
* Rust (stable, MSVC target) with the Visual Studio Build Tools "Desktop development with C++" workload
* Node 22
* Internet on the first `tauri build`: it downloads NSIS 3.13 and `nsis_tauri_utils.dll` from GitHub into
  `%LOCALAPPDATA%\tauri`

## Steps (from a clean checkout, repo root `V1/`)

```powershell
# 1. Python env + requirements (runtime and build)
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt      # includes requirements.txt + pyinstaller

# 2. Freeze the sidecar -> build\sidecar-dist\branch-scrapper-sidecar\ and stage it in app\src-tauri\sidecar\
.venv\Scripts\python.exe packaging\build_sidecar.py

# 3. Frontend deps, then the installer
cd app
npm ci
npx tauri build
```

`npx tauri build` runs `npm run build` (type check + Vite) itself, compiles the Rust shell in release mode,
bundles `app\src-tauri\sidecar\` as resources and runs NSIS. **Run step 2 again whenever Python code changes**;
`tauri build` does not rebuild the sidecar. Without step 2 the installer builds but the app cannot start its
backend (it says so, with the log path).

## Outputs

| What | Path | Size (measured) |
|---|---|---|
| Installer | `app\src-tauri\target\release\bundle\nsis\Branch Scrapper_0.1.1_x64-setup.exe` | 18.1 MiB |
| Frozen sidecar (onedir) | `build\sidecar-dist\branch-scrapper-sidecar\` | 43.9 MB, 138 files |
| Installed footprint | install dir (default `%LOCALAPPDATA%\Branch Scrapper`) | 51.3 MB, 141 files |
| Shell exe | `app\src-tauri\target\release\branch-scrapper.exe` | 9.9 MB |

(Installer and staged sidecar re-measured for v0.1.1: 18985079 bytes = 18.11 MiB, 43.9 MB in 138 files. The installed
footprint and shell exe figures are carried over from v0.1.0 and were not re-measured.)

`build\`, `dist\`, `app\src-tauri\target\` and the staged sidecar are git-ignored.

## Layout of an install

```
<install dir>\branch-scrapper.exe          Tauri shell
<install dir>\sidecar\branch-scrapper-sidecar.exe  + _internal\   frozen Python sidecar (spawned by the shell)
<install dir>\uninstall.exe
```

Per-user data (kept on uninstall):

* `%APPDATA%\BranchScrapper\` : `data.db`, `config.json`, `logs\sidecar.log`
* `%LOCALAPPDATA%\com.branchscrapper.desktop\logs\shell.log` : shell log, also contains the sidecar's stdout/stderr
* `%LOCALAPPDATA%\com.branchscrapper.desktop\EBWebView\` : WebView2 profile

### Upgrading from Ghost Scraper 0.1.0

* **Program files:** `app\src-tauri\installer-hooks.nsh` (`bundle.windows.nsis.installerHooks`) runs in
  `NSIS_HOOK_PREINSTALL`: if `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Ghost Scraper` exists, its
  uninstaller is run silently (`/S _?=<install dir>`, waited for) before the new files are copied. That keeps
  `%APPDATA%\GhostScraper`.
* **Data:** on its first start the sidecar copies `%APPDATA%\GhostScraper` to `%APPDATA%\BranchScrapper`
  (`ghscraper.config.migrate_legacy_data_dir`). It copies, never moves or deletes, and drops a `MIGRATED_TO.txt`
  note into the old folder. Not done when `GHOSTSCRAPER_HOME` or `--data-dir` is set.
* The new identifier gives a new WebView2 profile, so UI preferences (layout etc.) reset once.

## Silent install / uninstall

```powershell
& "Branch Scrapper_0.1.1_x64-setup.exe" /S /D=C:\path\to\install   # /D must be the last argument, no quotes
& "C:\path\to\install\uninstall.exe" /S
```

## Notes

* **Unsigned.** Windows SmartScreen shows "Windows protected your PC" on first run: More info, Run anyway.
  Signing needs a code-signing certificate (not set up).
* **onedir, not onefile.** onefile unpacks to `%TEMP%` on every start and adds a bootloader parent process,
  which is slower and more often flagged by antivirus; onedir starts in ~0.3 s. If a machine's antivirus still
  quarantines `branch-scrapper-sidecar.exe`, add an exclusion for the install folder.
* The packaged sidecar refuses `--test-mode`, and `ghscraper.api.testing` (the fake browser pool) is excluded.
* Dev builds (`cargo`/`tauri dev`) still run `.venv\Scripts\python.exe -m ghscraper.sidecar`;
  `GHOSTSCRAPER_SIDECAR_CMD` works in debug builds only.
* Updating the version: bump it in `ghscraper\__init__.py`, `app\src-tauri\tauri.conf.json`, `Cargo.toml`
  (then `cargo check` refreshes `Cargo.lock`) and `app\package.json` / `package-lock.json`.
* MSI is not built (`bundle.targets` is `["nsis"]`); WiX would be an extra download.
