"""26.09 — адверсарное ревью после правок 25.09 (W1 prod-live, W2/W3 в её коде).

Что здесь прибито (пол приватных записей W1 S1/S2 снят 10.11 словом Егора «сними» —
его тесты удалены вместе с полом, история в git):
* тёзки — по всей подходящей части книги, не по восьмёрке лучших (W1 S3);
* личка с третьим человеком в recall называется личкой, а не «комнатой» (W1 S4);
* раннер собрал ленту прежним окном — сборщик кадра не подаёт эпоху (W1 S6);
* снятие предложения свёртки: не при `state_changed`, tmp не общий (W1 S7);
* адресаты реплики владельца — только живые окна (W1 S9);
* JSONL: U+2028/U+2029/U+0085 не режут запись ни при записи, ни при перезаписи (W3 S6);
* пустая заявка на пересборку индекса — заявка; поиск без базы не создаёт пустую базу (W2 S4);
* заявку убитого сборщика подбирают сразу — flock раньше аренды (W3 S7).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import agent
import frame_epoch
import frame_layout
import memory_fts
import memory_life as ml
import mtproto_runner
import run_manager
import telegram_contacts
import turns

from test_frame_epoch_1509 import LEVER, ROOM_ID, frame_for, room_ctx


def _contacts(rows: dict) -> dict:
    return {"contacts": rows}


class NamesakesAcrossTheWholeBook(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "telegram_contacts.json"
        now = 1_800_000_000.0
        rows = {"1001": {"id": "1001", "display_name": "Иван", "contact": True, "dialog": True,
                         "interactions": 30, "last_seen": now, "last_outbound": now},
                "1002": {"id": "1002", "display_name": "Иван", "last_seen": now - 400 * 86400}}
        for i in range(8):
            rows[str(2000 + i)] = {"id": str(2000 + i), "display_name": f"Иван Фамилия{i}",
                                   "contact": True, "dialog": True, "interactions": 30,
                                   "last_seen": now, "last_outbound": now}
        path.write_text(json.dumps(_contacts(rows), ensure_ascii=False), encoding="utf-8")
        for patcher in (mock.patch.object(telegram_contacts, "CONTACTS_PATH", path),
                        mock.patch.object(telegram_contacts, "_CACHE", None),
                        mock.patch.object(telegram_contacts.time, "time", return_value=now)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_second_exact_namesake_behind_the_top_eight_still_refuses(self):
        top8 = telegram_contacts.candidates("Иван")
        self.assertNotIn("1002", [r["id"] for r in top8], "условие сценария: тёзка за восьмёркой")
        self.assertIsNone(mtproto_runner._ambiguous_book("Иван", top8))

        async def resolve():
            with (mock.patch.object(mtproto_runner, "_ensure_dialog_cache",
                                    mock.AsyncMock(return_value=None)),
                  mock.patch.dict(mtproto_runner._entity_cache, {}, clear=True)):
                return await mtproto_runner._resolve_entity("Иван")

        with self.assertRaises(mtproto_runner.ResolveDenied) as caught:
            asyncio.run(resolve())
        self.assertIn("несколько людей", str(caught.exception))

    def test_limit_none_returns_the_whole_matching_book(self):
        self.assertEqual(len(telegram_contacts.candidates("Иван", limit=None)), 10)
        self.assertEqual(len(telegram_contacts.candidates("Иван")), 8)


class RecallNamesADirectChatAsADirectChat(unittest.TestCase):
    def test_labels(self):
        with mock.patch.object(telegram_contacts, "_load",
                               return_value={"5550001": {"display_name": "Вика"}}):
            self.assertEqual(agent._recall_room_label("5550001"),
                             "из личной переписки с Вика (tg 5550001)")
            self.assertEqual(agent._recall_room_label("5550002"),
                             "из личной переписки с tg 5550002")
        self.assertEqual(agent._recall_room_label("-100777"), "из комнаты -100777")
        self.assertEqual(agent._recall_room_label("owner-dm"), "из личной переписки с владельцем")


class RefusedEpochIsNotServed(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (mock.patch.object(frame_epoch, "STORE", Path(tmp.name) / "epoch"),
                        mock.patch.object(frame_epoch, "anchor_for", return_value=100)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(frame_layout.reset)
        self.addCleanup(frame_epoch.reset)

    def test_runner_fallback_keeps_the_old_envelope(self):
        with mock.patch.dict(os.environ, LEVER):
            with frame_epoch.bind(ROOM_ID, None, refused=True):
                self.assertTrue(frame_epoch.refused(ROOM_ID))
                self.assertIsNone(frame_epoch.bound_anchor(ROOM_ID))
                _p, _d, evidence = frame_for(room_ctx(owner=False))
        self.assertIsNone(frame_epoch.taken(), "эпоха не подана")
        self.assertIn("РАНЕЕ В ЭТОМ ДИАЛОГЕ", evidence)
        self.assertNotIn("ЭПОХА ЭТОЙ КОМНАТЫ", evidence)

    def test_plain_unbound_turn_still_computes_the_anchor(self):
        with mock.patch.dict(os.environ, LEVER):
            with frame_epoch.bind(ROOM_ID, None):
                self.assertFalse(frame_epoch.refused(ROOM_ID))
                frame_for(room_ctx(owner=False))
        self.assertIsNotNone(frame_epoch.taken())


class FoldOffers(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(ml, "_FOLD_OFFERS", Path(tmp.name) / "fold_offers.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        ml._fold_offers_write({"-1001": {"place": "-1001"}})

    def test_state_changed_keeps_the_offer(self):
        with (mock.patch.object(ml, "compact_if_due",
                                return_value={"ok": False, "reason": "state_changed", "hot": 5}),
              mock.patch.object(ml, "adopt_place", side_effect=lambda p: p)):
            ml.fold_now("-1001")
        self.assertIn("-1001", ml.fold_offers())

    def test_owner_force_cannot_clear_an_unwritten_offer(self):
        with mock.patch.object(ml, "compact_if_due") as automatic:
            out = ml.fold_now("-1001")
        self.assertTrue(out["author_required"], out)
        self.assertEqual(out["folded"], 0)
        automatic.assert_not_called()
        self.assertIn("-1001", ml.fold_offers())

    def test_tmp_names_differ_between_writes(self):
        seen = []
        real = os.replace

        def spy(src, dst):
            seen.append(str(src))
            return real(src, dst)
        with mock.patch.object(ml.os, "replace", spy):
            ml._fold_offers_write({})
            ml._fold_offers_write({})
        self.assertEqual(len(set(seen)), 2)


class OwnerWordsGoOnlyToLiveWindows(unittest.TestCase):
    def test_terminal_run_is_skipped(self):
        manifests = {"a": {"status": "done"}, "b": {"status": "running"}}
        runs = types.SimpleNamespace(
            live_run_ids=lambda: ["a", "b"], manifest=lambda rid: manifests[rid],
            _manifest_listing_row=lambda rid, m: {"status": m["status"], "kind": "window"})
        with (mock.patch.object(agent, "_runs", return_value=runs),
              mock.patch("core.notices.WINDOW_RUN_KINDS", frozenset({"window"}))):
            self.assertEqual(agent._live_window_run_ids(), ["b"])
        self.assertIn("running", run_manager.NONTERMINAL_STATUSES)


class JsonlLineSeparators(unittest.TestCase):
    TEXT = "до после и\x85конец"

    def test_writer_escapes_and_round_trips(self):
        line = ml._jsonl_line(json.dumps({"t": self.TEXT}, ensure_ascii=False))
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(json.loads(line)["t"], self.TEXT)

    def test_reader_keeps_a_legacy_raw_record(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "events-2026-09-26.jsonl"
        path.write_bytes((json.dumps({"i": 1, "text": self.TEXT}, ensure_ascii=False) + "\n"
                          + json.dumps({"i": 2}) + "\n").encode("utf-8"))
        with mock.patch.dict(os.environ, {"PRAXIS_EVENTS_CACHE": "off"}):
            rows = ml._event_file_records(path)
        self.assertEqual([r["i"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["text"], self.TEXT)

    def test_update_delivery_rewrite_does_not_split_a_record(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "turns.jsonl"
        rows = [{"run_id": "r1", "delivery": "authored", "out": self.TEXT},
                {"run_id": "r2", "delivery": "authored", "out": ""}]
        path.write_bytes("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
                         .encode("utf-8"))
        with (mock.patch.object(turns, "PATH", path),
              mock.patch.object(turns, "_load_ring", return_value=[])):
            turns.update_delivery("r2", "accepted", out="слово")
        disk = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]
        self.assertEqual([r["run_id"] for r in disk], ["r1", "r2"])
        self.assertEqual(disk[0]["out"], self.TEXT)
        self.assertEqual(disk[1]["delivery"], "accepted")


class RecallRefreshWedge(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.mem = Path(tmp.name) / "memory"
        (self.mem / ".state").mkdir(parents=True)

    def test_empty_request_file_is_still_a_request(self):
        memory_fts._refresh_request_path(self.mem).write_bytes(b"")
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.mem))

    def test_dead_builders_claim_with_a_fresh_lease_is_recovered_now(self):
        """W3 S7: flock решает раньше аренды — заявку убитого сборщика не ждут шесть часов."""
        import time as _time
        request = memory_fts._refresh_request_path(self.mem)
        claim = request.with_name("recall_refresh.claim.4000000.cafe.json")
        claim.write_text('{"schema":"praxis.recall-refresh.v1","token":"t",'
                         f'"lease_expires_at":{_time.time() + 6 * 3600}}}', encoding="utf-8")
        memory_fts._recover_stale_refresh_claims(memory_dir=self.mem)
        self.assertTrue(request.exists())
        self.assertFalse(claim.exists())

    def test_search_without_a_database_does_not_create_one(self):
        db = self.mem / ".state" / "recall.sqlite3"
        out = memory_fts.search("что-нибудь", base=self.mem.parent, memory_dir=self.mem,
                                db_path=db)
        self.assertEqual(out, [])
        self.assertFalse(db.exists(), "sqlite3.connect не должен создать пустую базу")
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.mem))


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
