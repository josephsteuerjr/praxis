# -*- coding: utf-8 -*-
"""Кандидат evidence-0811 (08.11, Claude по просьбе Егора): фон, который ел GIL.

1. `claim_evidence_index` кэшируется по отпечатку ВСЕХ файлов ленты, а сегодняшний
   файл меняет каждое новое сообщение. Поэтому почти каждый вызов (пересборка места,
   refresh_debt, досье, identity, recall) заново считал текущие ревизии Telegram по всем
   ~128 тыс. событиям: ~4 с CPU под GIL. Теперь прошлые дни считаются один раз
   (`_current_event_ids_incremental`), на вызов — только последний файл. Ответ обязан
   совпадать с полным путём — это и проверяется здесь, в том числе против дословной
   копии прежнего `current_conversation_events`.
2. `_direct_outbox_once` раз в 15 с сортировал ~8,6 тыс. путей через pathlib и делал
   полную JSON-копию каждой принятой записи, чтобы тут же её пропустить.

Запуск:  python praxis_test.py test_evidence_0811 -v
"""
from __future__ import annotations

import atexit
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="praxis-evidence-0811-session-"))
atexit.register(shutil.rmtree, _SESSION_DIR, True)
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ["TELEGRAM_SESSION"] = str(_SESSION_DIR / "telethon")
os.environ.setdefault("PRAXIS_TEST", "1")

import memory_life as ml  # noqa: E402
import memory_provenance as mp  # noqa: E402
import telegram_outbox  # noqa: E402
import telegram_routes as tr  # noqa: E402


def _reference_current_conversation_events(rows):
    """Дословная копия `current_conversation_events` до 08.11 — эталон рефакторинга."""
    items = [row for row in rows if isinstance(row, dict)]
    selected = {}
    priority = {"original": 0, "edit": 1, "delete": 2}
    for index, row in enumerate(items):
        key = mp.telegram_message_key(row)
        if key is None:
            continue
        kind = mp.telegram_revision_kind(row)
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        revision_order = meta.get("revision_order")
        try:
            revision_order = int(revision_order)
        except (TypeError, ValueError):
            revision_order = 0
        rank = (
            1 if kind == "delete" else 0,
            mp._event_epoch(row.get("ts")),
            priority.get(kind, 0),
            revision_order,
            mp._event_epoch(meta.get("observed_at")),
            index,
        )
        previous = selected.get(key)
        if previous is None or rank >= previous[0]:
            selected[key] = (rank, row)
    current_ids = {id(row) for _rank, row in selected.values()}
    return [row for row in items
            if mp.telegram_message_key(row) is None or id(row) in current_ids]


def _reference_ids(rows):
    items = [row for row in rows if isinstance(row, dict)]
    current = {str(row.get("id")) for row in _reference_current_conversation_events(items)
               if mp.telegram_message_key(row) is not None and str(row.get("id") or "")}
    return frozenset(str(row.get("id")) for row in items
                     if str(row.get("id") or "")
                     and (mp.telegram_message_key(row) is None or str(row.get("id")) in current))


