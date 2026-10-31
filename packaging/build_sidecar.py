"""Freeze the sidecar with PyInstaller and stage it for the Tauri bundle.

    .venv\\Scripts\\python.exe packaging\\build_sidecar.py

Output:
  build/sidecar-dist/branch-scrapper-sidecar/  PyInstaller onedir result (git-ignored)
  app/src-tauri/sidecar/                     the same files staged for `tauri build` (git-ignored except README.txt;
                                             see tauri.conf.json bundle.resources)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "branch-scrapper-sidecar.spec"
WORK = ROOT / "build" / "pyinstaller"
DIST = ROOT / "build" / "sidecar-dist"
STAGE = ROOT / "app" / "src-tauri" / "sidecar"
NAME = "branch-scrapper-sidecar"
KEEP = "README.txt"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed in this Python; run: pip install -r requirements-build.txt",
              file=sys.stderr)
        return 1

    for d in (WORK, DIST):
        shutil.rmtree(d, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--workpath", str(WORK), "--distpath", str(DIST), str(SPEC)]
    print("+", " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        return 1

    built = DIST / NAME
    exe = built / f"{NAME}.exe" if sys.platform == "win32" else built / NAME
    if not exe.is_file():
        print(f"expected {exe} was not produced", file=sys.stderr)
        return 1

    # Stage the *contents* of the onedir folder into app/src-tauri/sidecar/, so the installed layout is
    # <resource_dir>/sidecar/branch-scrapper-sidecar.exe. The tracked README.txt placeholder is kept: Tauri's
    # build script needs the resources glob to match something even in dev builds.
    STAGE.mkdir(parents=True, exist_ok=True)
    for entry in STAGE.iterdir():
        if entry.name == KEEP:
            continue
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    for entry in built.iterdir():
        dest = STAGE / entry.name
        shutil.copytree(entry, dest) if entry.is_dir() else shutil.copy2(entry, dest)
    files = [f for f in STAGE.rglob("*") if f.is_file() and f.name != KEEP]
    print(f"staged {STAGE} ({sum(f.stat().st_size for f in files) / 1e6:.1f} MB, {len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
