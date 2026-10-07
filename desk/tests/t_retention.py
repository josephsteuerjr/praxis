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

    def test_backups_are_reported_but_never_swept(self):
        # 02.10, слово владельца: снимки-бэкапы — главная часть занятого места,
        # леджер обязан их показывать; удаляет владелец руками, sweep не трогает.
        # Песочница со своей обёрткой: backups ищется у РОДИТЕЛЯ data/.
        import shutil
        root = self.data.parent / "retention-backups-case"
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        data = root / "data"
        (root / "backups" / "2026-10-01_before").mkdir(parents=True, exist_ok=True)
        (root / "backups" / "2026-10-01_before" / "snap.zip").write_bytes(b"b" * 5000)
        classes = {Path(e["path"]).name: e["class"] for e in retention.classify(data)}
        self.assertEqual(classes.get("backups"), "backups")
        ledger = retention.sweep(data)
        self.assertIn("backups", [e["class"] for e in ledger["entries"]])
        self.assertEqual(ledger["removed"], [])
        self.assertTrue((root / "backups" / "2026-10-01_before" / "snap.zip").exists())
        self.assertIn("снимки чистит владелец", json.dumps(ledger["policies"], ensure_ascii=False))

    def test_cli_report_prints_and_never_writes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = retention.main(["--data", str(self.data), "report"])
        self.assertEqual(code, 0)
        self.assertIn("praxis-ui", out.getvalue())
        self.assertIn("только отчёт", out.getvalue())
        self.assertIsNone(retention.load_ledger(self.data),
                          "report не пишет леджер")

    # ---- удаление по слову владельца (05.10: «никак не чистится») ----------

    def test_delete_entry_removes_project_by_owner_word(self):
        ledger = retention.delete_entry(self.data, None, str(self.project))
        self.assertTrue(ledger.get("ok"), ledger.get("note"))
        self.assertFalse(self.project.exists(), "папка проекта должна уйти")
        kept = {e["path"] for e in ledger["entries"]}
        self.assertNotIn(str(self.project), kept)
        stored = retention.load_ledger(self.data)
        self.assertEqual(stored["removed"][0]["path"], str(self.project),
                         "удаление обязано попасть в леджер")

    def test_delete_entry_refuses_paths_outside_the_fresh_snapshot(self):
        stranger = self.data / "memory" / "soul"
        stranger.mkdir(parents=True)
        result = retention.delete_entry(self.data, None, str(stranger))
        self.assertFalse(result.get("ok"), "чужой путь удалён — дыра")
        self.assertTrue(stranger.exists())
        self.assertIn("снимке", result.get("note", ""))

    def test_delete_entry_refuses_models_and_backups(self):
        import contextlib
        models = self.data / "models"
        result = retention.delete_entry(self.data, None, str(models))
        self.assertFalse(result.get("ok"))
        self.assertTrue(models.exists(), "модели кнопке не подотчётны")
        backups = self.data.parent / "backups"
        with contextlib.suppress(FileExistsError):
            backups.mkdir(parents=True)
        result = retention.delete_entry(self.data, None, str(backups))
        self.assertFalse(result.get("ok"))
        self.assertTrue(backups.exists(), "снимки — страховка, кнопка их не ест")

    def test_delete_entry_removes_readonly_git_objects(self):
        import stat as stat_mod
        obj = self.project / ".git" / "objects" / "ab"
        obj.mkdir(parents=True)
        blob = obj / "cdef1234"
        blob.write_bytes(b"g" * 40)
        os.chmod(blob, stat_mod.S_IREAD)
        ledger = retention.delete_entry(self.data, None, str(self.project))
        self.assertTrue(ledger.get("ok"), ledger.get("note"))
        self.assertFalse(self.project.exists(),
                         "read-only git-объекты не должны спасать папку")


if __name__ == "__main__":
    unittest.main()
