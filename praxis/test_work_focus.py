"""Work focus stores one pointer and projects only current, typed private evidence."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import authored_notes
import desires
import work_focus
import work_store


class WorkFocusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="praxis-work-focus-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name) / "one"
        self.other = Path(temp.name) / "two"
        self.notes = authored_notes.AuthoredNoteLedger(self.base)
        self.desires = desires.DesireLedger(self.base)

    def _card(self, *, base=None, **kwargs):
        with mock.patch.object(work_store, "BASE", base or self.base):
            return work_store.open_card(goal=kwargs.pop("goal", "card goal"), **kwargs)

    def _body(self, work_id):
        text = work_focus.render(self.base, work_id)
        self.assertTrue(text.startswith(work_focus.HEADER))
        return json.loads(text[len(work_focus.HEADER):])

    def _transition(self, work_id, status, **kwargs):
        with mock.patch.object(work_store, "BASE", self.base):
            return work_store.transition(work_id, status, **kwargs)

    def _desire(self, statement="previous direction", **kwargs):
        result = self.desires.notice(statement, source=kwargs.pop("source", "chosen by Praxis"),
                                     why_it_matters="lasting reason", next_move="old move", **kwargs)
        return result["desire_id"]

    def test_pointer_writes_only_reference_and_revisioned_events(self):
        row = self._card()
        wid = row["id"]
        self._transition(wid, "waiting", evidence="latest receipt")
        with mock.patch.object(work_store, "BASE", self.base):
            work_store.note(wid, "PRIVATE PROSE LOG")
        before = work_store.card(wid, base=self.base)
        note = self.notes.write("author note ☀", scope="global")
        nid = note["id"]
        with mock.patch.object(work_store, "BASE", self.other):
            bound = work_focus.bind_note(self.base, wid, nid)
            no_change = work_focus.bind_note(self.base, wid, nid)
        self.assertEqual(bound, {"work_id": wid, "note_id": nid,
                                 "revision": before["revision"] + 1, "changed": True})
        self.assertEqual(no_change, {**bound, "changed": False})
        saved = work_store.card(wid, base=self.base)
        self.assertEqual(saved["focus_note_id"], nid)
        self.assertEqual(saved["body"], before["body"])
        task_dir = self.base / "memory" / "work" / "tasks" / wid
        self.assertNotIn(note["text"], (task_dir / "TASK.md").read_text(encoding="utf-8"))
        with mock.patch.object(work_store, "BASE", self.base):
            events = work_store.events(wid)
        intent, outcome = events[-2:]
        self.assertEqual([(intent["kind"], intent["revision"]), (outcome["kind"], outcome["revision"])],
                         [("intent", bound["revision"]), ("committed", bound["revision"])])
        self.assertEqual(intent["focus_note_id"], nid)
        self.assertNotIn(note["text"], json.dumps(events, ensure_ascii=False))
        self.assertFalse(self.other.exists())
        rendered = self._body(wid)
        self.assertEqual(rendered["note"], {"id": nid, "text": note["text"],
                         "version": "sha256:" + hashlib.sha256(note["text"].encode()).hexdigest()})
        self.assertEqual(rendered["card"]["last_evidence"], "latest receipt")
        self.assertNotIn("PRIVATE PROSE LOG", json.dumps(rendered))

    def test_rebind_and_detach_are_explicit_and_keep_notes_intact(self):
        wid = self._card()["id"]
        a = self.notes.write("old")
        b = self.notes.write("new")
        work_focus.bind_note(self.base, wid, a["id"])
        work_focus.bind_note(self.base, wid, b["id"])
        self.assertEqual(self._body(wid)["note"]["text"], "new")
        detached = work_focus.bind_note(self.base, wid, "")
        self.assertTrue(detached["changed"])
        self.assertFalse(work_focus.bind_note(self.base, wid, "")["changed"])
        self.assertNotIn("note", self._body(wid))
        self.assertNotIn("focus_note_id", work_store.card(wid, base=self.base))
        self.assertEqual(self.notes.get(a["id"])["status"], "open")
        self.assertEqual(self.notes.get(b["id"])["status"], "open")

    def test_refuses_bad_ids_and_any_non_global_or_closed_note(self):
        wid = self._card()["id"]
        good = self.notes.write("global text")
        closed = self.notes.write("closed text")
        self.notes.close(closed["id"])
        chat = self.notes.write("chat text", scope="chat", chat_id="22")
        run = self.notes.write("run text", scope="run", run_id="run-22")
        for note_id in (None, " ", " " + good["id"], good["id"] + "\n", "note-nonexistent",
                        "../notes", closed["id"], chat["id"], run["id"]):
            with self.subTest(note_id=note_id), self.assertRaises(ValueError):
                work_focus.bind_note(self.base, wid, note_id)
        for work_id in (None, "", "../" + wid, "nonexistent", wid + " ", wid.upper()):
            with self.subTest(work_id=work_id), self.assertRaises(ValueError):
                work_focus.bind_note(self.base, work_id, good["id"])
            self.assertEqual(work_focus.render(self.base, work_id), "")
        self.assertNotIn("focus_note_id", work_store.card(wid, base=self.base))
        self.assertFalse((self.base / "memory" / "work" / "tasks" / "nonexistent").exists())

    def test_render_rechecks_scope_and_note_lifecycle_even_if_pointer_already_exists(self):
        wid = self._card()["id"]
        note = self.notes.write("really current")
        work_focus.bind_note(self.base, wid, note["id"])
        self.assertEqual(self._body(wid)["note"]["text"], "really current")
        self.notes.close(note["id"], reason="PRIVATE CLOSE REASON")
        self.assertNotIn("really current", work_focus.render(self.base, wid))
        for scope, binding in (("chat", {"chat_id": "elsewhere"}), ("run", {"run_id": "run-elsewhere"})):
            scoped = self.notes.write("PRIVATE SCOPED TEXT", scope=scope, **binding)
            # A manually edited/stale card is not sufficient authorization.
            work_store._set_focus_note(wid, scoped["id"], base=self.base)
            body = self._body(wid)
            self.assertNotIn("note", body)
            self.assertEqual(body["omitted"]["note"]["reason"], "not_current_open_global_note")
            text = json.dumps(body)
            self.assertNotIn(scoped["id"], text)
            self.assertNotIn("PRIVATE", text)

    def test_closed_or_wrong_card_identity_yields_no_evidence(self):
        for status in work_store.TERMINAL:
            wid = self._card()["id"]
            self._transition(wid, status)
            with mock.patch.object(work_focus, "_note_fields") as read:
                self.assertEqual(work_focus.render(self.base, wid), "")
                read.assert_not_called()
            with self.assertRaises(ValueError):
                work_focus.bind_note(self.base, wid, "")
        wid = self._card()["id"]
        task_path = self.base / "memory" / "work" / "tasks" / wid / "TASK.md"
        text = task_path.read_text(encoding="utf-8").replace(f'id: "{wid}"', 'id: "foreign"')
        task_path.write_text(text, encoding="utf-8")
        self.assertEqual(work_focus.render(self.base, wid), "")

    def test_read_fresh_desire_and_only_typed_work_state(self):
        did = self._desire()
        other = self._desire("foreign desire")
        self.desires.want(other, note="not this work")
        row = self._card(desire_id=did)
        wid = row["id"]
        self._transition(wid, "waiting", evidence="first receipt")
        expected_wait = {"wake_on": "when receipt appears", "not_before": "2026-12-01T10:00:00Z"}
        self._transition(wid, "waiting", phase="verify", evidence="last receipt", wake={
            **expected_wait, "prose": "FOREIGN WAKE DETAIL"})
        latent = self._body(wid)
        self.assertNotIn("desire", latent)
        self.assertEqual(latent["card"], {"id": wid, "status": "waiting", "phase": "verify",
                         "goal": "card goal", "last_evidence": "last receipt", "wait": expected_wait})
        self.desires.want(did, note="PRIVATE CAUSAL LOG", statement="current direction", next_move="new move")
        live = self._body(wid)
        self.assertEqual(live["desire"], {"id": did, "status": "active", "statement": "current direction",
                         "why_it_matters": "lasting reason", "next_move": "new move"})
        for never in ("first receipt", "old move", "foreign desire", "FOREIGN WAKE DETAIL", "PRIVATE CAUSAL LOG"):
            self.assertNotIn(never, json.dumps(live))
        self.desires.choose(did, note="parked by her", status="blocked")
        self.assertNotIn("desire", self._body(wid))

    def test_rejects_a_normalized_desire_alias(self):
        did = self._desire()
        self.desires.want(did, note="chosen")
        wid = self._card(desire_id=did + " ")["id"]
        self.assertNotIn("desire", self._body(wid))

    def test_an_old_active_desire_cannot_bypass_provenance_policy(self):
        did = self._desire()
        self.desires.want(did, note="chosen")
        wid = self._card(desire_id=did)["id"]
        for source, refs, reason in (("journal", [], "untrusted_provenance"),
                                     ("chosen by Praxis", 42, "invalid_provenance")):
            event = self.desires.events(did)[-1]
            event["state"].update(source=source, evidence_refs=refs)
            with self.desires.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
            body = self._body(wid)
            self.assertNotIn("desire", body)
            self.assertEqual(body["omitted"]["desire"]["reason"], reason)

    def test_credential_floor_is_checked_before_excerpt_and_not_quoted(self):
        token = "sk-" + "a" * 24
        long = "no token here yet " * 100 + token
        did = self._desire()
        self.desires.want(did, note="chosen", next_move=long)
        wid = self._card(goal="ordinary reproducible task: " + token, desire_id=did)["id"]
        self._transition(wid, "waiting", evidence=long, wake={"wake_on": long})
        note = self.notes.write(long)
        work_focus.bind_note(self.base, wid, note["id"])
        evidence = work_focus.render(self.base, wid)
        self.assertNotIn(token, evidence)
        self.assertNotIn("no token here yet", evidence)
        payload = self._body(wid)
        self.assertNotIn("note", payload)
        self.assertNotIn("next_move", payload["desire"])
        for source in ("card.goal", "card.last_evidence", "card.wait.wake_on", "note", "desire.next_move"):
            self.assertEqual(payload["omitted"][source], {"reason": "credential_floor"})

    def test_size_budget_applies_to_escaped_values_with_explicit_omission(self):
        wid = self._card()["id"]
        text = "literal\n" + "\x01" * 9000 + "end"
        note = self.notes.write(text)
        work_focus.bind_note(self.base, wid, note["id"])
        evidence = work_focus.render(self.base, wid)
        self.assertEqual(len(evidence.splitlines()), 2)
        self.assertLess(len(evidence), 1700)
        payload = self._body(wid)
        kept = payload["note"]["text"]
        self.assertEqual(kept, text[:len(kept)])
        self.assertEqual(payload["omitted"]["note.text"],
                         {"reason": "size_bound", "chars_omitted": len(text) - len(kept)})

    def test_pointer_revision_survives_unrelated_updates_but_not_selector_changes(self):
        wid = self._card()["id"]
        a = self.notes.write("old note")
        b = self.notes.write("new note")
        work_focus.bind_note(self.base, wid, a["id"])
        saved_project = work_focus._note_fields

        def update(*args):
            self._transition(wid, "waiting", evidence="new receipt")
            return saved_project(*args)

        with mock.patch.object(work_focus, "_note_fields", side_effect=update):
            body = self._body(wid)
        self.assertEqual(body["note"]["id"], a["id"])
        self.assertEqual(body["card"]["last_evidence"], "new receipt")

        def switch(*args):
            work_focus.bind_note(self.base, wid, b["id"])
            return saved_project(*args)

        with mock.patch.object(work_focus, "_note_fields", side_effect=switch):
            body = self._body(wid)
        self.assertNotIn("note", body)
        self.assertEqual(body["omitted"]["note"]["reason"], "link_changed_during_read")
        self.assertEqual(self._body(wid)["note"]["id"], b["id"])

    def test_card_closing_during_projection_removes_entire_block(self):
        wid = self._card()["id"]
        note = self.notes.write("short")
        work_focus.bind_note(self.base, wid, note["id"])
        saved_project = work_focus._note_fields

        def close(*args):
            self._transition(wid, "done")
            return saved_project(*args)

        with mock.patch.object(work_focus, "_note_fields", side_effect=close):
            self.assertEqual(work_focus.render(self.base, wid), "")


if __name__ == "__main__":
    unittest.main()
