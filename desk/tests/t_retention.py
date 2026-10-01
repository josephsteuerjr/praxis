# -*- coding: utf-8 -*-
"""Леджер ретенции: классы, уборка только staging, неприкосновенность проектов."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / 'localharness')]
import retention


def days_ago(path: Path, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.put = self.data / "workspace/update-1.2.6"
        self.put.mkdir(parents=True)
        (self.put / "stage.bin").write_bytes(b"x" * 1000)
        days_ago(self.put, 20)
        fresh = self.data / "workspace/update-1.2.9-staging"
        fresh.mkdir(parents=True)
        (fresh / "part").write_bytes(b"y" * 10)
        stuck = self.data / "workspace/update-1.2.7-stuck"
        stuck.mkdir(parents=True)
        (stuck / "part").write_bytes(b"z" * 10)
        days_ago(stuck, 5)
        self.project = self.data / "workspace/praxis-ui"
        (self.project / "desk").mkdir(parents=True)
        (self.project / "desk/app.ts").write_bytes(b"a" * 500)
        (self.data / "workspace/media").mkdir(parents=True)
        (self.data / "workspace/media/spooled.png").write_bytes(b"m" * 100)
        (self.data / "models/whisper").mkdir(parents=True)
        (self.data / "models/whisper/model.bin").write_bytes(b"w" * 2000)
        run = self.data / "memory/runs/2026-09/run-x"
        run.mkdir(parents=True)
        (run / "0001-model-input.log").write_bytes(b"r" * 300)

    def classes(self):
        return {Path(e["path"]).name: e["class"] for e in retention.classify(self.data)}

    def test_classify_separates_projects_from_staging_and_caches(self):
        got = self.classes()
        self.assertEqual(got["update-1.2.6"], "update_staging")
        self.assertEqual(got["update-1.2.9-staging"], "update_staging")
        self.assertEqual(got["praxis-ui"], "project")
        self.assertEqual(got["media"], "spool_cache")
        self.assertEqual(got["models"], "models")
        self.assertEqual(got["runs"], "run_artifacts")
        sizes = {Path(e["path"]).name: e["bytes"] for e in retention.classify(self.data)}
        self.assertEqual(sizes["update-1.2.6"], 1000)
        self.assertEqual(sizes["praxis-ui"], 500)
        self.assertEqual(sizes["models"], 2000)

    def test_sweep_removes_only_old_staging_and_writes_ledger(self):
        ledger = retention.sweep(self.data)
        self.assertEqual([Path(r["path"]).name for r in ledger["removed"]],
                         ["update-1.2.6"])
        self.assertFalse(self.put.exists(), "старый staging убран")
        self.assertTrue((self.data / "workspace/update-1.2.7-stuck").exists(),
                        "моложе TTL — жив")
        self.assertTrue((self.data / "workspace/update-1.2.9-staging").exists(),
                        "свежий — жив")
        self.assertTrue(self.project.exists(), "проект неприкосновенен")
        self.assertTrue((self.data / "models/whisper/model.bin").exists())
        self.assertTrue((self.data / "memory/runs/2026-09/run-x").exists())
        on_disk = retention.load_ledger(self.data)
        self.assertEqual(on_disk["schema"], retention.SCHEMA)
        self.assertEqual(on_disk["removed"][0]["bytes"], 1000)
        names = [Path(e["path"]).name for e in on_disk["entries"]]
        self.assertIn("praxis-ui", names)
        self.assertNotIn("update-1.2.6", names)

    def test_second_sweep_is_idempotent(self):
        retention.sweep(self.data)
        again = retention.sweep(self.data)
        self.assertEqual(again["removed"], [])

    def test_cli_report_prints_and_never_writes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = retention.main(["--data", str(self.data), "report"])
        self.assertEqual(code, 0)
        self.assertIn("praxis-ui", out.getvalue())
        self.assertIn("только отчёт", out.getvalue())
        self.assertIsNone(retention.load_ledger(self.data),
                          "report не пишет леджер")


if __name__ == "__main__":
    unittest.main()