class RandomHistories:
    """Небольшой пул линий и меток времени — чтобы равные ранги и переходы через
    границу файлов случались часто."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.n = 0

    def row(self, *, empty_id=False):
        rng = self.rng
        self.n += 1
        peer = rng.choice(["-1001", "-1002", "777"])
        topic = rng.choice([None, None, 5, 9])
        chat = peer if topic is None else f"{peer}__topic__{topic}"
        mid = rng.randint(1, 6)
        kind = rng.choice(["original", "original", "edit", "edit", "delete"])
        source_id = str(mid) if kind == "original" else f"{mid}:{kind}:{self.n}"
        meta = {}
        if rng.random() < 0.85:
            meta["time_quality"] = "observed"
        if rng.random() < 0.6:
            meta["observed_at"] = rng.choice(
                ["2026-11-07T10:00:00.000Z", "2026-11-07T10:00:01.000Z", "garbage", 5.0])
        if rng.random() < 0.4:
            meta["revision_order"] = rng.choice([0, 1, 2, "3", "x", None])
        row = {
            "id": "" if empty_id else f"evt-{self.n:06d}",
            "kind": rng.choice(["conversation_message"] * 9 + ["note"]),
            "source": rng.choice(["telegram"] * 8 + ["runtime", "Telegram"]),
            "source_id": source_id if rng.random() < 0.95 else "not-a-message",
            "chat_id": chat if rng.random() < 0.97 else "",
            "ts": rng.choice(["2026-11-07T10:00:00.000Z", "2026-11-07T10:00:00.000Z",
                              "2026-11-07T10:00:02.000Z", 1794045600.0, "bad"]),
            "meta": meta,
        }
        if rng.random() < 0.7:
            row["dedupe_key"] = f"k{self.n}"
        return row

    def file(self, count):
        out = {}
        for _ in range(count):
            row = self.row(empty_id=self.rng.random() < 0.03)
            out[row["id"] or f"empty-{self.n}"] = row
        return out


class RevisionRankRefactorIsExact(unittest.TestCase):
    def test_current_conversation_events_matches_the_previous_code(self):
        gen = RandomHistories(8110)
        for _ in range(400):
            rows = [gen.row() for _ in range(gen.rng.randint(0, 40))]
            got = mp.current_conversation_events(rows)
            want = _reference_current_conversation_events(rows)
            self.assertEqual([id(row) for row in got], [id(row) for row in want])
            self.assertEqual(mp.current_conversation_event_ids(rows), _reference_ids(rows))


class IncrementalCurrentIdsMatchTheFullPath(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mp._CURRENT_PREFIX_CACHE.clear)

    @staticmethod
    def expected(per_file, dups):
        return _reference_ids(row for _path, _sig, candidates in per_file
                              for event_id, row in candidates.items() if event_id not in dups)

    def check(self, key, per_file, dups):
        got = mp._current_event_ids_incremental(key, per_file, set(dups))
        self.assertEqual(got, self.expected(per_file, set(dups)))
        self.assertIsInstance(got, frozenset)
        return got

    def test_random_histories_through_appends_rollover_old_edits_and_duplicates(self):
        gen = RandomHistories(811)
        for trial in range(250):
            key = f"trial-{trial}"
            files = [gen.file(gen.rng.randint(0, 25)) for _ in range(gen.rng.randint(1, 5))]
            sig = [0] * len(files)

            def per_file():
                return [(f"/e/{i:02d}.jsonl", ("sig", i, sig[i]), files[i])
                        for i in range(len(files))]

            dups = set()
            self.check(key, per_file(), dups)
            for _step in range(6):
                action = gen.rng.choice(["append", "append", "append", "rollover",
                                         "old", "dup", "nosig"])
                if action == "append":
                    files[-1] = dict(files[-1], **gen.file(gen.rng.randint(1, 6)))
                    sig[-1] += 1
                    before = mp._CURRENT_PREFIX_CACHE.get(key)
                    self.check(key, per_file(), dups)
                    if before is not None and len(files) > 1:
                        # Префикс не пересчитывался: тот же объект отбора.
                        self.assertIs(mp._CURRENT_PREFIX_CACHE[key][2], before[2])
                elif action == "rollover":
                    files.append(gen.file(gen.rng.randint(0, 10)))
                    sig.append(0)
                    self.check(key, per_file(), dups)
                elif action == "old" and len(files) > 1:
                    i = gen.rng.randrange(len(files) - 1)
                    files[i] = dict(files[i], **gen.file(gen.rng.randint(1, 4)))
                    sig[i] += 1
                    self.check(key, per_file(), dups)
                elif action == "dup" and len(files) > 1:
                    i = gen.rng.randrange(len(files) - 1)
                    if files[i]:
                        event_id = gen.rng.choice(list(files[i]))
                        files[-1] = dict(files[-1], **{event_id: dict(files[i][event_id])})
                        sig[-1] += 1
                        dups.add(event_id)
                        self.check(key, per_file(), dups)
                elif action == "nosig":
                    entries = per_file()
                    entries[0] = (entries[0][0], None, entries[0][2])
                    self.check(key, entries, dups)
                    if len(entries) > 1:
                        self.assertNotIn(key, mp._CURRENT_PREFIX_CACHE)

    def test_equal_rank_in_the_tail_beats_the_prefix_like_a_later_index(self):
        base = {"kind": "conversation_message", "source": "telegram", "chat_id": "-100",
                "ts": "2026-11-07T10:00:00.000Z", "meta": {"time_quality": "observed"}}
        old = dict(base, id="evt-a", source_id="7:edit:a")
        new = dict(base, id="evt-b", source_id="7:edit:b")
        per_file = [("/e/1", ("s", 1), {"evt-a": old}), ("/e/2", ("s", 2), {"evt-b": new})]
        self.assertEqual(self.check("tie", per_file, set()), frozenset({"evt-b"}))
        # и наоборот — более поздний по времени в префиксе удерживает линию
        later = dict(old, ts="2026-11-07T10:00:05.000Z")
        per_file = [("/e/1", ("s", 3), {"evt-a": later}), ("/e/2", ("s", 2), {"evt-b": new})]
        self.assertEqual(self.check("tie2", per_file, set()), frozenset({"evt-a"}))

    def test_empty_history(self):
        self.assertEqual(mp._current_event_ids_incremental("empty", [], set()), frozenset())


class ClaimEvidenceIndexUsesThePrefix(unittest.TestCase):
    """Настоящий индекс на временном дереве: дозапись в последний день не пересчитывает
    прошлые, правка/удаление старого сообщения сегодняшним днём видны, смена суток тоже."""

    ROOM = "-1001240718803"
    DAY = 86400.0
    T0 = 1794000000.0          # 2026-11-06

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_ev0811_"))
        names = ("BASE", "MEM_DIR", "LIFE_DIR", "EVENTS_DIR", "COMPACTS_DIR", "EPISODES_DIR",
                 "CLAIMS_DIR", "PATCHES_DIR", "REFLECTIONS_DIR", "STATE_DIR",
                 "LEGACY_SUMMARIES_DIR", "DIALOGUES_DIR")
        self._orig = {name: getattr(ml, name) for name in names}
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
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._orig.items():
            setattr(ml, name, value)
        tr.DIR = self._routes
        shutil.rmtree(self.tmp, ignore_errors=True)

    def event(self, mid, *, day, sec, revision=""):
        source_id = str(mid) if not revision else f"{mid}:{revision}:{day}-{sec}"
        return ml.append_event(
            "conversation_message", chat_id=self.ROOM, actor="Николай", direction="in",
            text=f"Николай: {mid} {revision or 'original'} {day}-{sec}", source="telegram",
            source_id=source_id, ts=self.T0 + day * self.DAY + sec,
            dedupe_key=f"telegram:{self.ROOM}:{source_id}",
            meta={"is_dm": False, "time_quality": "observed"})

    def index(self):
        evidence = mp.claim_evidence_index(ml.MEM_DIR)
        self.assertEqual(evidence["current_event_ids"],
                         mp.current_conversation_event_ids(evidence["events"].values()))
        return evidence

    def test_index_matches_the_full_path_and_reuses_past_days(self):
        first = [self.event(mid, day=0, sec=mid) for mid in range(1, 6)]
        second = [self.event(mid, day=1, sec=mid) for mid in range(6, 9)]
        self.event(2, day=1, sec=30, revision="edit")
        evidence = self.index()
        self.assertNotIn(first[1]["id"], evidence["current_event_ids"])
        self.assertIn(first[0]["id"], evidence["current_event_ids"])
        key = Path(ml.MEM_DIR).resolve().as_posix()
        prefix = mp._CURRENT_PREFIX_CACHE[key][2]

        # Дозапись в последний день: прошлые дни не пересчитываются, удаление видно.
        gone = self.event(3, day=1, sec=40, revision="delete")
        evidence = self.index()
        self.assertIs(mp._CURRENT_PREFIX_CACHE[key][2], prefix)
        self.assertIn(gone["id"], evidence["current_event_ids"])
        self.assertNotIn(first[2]["id"], evidence["current_event_ids"])
        self.assertIn(second[0]["id"], evidence["current_event_ids"])

        # Смена суток: вчерашний день уходит в префикс, ответ тот же, что у полного пути.
        self.event(6, day=2, sec=1, revision="edit")
        evidence = self.index()
        self.assertNotIn(second[0]["id"], evidence["current_event_ids"])


class OutboxPeriodicSkipsWhatIsReconciled(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import mtproto_runner as runner
        self.runner = runner
        self.tempdir = tempfile.TemporaryDirectory(prefix="praxis-outbox-0811-")
        self.old_outbox = runner._DIRECT_OUTBOX
        self.old_reconciled = runner._DIRECT_OUTBOX_RECONCILED
        self.outbox = telegram_outbox.TelegramOutbox(Path(self.tempdir.name) / "outbox")
        runner._DIRECT_OUTBOX = self.outbox
        runner._DIRECT_OUTBOX_RECONCILED = set()
        self.keys = []
        for i in range(5):
            entry = self.outbox.prepare_text(
                f"telegram-outbox:run-{i}:tool:call-{i}", peer_id=42, text=f"t{i}",
                run_id=f"run-{i}", call_id=f"call-{i}", purpose="tool:send_message")
            self.outbox.mark_accepted(entry["key"], message_id=100 + i,
                                      random_id=entry["random_id"])
            self.keys.append(entry["key"])

    async def asyncTearDown(self):
        self.runner._DIRECT_OUTBOX = self.old_outbox
        self.runner._DIRECT_OUTBOX_RECONCILED = self.old_reconciled
        self.tempdir.cleanup()

    async def test_accepted_excludes_only_the_given_keys(self):
        everything = self.outbox.accepted()
        self.assertEqual(len(everything), 5)
        some = set(self.keys[:3])
        rest = self.outbox.accepted(exclude_keys=some)
        self.assertEqual(rest, tuple(row for row in everything if row["key"] not in some))
        self.assertEqual(self.outbox.accepted(exclude_keys=()), everything)

    async def test_entry_paths_keep_the_name_order(self):
        self.assertEqual(self.outbox._entry_paths(),
                         sorted(self.outbox.entries_dir.glob("*.jsonl")))

    async def test_periodic_passes_the_reconciled_keys_and_still_reconciles_the_rest(self):
        runner = self.runner
        runner._DIRECT_OUTBOX_RECONCILED.update(self.keys[:2])
        seen = []

        async def reconcile(entry):
            seen.append(entry["key"])
            runner._DIRECT_OUTBOX_RECONCILED.add(entry["key"])
            return True

        real = self.outbox.accepted
        calls = []

        def accepted(**kwargs):
            calls.append(kwargs)
            return real(**kwargs)

        with (patch.object(runner, "_reconcile_direct_outbox_entry", AsyncMock(side_effect=reconcile)),
              patch.object(self.outbox, "accepted", side_effect=accepted)):
            await runner._direct_outbox_once()
            await runner._direct_outbox_once()
        self.assertEqual(sorted(seen), sorted(self.keys[2:]))
        self.assertEqual(set(calls[0]["exclude_keys"]), set(self.keys[:2]))
        self.assertEqual(set(calls[1]["exclude_keys"]), set(self.keys))


if __name__ == "__main__":
    unittest.main()
