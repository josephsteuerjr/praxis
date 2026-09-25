"""26.09 — индекс памяти издания рядом с потоком ухода за recall (ревью W2 S2/S3, W3 S4/S7).

У неё пересборка индекса — ночь под общим замком хода. В издании 1.0.1 её ведёт ещё и поток
ухода за recall в любое время суток, и швы стали видны:
* рука памяти хода (`upsert` через `agent._reindex`) стояла всю пересборку на `_LOCK` —
  теперь она откладывает свой путь, а пересборка догоняет его сразу после замены базы;
* замена базы поверх открытого соединения на Windows — отказ доступа; теперь повтор с паузой;
* недостроенная база убитого сборщика оставалась навсегда — теперь её убирают;
* заявку убитого сборщика на POSIX не подбирали шесть часов аренды — теперь решает flock.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import memory_fts


def _corpus(root: Path) -> tuple[Path, Path]:
    base = root
    memory = base / "memory"
    (memory / "people").mkdir(parents=True)
    (memory / ".state").mkdir(parents=True)
    for i in range(6):
        (memory / "people" / f"p{i}.md").write_text(f"# Человек {i}\n\nлюбит яблоки {i}\n",
                                                    encoding="utf-8")
    return base, memory


class UpsertDoesNotWaitForTheRebuild(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base, self.memory = _corpus(Path(tmp.name))
        memory_fts.rebuild(base=self.base, memory_dir=self.memory)

    def test_upsert_during_rebuild_is_deferred_then_caught_up(self):
        started, release = threading.Event(), threading.Event()
        real_insert = memory_fts._insert_source

        def slow_insert(db, source, memory_dir, **kw):
            started.set()
            release.wait(10)
            return real_insert(db, source, memory_dir, **kw)

        with mock.patch.object(memory_fts, "_insert_source", slow_insert):
            builder = threading.Thread(
                target=memory_fts.rebuild, kwargs={"base": self.base, "memory_dir": self.memory})
            builder.start()
            self.assertTrue(started.wait(10))
            note = self.memory / "people" / "new.md"
            note.write_text("# Новенький\n\nзнает про кометы\n", encoding="utf-8")
            t0 = time.monotonic()
            out = memory_fts.upsert(note, base=self.base, memory_dir=self.memory)
            waited = time.monotonic() - t0
            release.set()
            builder.join(20)
        self.assertLess(waited, 2.0, "рука памяти хода не ждёт пересборку")
        self.assertTrue(out.get("deferred"), out)
        hits = memory_fts.search("кометы", base=self.base, memory_dir=self.memory)
        self.assertTrue(any("new.md" in str(h.get("path")) for h in hits),
                        "отложенная правка догнала пересборку")

    def test_upsert_without_a_rebuild_is_immediate(self):
        note = self.memory / "people" / "p0.md"
        out = memory_fts.upsert(note, base=self.base, memory_dir=self.memory)
        self.assertFalse(out.get("deferred"))


class ReplaceRetriesWhileAReaderHoldsTheDatabase(unittest.TestCase):
    def test_permission_error_is_retried_on_windows(self):
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, "Отказано в доступе")
        with (mock.patch.object(memory_fts, "_WINDOWS", True),
              mock.patch.object(memory_fts.os, "replace", flaky),
              mock.patch.object(memory_fts, "_REPLACE_PAUSE_SEC", 0.0)):
            memory_fts._replace_db(Path("a.tmp"), Path("a"))
        self.assertEqual(len(calls), 3)

    def test_posix_does_not_hide_a_real_error(self):
        with (mock.patch.object(memory_fts, "_WINDOWS", False),
              mock.patch.object(memory_fts.os, "replace", side_effect=PermissionError(13, "no"))):
            with self.assertRaises(PermissionError):
                memory_fts._replace_db(Path("a.tmp"), Path("a"))


class OrphanBuildsAreSwept(unittest.TestCase):
    def test_dead_builders_leftovers_go_live_ones_stay(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "recall.sqlite3"
        dead = Path(f"{db}.4000000.aaaa.tmp")
        dead_journal = Path(f"{db}.4000000.aaaa.tmp-journal")
        mine = Path(f"{db}.{os.getpid()}.bbbb.tmp")
        for path in (dead, dead_journal, mine):
            path.write_bytes(b"x")
        import process_liveness
        with mock.patch.object(process_liveness, "is_process_alive",
                               side_effect=lambda pid, *a: pid != 4000000):
            memory_fts._sweep_orphan_builds(db)
        self.assertFalse(dead.exists())
        self.assertFalse(dead_journal.exists())
        self.assertTrue(mine.exists(), "свой процесс не трогается")


class DeadBuildersClaimIsRecoveredNow(unittest.TestCase):
    def test_unlocked_claim_with_a_fresh_lease_returns_to_the_request(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = Path(tmp.name) / "memory"
        (memory / ".state").mkdir(parents=True)
        request = memory_fts._refresh_request_path(memory)
        claim = request.with_name("recall_refresh.claim.4000000.cafe.json")
        claim.write_text('{"schema":"praxis.recall-refresh.v1","token":"t",'
                         f'"lease_expires_at":{time.time() + 6 * 3600}}}', encoding="utf-8")
        memory_fts._recover_stale_refresh_claims(memory_dir=memory)
        self.assertTrue(request.exists(), "заявка убитого сборщика подобрана сразу")
        self.assertFalse(claim.exists())


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
