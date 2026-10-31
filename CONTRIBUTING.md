# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Licence of contributions

This project is licensed under the AGPL-3.0. By submitting a contribution you agree that it is licensed under
the same terms (AGPL-3.0-only).

## Dev setup (Windows)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd app
npm ci
npx tauri dev          # needs Rust (MSVC) and Node 22
```

Installer build steps are in [`packaging/README.md`](packaging/README.md).

## Checks before a pull request

```powershell
# Python (offline, no network)
.\.venv\Scripts\python.exe -m unittest discover tests

# Frontend: type check + production build (must have 0 errors and 0 warnings)
cd app
npm run build

# Rust shell
cd src-tauri
cargo check

# End-to-end smoke tests against the real sidecar in test mode (fake browsers, no GitHub traffic)
cd ..
npm run smoke
npm run data-smoke
```

## Guidelines

- Keep changes focused. Match the existing code style and the design tokens in `app/src/app.css`.
- The opener capability is https-only on purpose; do not widen it.
- Parser changes need a fixture in `tests/fixtures` and a test.
- Do not add donation prompts or telemetry to the app.
- Add new third-party dependencies sparingly and list them in `THIRD_PARTY_NOTICES.md`.
