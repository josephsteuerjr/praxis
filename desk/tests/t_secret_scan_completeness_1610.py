"""The release value scanner must not call an incomplete tree clean."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[1] / "installer" / "secret_scan.py"


class CompleteScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "tracked.txt").write_text("ordinary source", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt"], check=True)
        self.values = self.root / "live-values.json"
        self.values.write_text(json.dumps({"fixture.env": "TOKEN=synthetic-value-123456789"}), encoding="utf-8")

    def scan(self):
        return subprocess.run([sys.executable, str(SCANNER), str(self.root), str(self.values)],
                              capture_output=True, text=True, encoding="utf-8")

    def test_clean_and_missing_tracked_file(self):
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ноль попаданий", result.stdout)
        (self.root / "tracked.txt").unlink()
        result = self.scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("НЕПОЛНАЯ СВЕРКА", result.stderr)
        self.assertNotIn("synthetic-value-123456789", result.stdout + result.stderr)

    def test_oversized_file_is_not_a_clean_scan(self):
        with (self.root / "tracked.txt").open("wb") as file:
            file.truncate(8 * 1024 * 1024 + 1)
        result = self.scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("НЕПОЛНАЯ СВЕРКА", result.stderr)

    def test_git_failure_never_falls_back_to_directory_walk(self):
        (self.root / ".git" / "index").unlink()
        # A deleted index yields a successful but empty ls-files, not a directory scan.
        result = self.scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("индекс git пуст", result.stderr)

    def test_public_model_values_are_exact_exceptions_not_key_exceptions(self):
        (self.root / "tracked.txt").write_text("gpt-6.1-sol / gpt-image-2", encoding="utf-8")
        self.values.write_text(json.dumps({"profile": json.dumps({
            "roles": {"voice": {"model": "gpt-6.1-sol"}}, "images": {"model": "gpt-image-2"}})}), encoding="utf-8")
        # A scan with no eligible values must refuse, even when both are public model IDs.
        result = self.scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("НЕЧЕГО ИСКАТЬ", result.stderr)
        private = "synthetic-private-model-token"
        (self.root / "tracked.txt").write_text(private, encoding="utf-8")
        self.values.write_text(json.dumps({"profile": json.dumps({
            "roles": {"voice": {"model": private}}})}), encoding="utf-8")
        result = self.scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("СЕКРЕТЫ В ЗЕРКАЛЕ", result.stderr)
        self.assertNotIn(private, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
