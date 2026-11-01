"""Write ``latest.json``, the manifest the in-app updater reads (Tauri updater plugin, static JSON format).

    python packaging/make_latest_json.py --version 0.1.2 \\
        --sig "BranchScrapper_0.1.2_x64-setup.exe.sig" \\
        --notes docs/release-notes-v0.1.2.md --out latest.json

Used by CI (``.github/workflows/build-windows.yml``) and usable locally. Output::

    {"version": "0.1.2", "notes": "...", "pub_date": "2026-11-01T12:00:00Z",
     "platforms": {"windows-x86_64": {"signature": "<contents of the .sig file>",
                                      "url": "https://github.com/<repo>/releases/download/v0.1.2/BranchScrapper_0.1.2_x64-setup.exe"}}}

* ``url`` points at the release asset ``BranchScrapper_<version>_x64-setup.exe`` (no space; CI renames the
  installer Tauri built, ``Branch Scrapper_<version>_x64-setup.exe``, to that name before uploading it).
* ``signature`` is the **content** of the ``.sig`` file made by ``tauri signer sign`` over exactly the file that is
  uploaded as the asset. If the installer is code-signed afterwards (SignPath), the ``.sig`` must be made again over
  the signed file and this script run again, because the code signature changes the bytes.
* ``notes`` are the release notes shown in the app's "What's new" dialog: the notes file up to (not including) its
  "Install" heading, without ``SHA256:`` lines.
* Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REPO = "abhishekairan/Branch-Scrapper"
PLATFORM = "windows-x86_64"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
INSTALL_HEADING_RE = re.compile(r"^#{1,6}\s*install\b", re.IGNORECASE)
SHA_LINE_RE = re.compile(r"sha-?256", re.IGNORECASE)


class ManifestError(ValueError):
    """Something is missing or malformed; the message says what."""


def asset_name(version: str) -> str:
    """File name of the installer as uploaded to the GitHub release (no space)."""
    return f"BranchScrapper_{version}_x64-setup.exe"


def asset_url(version: str, repo: str = DEFAULT_REPO) -> str:
    return f"https://github.com/{repo}/releases/download/v{version}/{asset_name(version)}"


def check_version(version: str) -> str:
    version = version.strip().removeprefix("v")
    if not VERSION_RE.match(version):
        raise ManifestError(f"not a semantic version: {version!r}")
    return version


def clean_signature(text: str, version: str | None = None) -> str:
    """The signature exactly as the updater wants it: the .sig file content, one base64 string.

    The file is already base64 of a minisign signature file ("untrusted comment: ..." and the signature lines).
    Checked loosely so a wrong file (an empty one, the private key, the installer) is caught early. With `version`,
    the version recorded in the signed trusted comment (``version:0.1.2``, written by `tauri signer sign`) must be
    the same: the app (requireSignedVersion) rejects the update otherwise, so fail here instead.
    """
    sig = "".join(text.split())
    if not sig:
        raise ManifestError("the signature file is empty")
    try:
        decoded = base64.b64decode(sig, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as e:
        raise ManifestError(f"the signature is not base64 text from `tauri signer sign`: {e}") from None
    # A signature file has an untrusted AND a trusted comment line; a key file (which must never end up here) has
    # only the first.
    if not decoded.startswith("untrusted comment:") or "\ntrusted comment:" not in decoded:
        raise ManifestError("the signature does not look like a minisign signature (.sig from `tauri signer sign`)")
    if version is not None:
        trusted = decoded.split("\ntrusted comment:", 1)[1].split("\n", 1)[0]
        m = re.search(r"(?:^|\t|\s)version:(\S+)", trusted)
        if m is None:
            raise ManifestError("the signature carries no version (sign with `tauri signer sign --app-version`)")
        if m.group(1) != version:
            raise ManifestError(f"the signature was made for version {m.group(1)}, not {version}")
    return sig


def release_notes(text: str) -> str:
    """Notes for the update dialog: everything before the "Install" heading, minus SHA256 lines, trimmed."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if INSTALL_HEADING_RE.match(line.strip()):
            break
        if SHA_LINE_RE.search(line) and ("__INSTALLER_SHA256__" in line or re.search(r"[0-9a-f]{64}", line, re.I)):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def build_manifest(version: str, signature: str, notes: str, pub_date: datetime | None = None,
                   repo: str = DEFAULT_REPO) -> dict:
    version = check_version(version)
    when = (pub_date or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "version": version,
        "notes": notes,
        "pub_date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            PLATFORM: {"signature": clean_signature(signature, version), "url": asset_url(version, repo)},
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--version", required=True, help="app version, e.g. 0.1.2 (a leading v is accepted)")
    p.add_argument("--sig", required=True, type=Path, help="the .sig file of the installer")
    p.add_argument("--notes", type=Path, help="release notes markdown (default: none)")
    p.add_argument("--out", required=True, type=Path, help="where to write latest.json")
    p.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub owner/name (default {DEFAULT_REPO})")
    p.add_argument("--pub-date", help="RFC 3339 time, default: now (UTC)")
    args = p.parse_args(argv)
    try:
        version = check_version(args.version)
        signature = args.sig.read_text(encoding="utf-8")
        notes = release_notes(args.notes.read_text(encoding="utf-8")) if args.notes else ""
        when = datetime.fromisoformat(args.pub_date.replace("Z", "+00:00")) if args.pub_date else None
        manifest = build_manifest(version, signature, notes, when, args.repo)
    except (OSError, ValueError) as e:        # ManifestError is a ValueError
        print(f"make_latest_json: {e}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}: version {version}, {manifest['platforms'][PLATFORM]['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
