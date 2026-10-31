# Third-party notices

Branch Scrapper itself is licensed under the GNU Affero General Public License v3.0 (see `LICENSE`).
The Windows installer also contains the third-party components listed below, each under its own licence.

How this list was made: licences were read from each package's own metadata (Python: the installed
`.dist-info` metadata in the project's `.venv`; Rust: the `license` field in the crate's `Cargo.toml` in the
local cargo registry; npm: the `license` field in `package.json`). Where metadata only carried a licence
*classifier* rather than a full SPDX expression, that is stated. Full licence texts ship with the respective
upstream packages; this file does not reproduce them.

## Python sidecar (frozen with PyInstaller)

Direct runtime dependencies (`requirements.txt`):

| Package | Version | Licence (from metadata) |
|---|---|---|
| zendriver | 0.17.1 | AGPL-3.0 (licence text in metadata; classifier "GNU Affero General Public License v3") |
| psutil | 7.2.2 | BSD-3-Clause |
| beautifulsoup4 | 4.15.0 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| rich | 15.0.0 | MIT |
| questionary | 2.1.1 | MIT |

Transitive packages that the PyInstaller analysis (`build/pyinstaller/.../PYZ-00.toc`) shows were bundled:

| Package | Version | Licence (from metadata) |
|---|---|---|
| asyncio-atexit | 1.0.1 | MIT |
| attrs | 26.1.0 | MIT |
| charset-normalizer | 3.5.2 | MIT |
| cssselect | 1.6.0 | BSD-3-Clause |
| deprecated | 3.0.1 | MIT |
| emoji | 2.16.0 | BSD (classifier only; see upstream for the exact variant) |
| grapheme | 0.6.0 | MIT |
| markdown-it-py | 4.2.0 | MIT (classifier only) |
| mdurl | 0.1.2 | MIT (classifier only) |
| mss | 10.2.0 | MIT |
| prompt_toolkit | 3.0.53 | BSD (classifier only; see upstream for the exact variant) |
| pygments | 2.21.0 | BSD-2-Clause |
| pyreadline3 | 3.5.6 | BSD (see upstream for the exact variant) |
| soupsieve | 3.0.3 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| wcwidth | 0.9.3 | MIT (classifier only) |
| websockets | 17.3 | BSD-3-Clause |
| wrapt | 2.5.2 | BSD-2-Clause |

Runtime and build tooling:

| Component | Licence |
|---|---|
| CPython 3.12 (interpreter and standard library, bundled by PyInstaller) | Python Software Foundation License (PSF) |
| PyInstaller 6.22.3 bootloader (the `ghscraper-sidecar.exe` launcher) | GPL-2.0-or-later with a special exception that allows using PyInstaller to build and distribute non-free programs (wording taken from the package metadata) |

Zendriver is the reason Branch Scrapper is AGPL-3.0: the installer distributes it, so the combined work is
offered under the same licence. Its source is available at https://github.com/cdpdriver/zendriver.

## Desktop shell (Rust, Tauri 2)

Direct dependencies from `app/src-tauri/Cargo.toml`, resolved versions from `Cargo.lock`:

| Crate | Version | Licence (from Cargo.toml) |
|---|---|---|
| tauri | 2.12.5 | Apache-2.0 OR MIT |
| tauri-build (build-time) | 2.7.4 | Apache-2.0 OR MIT |
| tauri-plugin-log | 2.10.1 | Apache-2.0 OR MIT |
| tauri-plugin-dialog | 2.8.4 | Apache-2.0 OR MIT |
| tauri-plugin-opener | 2.7.2 | Apache-2.0 OR MIT |
| serde | 1.0.229 | MIT OR Apache-2.0 |
| serde_json | 1.0.154 | MIT OR Apache-2.0 |
| log | 0.4.35 | MIT OR Apache-2.0 |
| getrandom | 0.4.3 | MIT OR Apache-2.0 |

The shell also statically links the transitive crates listed in `app/src-tauri/Cargo.lock`; their licences
are not enumerated here (see upstream, or run a tool such as `cargo-license` against the lockfile).

The installer does not contain Microsoft Edge WebView2; the installer downloads the WebView2 bootstrapper on
machines that lack it, under Microsoft's own terms.

## Frontend (Svelte 5, bundled by Vite)

| Package | Version | Licence (from package.json) |
|---|---|---|
| svelte | 5.57.3 | MIT |
| @tauri-apps/api | 2.12.4 | Apache-2.0 OR MIT |
| @tauri-apps/plugin-dialog | 2.8.4 | MIT OR Apache-2.0 |
| @tauri-apps/plugin-opener | 2.7.2 | MIT OR Apache-2.0 |

Vite, TypeScript and the other development tools are build-time only and are not shipped.

## Not bundled

Google Chrome, Microsoft Edge or Brave is **not** part of the installer; Branch Scrapper drives a browser that
is already installed on the user's machine, under that browser's own licence.
