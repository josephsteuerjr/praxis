# -*- coding: utf-8 -*-
"""The local desktop full-document writer; not the restricted server writer.

Run: python desk/tests/t_local_config_writer_1710.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))
import config_cli  # noqa: E402


class ConfigWriterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "helene.json"

    def cli(self, text, seen=None):
        argv = [sys.executable, str(Path(config_cli.__file__)), "--path", str(self.path)]
        if seen is not None:
            argv += ["--mtime-ns", seen]
        import os
        return subprocess.run(argv, input=text, text=True, encoding="utf-8", capture_output=True,
                              env={**os.environ, "PYTHONUTF8": "1"})

    def test_full_document_roundtrip_and_observed_stale(self):
        initial = '{"mode":"local","model":{"key":"secret","name":"Элен"},"relay":{"enabled":true}}'
        first = self.cli(initial)
        self.assertEqual(first.returncode, 0, first.stderr)
        receipt = json.loads(first.stdout)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["mtime_ns"], str(self.path.stat().st_mtime_ns))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["model"]["name"], "Элен")
        self.assertNotIn("secret", first.stdout + first.stderr)
        other = '{"mode":"remote"}'
        # Explicitly move mtime even on filesystems with coarse timestamp ticks.
        self.path.write_text(other, encoding="utf-8")
        previous = int(receipt["mtime_ns"])
        self.path.touch()
        import os
        os.utime(self.path, ns=(previous + 2_000_000_000, previous + 2_000_000_000))
        refused = json.loads(self.cli(initial, receipt["mtime_ns"]).stdout)
        self.assertEqual(refused["code"], "stale")
        self.assertFalse(refused["ok"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), other)
        saved = json.loads(self.cli(initial, refused["mtime_ns"]).stdout)
        self.assertTrue(saved["ok"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["mode"], "local")

    def test_invalid_input_and_pre_replace_error_leave_old_bytes(self):
        self.path.write_bytes(b'{"kept":true}')
        self.assertNotEqual(self.cli("not-json").returncode, 0)
        self.assertEqual(self.path.read_bytes(), b'{"kept":true}')
        with patch.object(config_cli.os, "replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                config_cli.save(self.path, '{"new":true}', None)
        self.assertEqual(self.path.read_bytes(), b'{"kept":true}')
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["helene.json"])

    def test_post_replace_receipt_failure_is_not_reported_as_no_write(self):
        """A failed post-write observation must not license a blind retry/relay."""
        self.path.write_bytes(b'{"old":true}')
        with patch.object(config_cli, "mtime_ns", side_effect=OSError("stat refused")):
            with self.assertRaisesRegex(OSError, "stat refused"):
                config_cli.save(self.path, '{"new":true}', None)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"new": True})
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["helene.json"])

    def test_prewrite_observation_failure_preserves_old_bytes(self):
        self.path.write_bytes(b'{"old":true}')
        with patch.object(config_cli, "mtime_ns", side_effect=OSError("stat refused")):
            with self.assertRaisesRegex(OSError, "stat refused"):
                config_cli.save(self.path, '{"new":true}', "1")
        self.assertEqual(self.path.read_bytes(), b'{"old":true}')

    def test_legacy_no_mtime_writes_and_missing_target(self):
        result = self.cli('{"a": 1}', "")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 1})


if __name__ == "__main__":
    unittest.main()
