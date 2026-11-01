"""packaging/make_latest_json.py: the manifest the in-app updater reads."""
import base64
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "make_latest_json.py"
_spec = importlib.util.spec_from_file_location("make_latest_json", SCRIPT)
mlj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mlj)

# the shape of a .sig written by `tauri signer sign`: base64 of a minisign signature file
SIG_TEXT = ("untrusted comment: signature from tauri secret key\n"
            "RUQuwvXVoUUhDHQq6Z0Y8mF5nE4Yw4J1y2v2Zl1c0Q3oV1m4k5l6m7n8o9p0q1r2s3t4u5v6w7x8y9z0A1B2C3D4\n"
            "trusted comment: timestamp:1793519319\tfile:Branch Scrapper_0.1.2_x64-setup.exe\tversion:0.1.2\n"
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKl==\n")
SIG = base64.b64encode(SIG_TEXT.encode()).decode()

NOTES = """# Branch Scrapper v0.1.2

Intro line.

## What's new

- **In-app updates.** Works.

## Install

1. Download it.

SHA256: __INSTALLER_SHA256__

## Known limitations

- Nothing.
"""


class ManifestTests(unittest.TestCase):
    def test_shape_url_and_asset_name(self):
        m = mlj.build_manifest("0.1.2", SIG, "notes", datetime(2026, 11, 1, 12, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(set(m), {"version", "notes", "pub_date", "platforms"})
        self.assertEqual(m["version"], "0.1.2")
        self.assertEqual(m["pub_date"], "2026-11-01T12:00:05Z")
        p = m["platforms"]["windows-x86_64"]
        self.assertEqual(p["url"], "https://github.com/abhishekairan/Branch-Scrapper/releases/download/"
                                   "v0.1.2/BranchScrapper_0.1.2_x64-setup.exe")
        self.assertNotIn(" ", p["url"])
        self.assertEqual(p["signature"], SIG)
        self.assertEqual(list(m["platforms"]), ["windows-x86_64"])

    def test_pub_date_is_converted_to_utc(self):
        from datetime import timedelta
        local = datetime(2026, 11, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(mlj.build_manifest("0.1.2", SIG, "", local)["pub_date"], "2026-11-01T12:00:00Z")

    def test_version_is_checked_and_a_leading_v_dropped(self):
        self.assertEqual(mlj.build_manifest("v0.1.2", SIG, "")["version"], "0.1.2")
        for bad in ("", "1.2", "latest", "0.1.2.3", "v"):
            with self.assertRaises(mlj.ManifestError, msg=bad):
                mlj.build_manifest(bad, SIG, "")

    def test_signature_is_whitespace_normalised(self):
        wrapped = "\n".join(SIG[i:i + 40] for i in range(0, len(SIG), 40)) + "\n"
        self.assertEqual(mlj.clean_signature(wrapped), SIG)

    def test_signature_must_be_for_the_same_version(self):
        with self.assertRaisesRegex(mlj.ManifestError, "made for version 0.1.2, not 0.1.3"):
            mlj.build_manifest("0.1.3", SIG, "")
        unversioned = base64.b64encode(SIG_TEXT.replace("\tversion:0.1.2", "").encode()).decode()
        with self.assertRaisesRegex(mlj.ManifestError, "no version"):
            mlj.build_manifest("0.1.2", unversioned, "")
        # a file name that merely contains the text "version:" must not be mistaken for the version field
        tricky = base64.b64encode(SIG_TEXT.replace("\tversion:0.1.2", "\tfile2:xversion:9.9.9").encode()).decode()
        with self.assertRaisesRegex(mlj.ManifestError, "no version"):
            mlj.build_manifest("0.1.2", tricky, "")

    def test_bad_signatures_are_rejected(self):
        secret = base64.b64encode(b"untrusted comment: rsign encrypted secret key\nRWRTY0Iy" + b"A" * 80 + b"\n").decode()
        for bad in ("", "  \n", "not base64 !!", base64.b64encode(b"MZ\x90\x00binary").decode(),
                    base64.b64encode(b"hello world").decode(), secret):
            with self.assertRaises(mlj.ManifestError, msg=bad[:20]):
                mlj.clean_signature(bad)


class NotesTests(unittest.TestCase):
    def test_cut_at_install_heading_and_no_sha_line(self):
        out = mlj.release_notes(NOTES)
        self.assertIn("In-app updates", out)
        self.assertIn("Intro line.", out)
        self.assertNotIn("Download it", out)
        self.assertNotIn("SHA256", out)
        self.assertNotIn("Known limitations", out)       # everything after the Install heading is dropped
        self.assertEqual(out, out.strip())

    def test_sha_line_with_a_real_hash_is_dropped_but_prose_about_it_stays(self):
        text = "# T\n\nSHA256: " + "ab" * 32 + "\nThe SHA-256 of the file is shown below.\n"
        out = mlj.release_notes(text)
        self.assertNotIn("ab" * 32, out)
        self.assertIn("is shown below", out)

    def test_crlf_and_no_install_heading(self):
        out = mlj.release_notes("# T\r\n\r\n- a\r\n- b\r\n")
        self.assertEqual(out, "# T\n\n- a\n- b")

    def test_empty(self):
        self.assertEqual(mlj.release_notes(""), "")


class CliTests(unittest.TestCase):
    def run_main(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = mlj.main(list(args))
        return code, err.getvalue()

    def test_writes_json_the_updater_can_parse(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "x.sig").write_text(SIG + "\n", encoding="utf-8")
            (d / "notes.md").write_text(NOTES, encoding="utf-8")
            out = d / "latest.json"
            code, err = self.run_main("--version", "0.1.2", "--sig", str(d / "x.sig"), "--notes", str(d / "notes.md"),
                                      "--out", str(out), "--pub-date", "2026-11-01T10:00:00Z")
            self.assertEqual((code, err), (0, ""))
            m = json.loads(out.read_text("utf-8"))
            self.assertEqual(m["version"], "0.1.2")
            self.assertEqual(m["pub_date"], "2026-11-01T10:00:00Z")
            self.assertEqual(m["platforms"]["windows-x86_64"]["signature"], SIG)
            self.assertIn("In-app updates", m["notes"])
            self.assertNotIn(b"\r", out.read_bytes())

    def test_errors_exit_1_without_a_traceback_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            out = d / "latest.json"
            (d / "empty.sig").write_text("", encoding="utf-8")
            code, err = self.run_main("--version", "0.1.2", "--sig", str(d / "empty.sig"), "--out", str(out))
            self.assertEqual(code, 1)
            self.assertIn("empty", err)
            code, err = self.run_main("--version", "0.1.2", "--sig", str(d / "missing.sig"), "--out", str(out))
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
