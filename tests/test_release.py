"""Verify that distribution archives omit local credentials and runtime data."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_release import ROOT, build_release


class ReleaseTests(unittest.TestCase):
    def test_archive_checksum_and_extracted_cli(self) -> None:
        version = (ROOT / "VERSION").read_text().strip()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            archive = build_release(version, output=output)
            checksum = archive.with_suffix(".zip.sha256").read_text().split()[0]
            self.assertEqual(checksum, hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                self.assertIn(f"competitor-radar-{version}/.env.example", names)
                for name in names:
                    parts = Path(name).parts[1:]
                    self.assertFalse(any(part in {".env", ".venv", "history", "reports", ".git", "__pycache__"} for part in parts))
                bundle.extractall(output / "extracted")
            result = subprocess.run(
                [sys.executable, "main.py", "--dry-run"],
                cwd=output / "extracted" / f"competitor-radar-{version}",
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[dry-run]", result.stdout)

    def test_version_must_match_project(self) -> None:
        with self.assertRaises(ValueError):
            build_release("../not-a-version")
        with self.assertRaises(ValueError):
            build_release("999.0.0")
