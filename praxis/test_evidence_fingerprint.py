"""Отпечаток индекса доказательств и перенос разрешений между пересборками.

Корень, замеренный 28.08 на живом дереве: сборка кадра в комнате 16,6 с, из них
14,8 с — `read_summary` -> `claim_evidence_index`. Кэш индекса убивал сам себя:
ключ = sha256 ВСЕХ файлов жизни (65 МБ), она дописывает события каждую минуту,
memo разрешений жил внутри индекса и умирал вместе с ним.

Три гарантии кандидата, каждая накрыта здесь:
1. Отпечаток без чтения корпуса целиком ОСТАЁТСЯ отпечатком: дозапись видна,
   правка той же длины с ПОДДЕЛАННЫМ mtime видна (хвост-sha, не stat).
2. Пересборка пофайловая: неизменённые файлы не перечитываются (строки — те же
   объекты), дозапись стоит один файл.
3. Разрешения переезжают в новый индекс, пока их основания не тронуты; тронутые —
   пересчитываются, включая разрешение, упавшее на ПРОПАВШЕМ событии.

Запуск:  python praxis_test.py test_evidence_fingerprint -v
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory_fts
import memory_life as ml
import memory_provenance as mp
import telegram_routes as tr

ROOM = "-1001240718803"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_fp_"))
        self._orig = {name: getattr(ml, name) for name in
                      ("BASE", "MEM_DIR", "LIFE_DIR", "EVENTS_DIR", "COMPACTS_DIR",
                       "EPISODES_DIR", "CLAIMS_DIR", "PATCHES_DIR", "REFLECTIONS_DIR",
                       "STATE_DIR", "LEGACY_SUMMARIES_DIR", "DIALOGUES_DIR")}
        ml.BASE = self.tmp
        ml.MEM_DIR = self.tmp / "memory"
        ml.LIFE_DIR = ml.MEM_DIR / "life"
        ml.EVENTS_DIR = ml.LIFE_DIR / "events"
        ml.COMPACTS_DIR = ml.LIFE_DIR / "compacts"
        ml.EPISODES_DIR = ml.LIFE_DIR / "episodes"
        ml.CLAIMS_DIR = ml.LIFE_DIR / "claims"
        ml.PATCHES_DIR = ml.LIFE_DIR / "patches"
        ml.REFLECTIONS_DIR = ml.LIFE_DIR / "reflections"
        ml.STATE_DIR = ml.MEM_DIR / ".state" / "life"
        ml.LEGACY_SUMMARIES_DIR = ml.MEM_DIR / ".summaries"
        ml.DIALOGUES_DIR = ml.MEM_DIR / "dialogues"
        self._routes = tr.DIR
        tr.DIR = self.tmp / "memory" / ".state" / "group_context"

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(ml, name, value)
        tr.DIR = self._routes
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ помощь
    def _msg(self, mid: int, text: str, *, chat: str = ROOM, ts: float | None = None):
        return ml.record_message(chat, f"Николай: {text}", actor="Николай",
                                 direction="in", source_id=str(mid),
                                 ts=float(mid) if ts is None else ts,
                                 dedupe_key=f"telegram:{chat}:{mid}:in")

    def _edit(self, mid: int, text: str, *, chat: str = ROOM, ts: float = 10_000.0):
        row = ml.record_message(chat, f"Николай: {text}", actor="Николай",
                                direction="in", source_id=f"{mid}:edit:1", ts=ts,
                                dedupe_key=f"telegram:{chat}:{mid}:edit:in")
        ml.note_message_revision(chat, mid, f"Николай: {text}")
        return row

    def _compact(self, rows, *, chat: str = ROOM, summary: str = "сводка"):
        return ml._write_compact(
            chat, {"summary": summary, "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[r["id"] for r in rows], source_compacts=[],
            event_count=len(rows), continued=False,
            first_ts=min(str(r["ts"]) for r in rows),
            last_ts=max(str(r["ts"]) for r in rows))

    def _evidence(self):
        return mp.claim_evidence_index(ml.MEM_DIR)


class TestFingerprintStaysAFingerprint(Base):
    """Гарантия 1: дешёвый отпечаток сбрасывает кэш всюду, где сбрасывал дорогой."""

    def test_append_invalidates_the_index(self):
        self._msg(1, "первая")
        before = self._evidence()
        row = self._msg(2, "вторая")
        after = self._evidence()
        self.assertIsNot(after, before)
        self.assertIn(row["id"], after["events"])

    def test_same_size_rewrite_with_forged_mtime_is_still_seen(self):
        """Правка той же длины, та же инода, mtime ПОДДЕЛАН обратно — ловит хвост.

        Голый (size, mtime_ns, ino) здесь слеп по построению; если этот тест упал,
        значит из отпечатка выпал хэш содержимого."""
        rows = [self._msg(1, "реплика")]
        compact = self._compact(rows)
        path = next(ml.COMPACTS_DIR.glob("*/*.md"))
        stat = path.stat()
        raw = path.read_bytes()
        target = compact["id"][-1]
        forged = ("0" if target != "0" else "1").encode("ascii")
        mutated = raw.replace(compact["id"].encode("ascii"),
                              compact["id"][:-1].encode("ascii") + forged)
        self.assertEqual(len(mutated), len(raw))
        before = self._evidence()
        self.assertIn(compact["id"], before["compacts"])
        with path.open("r+b") as stream:  # та же инода, никакого rename
            stream.write(mutated)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        after = self._evidence()
        self.assertIsNot(after, before)
        self.assertNotIn(compact["id"], after["compacts"])


class TestRebuildIsPerFile(Base):
    """Гарантия 2: неизменённые файлы не перечитываются и не переразбираются."""

    def test_rows_of_untouched_days_are_the_same_objects(self):
        old_day = self._msg(1, "вчерашняя", ts=1.0)          # 1970-01-01.jsonl
        before = self._evidence()
        self._msg(2, "сегодняшняя", ts=90_000.0)             # 1970-01-02.jsonl
        after = self._evidence()
        self.assertIsNot(after, before)
        self.assertIs(after["events"][old_day["id"]], before["events"][old_day["id"]])

    def test_a_changed_day_is_reparsed(self):
        first = self._msg(1, "первая", ts=1.0)
        before = self._evidence()
        second = self._msg(2, "вторая", ts=2.0)              # тот же день, тот же файл
        after = self._evidence()
        self.assertIn(second["id"], after["events"])
        self.assertEqual(after["events"][first["id"]], before["events"][first["id"]])


class TestResolutionsTravel(Base):
    """Гарантия 3: перенос memo точен — переезжает нетронутое, пересчитывается тронутое."""

    def test_unrelated_append_carries_the_resolution(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 4)]
        compact = self._compact(rows)
        before = self._evidence()
        resolved = mp.compact_evidence(compact["id"], before)
        self.assertTrue(resolved["valid"])
        self._msg(50, "не имеет отношения к свёртке")
        after = self._evidence()
        self.assertIsNot(after, before)
        carried = after.get(mp._RESOLUTION_MEMO_KEY) or {}
        self.assertIs(carried.get((compact["id"], True)), resolved)
        self.assertTrue(mp.compact_evidence(compact["id"], after)["valid"])

    def test_a_revision_drops_only_the_affected_resolution(self):
        first_rows = [self._msg(i, f"реплика {i}") for i in range(1, 4)]
        second_rows = [self._msg(i, f"реплика {i}") for i in range(4, 7)]
        first = self._compact(first_rows)
        second = self._compact(second_rows)
        before = self._evidence()
        first_resolved = mp.compact_evidence(first["id"], before)
        second_resolved = mp.compact_evidence(second["id"], before)
        self.assertTrue(first_resolved["valid"] and second_resolved["valid"])
        self._edit(2, "переписала вторую")
        after = self._evidence()
        carried = after.get(mp._RESOLUTION_MEMO_KEY) or {}
        self.assertIs(carried.get((second["id"], True)), second_resolved)
        # Несвежий ответ не переезжает. Свежий пересчёт под этим ключом законен:
        # писатели memory_life сами разрешают свёртки при записи ревизии.
        self.assertIsNot(carried.get((first["id"], True)), first_resolved)
        self.assertFalse(mp.compact_evidence(first["id"], after)["valid"])
        self.assertTrue(mp.compact_coverage(first["id"], after)["valid"])
        self.assertTrue(mp.compact_evidence(second["id"], after)["valid"])

    def test_a_missing_source_coming_back_recomputes(self):
        """Отказ на пропавшем событии — тоже основание: событие вернулось, отказ ушёл."""
        rows = [self._msg(1, "единственная")]
        compact = self._compact(rows)
        day = ml.EVENTS_DIR / "1970-01-01.jsonl"
        lines = day.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if rows[0]["id"] not in line]
        self.assertEqual(len(kept), len(lines) - 1)
        day.write_text("".join(kept), encoding="utf-8")
        broken = self._evidence()
        self.assertFalse(mp.compact_evidence(compact["id"], broken)["valid"])
        with day.open("a", encoding="utf-8") as stream:
            stream.write([line for line in lines if rows[0]["id"] in line][0])
        healed = self._evidence()
        self.assertNotIn((compact["id"], True),
                         healed.get(mp._RESOLUTION_MEMO_KEY) or {})
        self.assertTrue(mp.compact_evidence(compact["id"], healed)["valid"])

    def test_claims_resolve_through_the_memo(self):
        rows = [self._msg(1, "реплика")]
        compact = self._compact(rows)
        evidence = self._evidence()
        resolution = mp._resolve_claim_evidence(
            {"evidence_ids": [compact["id"]]}, evidence)
        self.assertTrue(resolution["valid"])
        self.assertIn((compact["id"], True),
                      evidence.get(mp._RESOLUTION_MEMO_KEY) or {})


class TestFirstLineCache(Base):
    """`_generated_markdown` без открытия файла на тёплом пути."""

    def test_warm_path_does_not_reopen_and_change_is_seen(self):
        note = ml.MEM_DIR / "notes" / "заметка.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("<!-- praxis-generated: тест -->\nтело\n", encoding="utf-8")
        self.assertTrue(memory_fts._generated_markdown(note, ml.MEM_DIR))
        with mock.patch.object(Path, "open",
                               side_effect=AssertionError("тёплый путь открыл файл")):
            self.assertTrue(memory_fts._generated_markdown(note, ml.MEM_DIR))
        note.write_text("обычная заметка, маркера больше нет\nтело\n", encoding="utf-8")
        self.assertFalse(memory_fts._generated_markdown(note, ml.MEM_DIR))

    def test_unreadable_and_empty_files_keep_their_old_branches(self):
        empty = ml.MEM_DIR / "notes" / "пустая.md"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("", encoding="utf-8")
        # Пустой файл: маркера нет, файл не «generated» — как и до кэша.
        self.assertFalse(memory_fts._generated_markdown(empty, ml.MEM_DIR))
        missing = ml.MEM_DIR / "notes" / "нет-такой.md"
        self.assertFalse(memory_fts._generated_markdown(missing, ml.MEM_DIR))


if __name__ == "__main__":
    unittest.main()
