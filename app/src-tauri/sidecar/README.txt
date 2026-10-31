This folder is bundled into the installer as <resource dir>/sidecar/.

It is filled by `python packaging/build_sidecar.py` (the PyInstaller-frozen Python sidecar,
branch-scrapper-sidecar.exe plus its _internal folder). Everything here except this file is git-ignored.
Run that script before `npx tauri build`; without it the installed app cannot start its backend.
