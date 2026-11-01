# Building the Windows installer

Result: `Branch Scrapper_0.1.2_x64-setup.exe` (plus `Branch Scrapper_0.1.2_x64-setup.exe.sig`, the updater
signature), a per-user NSIS installer (no admin rights). It carries the Tauri
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

# 3. Frontend deps, then the installer. The updater signing key must be in the environment (see "Releases and
#    in-app updates" below); without it `tauri build` fails because bundle.createUpdaterArtifacts is on.
cd app
npm ci
$d = "$env:USERPROFILE\.branch-scrapper-signing"
$env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content "$d\updater.key" -Raw).Trim()
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content "$d\updater.key.password" -Raw).Trim()
npx tauri build
```

`npx tauri build` runs `npm run build` (type check + Vite) itself, compiles the Rust shell in release mode,
bundles `app\src-tauri\sidecar\` as resources and runs NSIS. **Run step 2 again whenever Python code changes**;
`tauri build` does not rebuild the sidecar. Without step 2 the installer builds but the app cannot start its
backend (it says so, with the log path).

## Outputs

| What | Path | Size (measured) |
|---|---|---|
| Installer | `app\src-tauri\target\release\bundle\nsis\Branch Scrapper_0.1.2_x64-setup.exe` (+ `.sig`) | 19.2 MiB (20147509 bytes, 0.1.2; 18.1 MiB in 0.1.1: the updater adds an HTTP client and TLS) |
| Frozen sidecar (onedir) | `build\sidecar-dist\branch-scrapper-sidecar\` | 43.9 MB, 138 files |
| Installed footprint | install dir (default `%LOCALAPPDATA%\Branch Scrapper`) | 51.3 MB, 141 files |
| Shell exe | `app\src-tauri\target\release\branch-scrapper.exe` | 9.9 MB |

(Installer and staged sidecar re-measured for v0.1.1: 18985079 bytes = 18.11 MiB, 43.9 MB in 138 files. The installed
footprint and shell exe figures are carried over from v0.1.0 and were not re-measured.)

`build\`, `dist\`, `app\src-tauri\target\` and the staged sidecar are git-ignored.

## Releases and in-app updates

The app updates itself with the Tauri updater plugin (`tauri-plugin-updater` 2.x; our commands are in
`app\src-tauri\src\updates.rs`, the UI in `app\src\lib\updates.svelte.ts`, Settings > Updates and the update bar).

**How it works.** The installed app downloads
`https://github.com/abhishekairan/Branch-Scrapper/releases/latest/download/latest.json` (the endpoint in
`tauri.conf.json > plugins > updater`), compares `version` with its own and, if newer, offers it. On "Install &
restart" it downloads the `url` in that file, **verifies the minisign signature against the public key embedded in
`tauri.conf.json`** (and, with `requireSignedVersion`, that the signature was made for the announced version), stops
its backend, then starts the NSIS installer in passive mode (`/P /UPDATE /R`: progress bar, no questions, relaunch
when done). An installer that fails the signature check is never started. The installer replaces the program files
in place; nothing in `%APPDATA%\BranchScrapper` is touched. (The plugin unpacks the installer to
`%TEMP%\Branch Scrapper-<version>-updater-<random>` and never removes it, because the process exits right after
starting it; `updates::cleanup_leftovers` deletes such folders older than two minutes at the next start.)

**What a release consists of.** `latest/download/` only resolves to the newest **non-draft, non-prerelease** GitHub
release, so every such release must carry both:

| Asset | Made by |
|---|---|
| `BranchScrapper_<version>_x64-setup.exe` (no space in the name) | `tauri build`, renamed by CI |
| `latest.json` | `packaging\make_latest_json.py` (CI) |

CI (`.github/workflows/build-windows.yml`, on a `v*` tag or by hand) also puts the `.sig` and a `.sha256` into the
artifact. It does not publish anything: download the `unsigned-installer` artifact (or `signed-installer` when
SignPath is on), review it, create the GitHub release `v<version>` with `docs\release-notes-v<version>.md` as the
text (fill in `__INSTALLER_SHA256__` from the `.sha256`), and upload the `.exe` and `latest.json` (the `.sig` is
optional; its content is inside `latest.json`). The winget manifests (`packaging\winget`) point at the same `.exe`.

`latest.json` is generated, not hand-written: `python packaging\make_latest_json.py --version 0.1.2 --sig
"Branch Scrapper_0.1.2_x64-setup.exe.sig" --notes docs\release-notes-v0.1.2.md --out latest.json`. Its `notes` are
the release notes up to the "Install" heading (shown in the app's "What's new" dialog as plain text). Unit tests:
`tests\test_make_latest_json.py`.

**Keys.**

* Updater keypair (minisign), made once with `npx tauri signer generate`, lives **outside the repo** in
  `%USERPROFILE%\.branch-scrapper-signing\`: `updater.key` (private, password-protected), `updater.key.password`
  and `updater.key.pub`. The public key is embedded in `tauri.conf.json` (`plugins.updater.pubkey`); it is the
  content of `updater.key.pub`.
* The same private key and password are in the GitHub repository secrets `TAURI_SIGNING_PRIVATE_KEY` (the *contents*
  of `updater.key`, not a path) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. Local builds read them from the files into
  the environment variables of the same names (see step 3 above); never commit, print or paste them.
* **If the private key is lost, existing installs cannot auto-update.** The app only accepts updates signed with the
  key that matches the public key it was built with, so a new key means every installed copy must be reinstalled by
  hand once (a release signed with a new key is rejected by the old apps). **Back up
  `%USERPROFILE%\.branch-scrapper-signing\` (all three files) in a password manager.** If the key leaks, treat it
  like a lost key: anyone holding it can publish updates that installed apps accept.

**SignPath (Windows code signing, off until `SIGNPATH_ENABLED` is `true`).** Authenticode signing rewrites the
installer, so the `.sig` made at build time no longer matches it. When SignPath is enabled, the `sign` job signs the
code-signed exe again with `tauri signer sign --app-version <version>` (same secrets) and regenerates `latest.json`;
publish the files from the `signed-installer` artifact, not from `unsigned-installer`.

**Testing an update without publishing.** Set `BRANCH_SCRAPPER_UPDATE_URL` to the address of a `latest.json`
(for example from `python -m http.server`) before starting the app: it replaces the endpoint. The signature check
still applies, so only a build signed with our key installs. A release build accepts `https` addresses only; for
`http://127.0.0.1` a test build needs `plugins.updater.dangerousInsecureTransportProtocol: true` (never in the
shipped config). Installing a test build must not touch a real install: give the test build its own identifier
and name (`tauri build --config '{"productName":"...","identifier":"...","mainBinaryName":"..."}'`) and point
`GHOSTSCRAPER_HOME` at a scratch data folder.

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
& "Branch Scrapper_0.1.2_x64-setup.exe" /S /D=C:\path\to\install   # /D must be the last argument, no quotes
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
  (then `cargo check` refreshes `Cargo.lock`) and `app\package.json` / `package-lock.json`, and add
  `docs\release-notes-v<version>.md`. CI refuses to build when these disagree or the notes are missing.
* MSI is not built (`bundle.targets` is `["nsis"]`); WiX would be an extra download.
