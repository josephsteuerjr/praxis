"""Regression for real archive-only pressure after all life inputs are folded."""
import hashlib
import json
import os
import unittest
from unittest import mock

import archive_repair as ar
import epoch_repair as er
import frame_epoch as fe
import group_context as gc
import memory_life as ml
import llm
import test_epoch_tape_2010 as fixture


class ArchiveRepair(unittest.TestCase):
    def setUp(self):
        self.f = fixture.EpochSources();self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.enterContext(mock.patch.object(ar, "BASE", self.f.base))
        self.enterContext(mock.patch.object(er, "BASE", self.f.base))
        self.enterContext(mock.patch.object(ml, "append_event", return_value={"id": "repair-event"}))

    def prepare(self):
        return self.f.prepare()

    def sources(self, topics=(10, 20, 20)):
        for i, topic in enumerate(topics, 1):
            self.f.add(i, "exact source " + str(i) + (" X" * 400), topic=topic, reply=99)
        return self.prepare()

    def fold(self, selected):
        prepared = ar.prepare(selected)
        with mock.patch.object(llm,"chat",return_value=llm.LLMResponse(text="Distinct factual recap", model="helper-real")):
            recap = er._recap(prepared)
        return prepared, ar.commit(prepared, recap)

    def test_real_archive_only_repair_reduces_full_selection_without_changing_raw(self):
        before = self.sources();raw = gc.archive_path(fixture.ROOM).read_bytes()
        prepared, result = self.fold(before)
        after = self.prepare()
        self.assertTrue(result["ok"])
        self.assertLess(after["capacity"]["chars"], before["capacity"]["chars"])
        self.assertEqual(gc.archive_path(fixture.ROOM).read_bytes(), raw)
        self.assertEqual(len(after["residuals"]), 3)
        self.assertEqual(after["covered_event_ids"] if "covered_event_ids" in after else [], [])
        text = "\n".join(r["line"] for r in after["rows"])
        self.assertIn("helper-real", text);self.assertIn("не авторский текст", text)
        self.assertIn("exact source 3", text)
        self.assertNotEqual(before["boundary"], after["boundary"])
        self.assertEqual(len(prepared["sources"]), 2)

    def test_narrow_scope_does_not_expose_combined_other_topic_recap(self):
        before = self.sources();self.fold(before)
        narrow = fe.prepare_tape(fixture.ROOM, peer_id=fixture.ROOM, topic_id=10)
        text = "\n".join(r["line"] for r in narrow["rows"])
        self.assertIn("exact source 1", text)
        self.assertNotIn("Distinct factual recap", text)
        self.assertNotIn("exact source 2", text)

    def test_changed_raw_and_changed_recap_never_prove_coverage(self):
        before=self.sources();prepared,_result=self.fold(before)
        path=ar.directory(fixture.ROOM)/(prepared["selection"]+".md")
        path.write_text("forged recap")
        self.assertEqual(ar.receipts(fixture.ROOM), [])
        after=self.prepare()
        self.assertEqual(len(after["rows"]), len(before["rows"]))
        path.unlink()
        # A source hash mismatch is independently rejected, before another call.
        source=prepared["sources"][0];source["sha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"source changed"):
            ar.verify(fixture.ROOM,prepared["sources"])

    def test_restart_growth_and_boundary_rebuild_do_not_duplicate_or_forget_recap(self):
        before=self.sources();self.fold(before)
        first=self.prepare();fe.reset();gc._KEY_CACHE.clear()
        self.f.add(4,"new live source",topic=20)
        again=self.prepare()
        self.assertEqual(sum(bool(r.get("archive_maintenance_compact")) for r in again["rows"]),1)
        self.assertIn("new live source", "\n".join(r["line"] for r in again["rows"]))
        self.f.snapshot["boundary"]="new-life-fold"
        rebuilt=self.prepare()
        self.assertEqual(sum(bool(r.get("archive_maintenance_compact")) for r in rebuilt["rows"]),1)

    def test_actual_repair_falls_back_to_exact_archive_sources_when_life_is_empty(self):
        original=self.sources()
        request={"id":"repair","chat":fixture.ROOM,"selection":{"scope":self.prepare()["scope"]}}
        with mock.patch.dict(os.environ,{"PRAXIS_FRAME_EPOCH_MAX_CHARS":"1500"}), \
             mock.patch.object(ml,"prepare_repair_fold",return_value={"ok":False}), \
             mock.patch.object(llm,"chat",return_value=llm.LLMResponse(text="Small attributed recap", model="configured-helper")) as call:
            result=er.repair_step(request)
        self.assertEqual(result["state"],"progress")
        self.assertIn(call.call_args.args[0],llm.ROLES)
        self.assertLess(self.prepare()["capacity"]["chars"],original["capacity"]["chars"])

    def test_budget_preserves_complete_occurrences_and_latest_live_source(self):
        selected=self.sources()
        first_size=len(json.dumps(selected["residuals"][0],ensure_ascii=False))
        self.assertFalse(ar.prepare(selected,budget_chars=first_size-1)["ok"])
        prepared=ar.prepare(selected,budget_chars=first_size)
        self.assertEqual(prepared["sources"],[selected["residuals"][0]])


if __name__ == "__main__":
    unittest.main()
