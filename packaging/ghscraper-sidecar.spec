# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Ghost Scraper sidecar. Build with packaging/build_sidecar.py (not by hand).
#
# onedir, not onefile: onefile unpacks ~100 MB to %TEMP% on every launch (seconds of startup) and runs a
# bootloader *parent* process, which is a classic antivirus false-positive trigger and would sit between
# Tauri and the real sidecar (breaking "kill the PID from READY" and the Job Object story). onedir starts
# in place with a single process.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# The fake browser pool serves test fixtures from a source checkout; it must not exist in the shipped app.
TEST_ONLY = ["ghscraper.api.testing"]

hiddenimports = (
    # zendriver loads CDP domain modules by name at runtime (zendriver.core.util.cdp_get_module)
    collect_submodules("zendriver")
    # everything of ours, incl. modules imported lazily inside functions
    + [m for m in collect_submodules("ghscraper") if m not in TEST_ONLY]
    # zendriver's third-party deps
    + collect_submodules("websockets")
    + ["emoji", "grapheme", "mss", "asyncio_atexit", "deprecated", "psutil"]
)

datas = (
    [(os.path.join(ROOT, "ghscraper", "store", "schema.sql"), "ghscraper/store")]
    + collect_data_files("emoji")      # the emoji database (json) is read lazily
    + collect_data_files("grapheme")   # grapheme-break property tables
    + collect_data_files("zendriver")
)

excludes = TEST_ONLY + [
    "tkinter", "_tkinter", "turtle", "idlelib", "lib2to3", "pydoc_data", "test", "tests", "pytest", "_pytest",
    # the legacy Selenium stack that shares the dev venv; the sidecar never imports it
    "selenium", "seleniumbase", "sbvirtualdisplay", "trio", "trio_websocket", "mycdp", "behave", "pynose",
    "requests", "urllib3", "pdbp", "parameterized",
    "numpy", "pandas", "matplotlib", "IPython", "jupyter",
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "sidecar_entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir
    name="ghscraper-sidecar",
    debug=False,
    strip=False,
    upx=False,                  # UPX-packed binaries trigger antivirus heuristics
    console=True,               # Tauri spawns it with CREATE_NO_WINDOW and needs stdin/stdout/stderr
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "app", "src-tauri", "icons", "icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ghscraper-sidecar",
)
