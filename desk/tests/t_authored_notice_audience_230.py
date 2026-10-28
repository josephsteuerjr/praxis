"""Causal persisted-work admission, actual notice projection/renderer (no model/network)."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "helene" / "core"
sys.path.insert(0, str(CORE))
import authored_notes
from core import notices, subagents


class WorkNotes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.ledger = authored_notes.AuthoredNoteLedger(self.base)
        self.patch = patch.multiple(notices, BASE=self.base, STATE_FILE=self.base / "notices.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_explicit_identity_not_latest_chat_or_nearby_task(self):
        good = self.ledger.write("AUTHOR SAME WORK", work_ref="forge:task-a:unit-a", audience="owner", chat_id="window")
        self.ledger.write("WRONG UNIT", work_ref="forge:task-a:unit-b", chat_id="window")
        self.ledger.write("WRONG TASK", work_ref="forge:task-b:unit-a", chat_id="window")
        self.ledger.write("LATEST UNBOUND", chat_id="window")
        row = notices.note_node(task_id="task-a", unit_id="unit-a", status="succeeded", origin_chat="window")
        line = notices._line(row, owner_context=True, fresh=True)
        self.assertIn(good["id"], line)
        self.assertIn("AUTHOR SAME WORK", line)
        for text in ("WRONG UNIT", "WRONG TASK", "LATEST UNBOUND"):
            self.assertNotIn(text, line)

    def test_no_fallback_after_newer_note_withdrawal(self):
        self.ledger.write("OLD NICE SUMMARY", work_ref="run:run-a")
        newer = self.ledger.write("NEW UNCERTAIN RESULT", work_ref="run:run-a")
        event = {"work_ref": "run:run-a", "run_id": "run-a", "audience": "owner"}
        self.assertIn("NEW UNCERTAIN RESULT", authored_notes.for_notice(self.base, event, owner=True))
        self.ledger.close(newer["id"])
        self.assertEqual(authored_notes.for_notice(self.base, event, owner=True), "")

    def test_exact_audience_no_owner_note_fallback_for_chat_work(self):
        self.ledger.write("PRIVATE OWNER NOTE", work_ref="forge:task-a:unit-a", chat_id="room")
        public = self.ledger.write("PUBLIC ROOM NOTE", work_ref="forge:task-a:unit-a", audience="chat:room", chat_id="room")
        row = notices.note_node(task_id="task-a", unit_id="unit-a", status="done", origin_chat="room", audience="chat:room")
        text = notices._line(row, owner_context=False, fresh=True, chat_id="room", audience="chat:room")
        self.assertIn(public["id"], text)
        self.assertIn("PUBLIC ROOM NOTE", text)
        self.assertNotIn("PRIVATE OWNER NOTE", text)
        paired = notices.project_node(row, owner=False, chat_id="room", audience="paired")
        self.assertTrue(paired["redacted"])
        self.assertNotIn(public["id"], json.dumps(paired))
        self.assertNotIn("PUBLIC ROOM NOTE", notices._line(row, owner_context=False, fresh=True, chat_id="other"))

    def test_private_work_persisted_then_new_process_resumes_other_audience(self):
        note = self.ledger.write("SECRET-AUTHORED-WORK", work_ref="forge:task-secret:unit-secret", source_ref="run:private-source:call:1")
        row = notices.note_node(task_id="task-secret", unit_id="unit-secret", status="done", goal="SECRET-GOAL",
            origin_chat="owner-dm", result_ref={"path": "PRIVATE/result.json", "sha256": "abc"})
        first = notices._line(row, owner_context=True, fresh=True)
        self.assertIn("SECRET-AUTHORED-WORK", first)
        self.assertIn("PRIVATE/result.json", first)
        # Actual fresh interpreter reads only persisted ledgers: no in-process
        # note object or pre-rendered text crosses this restart boundary.
        code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core import notices
notices.BASE = Path(sys.argv[2]); notices.STATE_FILE = notices.BASE / 'notices.json'
row = notices.pending()[0]
public = notices.project_node(row, owner=False, chat_id='public-room', audience='public')
paired = notices.project_node(row, owner=True, chat_id='owner-dm', audience='paired')
model_input = notices.block_for_input(run_id='resumed', run_kind='chat', chat_id='public-room', owner_context=False, audience='public')
print(json.dumps({'viewer':public,'paired':paired,'model_input':model_input}, ensure_ascii=True))
'''
        proc = subprocess.run([sys.executable, "-c", code, str(CORE), str(self.base)], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["viewer"]["redacted"])
        self.assertTrue(result["paired"]["redacted"])
        for secret in ("SECRET-AUTHORED-WORK", "SECRET-GOAL", note["id"], "PRIVATE/result.json", "private-source", "task-secret", "unit-secret"):
            self.assertNotIn(secret, proc.stdout)
        self.assertEqual(len(notices.pending()), 1, "redaction does not destroy original private evidence")
        self.assertIn("SECRET-AUTHORED-WORK", notices._line(notices.pending()[0], owner_context=True, fresh=False))

    def test_legacy_missing_audience_private_after_reload(self):
        row = self.ledger.write("LEGACY PRIVATE", work_ref="task:task-a")
        events = self.ledger.events()
        events[0].pop("audience")
        self.ledger.events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        reloaded = authored_notes.AuthoredNoteLedger(self.base).get(row["id"])
        self.assertFalse(authored_notes.visible(reloaded, chat_id="public"))
        self.assertTrue(authored_notes.visible(reloaded, owner=True))
        event = {"work_ref": "task:task-a", "task_id": "task-a"}
        self.assertEqual(authored_notes.for_notice(self.base, event, chat_id="public"), "")
        self.assertIn("LEGACY PRIVATE", authored_notes.for_notice(self.base, event, owner=True))

    def test_contradictory_or_invalid_work_link_never_selects_a_note(self):
        self.ledger.write("DO NOT PICK", work_ref="forge:task-a:unit-a")
        event = {"work_ref": "forge:task-a:unit-a", "task_id": "task-b"}
        self.assertEqual(authored_notes.for_notice(self.base, event, owner=True), "")
        with self.assertRaises(ValueError):
            notices.note_node(task_id="task-b", unit_id="unit-a", status="done", work_ref="forge:task-a:unit-a")
        for value in ("latest-in-this-chat", "forge:task-a:unit-a:other", "run:../private", "task:"):
            with self.assertRaises(ValueError):
                self.ledger.write("x", work_ref=value)

    def test_public_normalization_cannot_take_authority_from_result(self):
        row = subagents.normalize("task-a", "unit-a", {"status": "done", "audience": "chat:public", "run_id": "forged"},
            request={"run_context": {"run_id": "captured-run"}}, task={"origin_chat": "private"})
        self.assertEqual(row["audience"], "owner")
        self.assertEqual(row["run_id"], "captured-run")
        self.assertEqual(row["work_ref"], "forge:task-a:unit-a")
        row = subagents.normalize("task-a", "unit-a", {}, task={"origin_chat": "room", "audience": "chat:room"})
        self.assertEqual(row["audience"], "chat:room")
        row = subagents.normalize("task-a", "unit-a", {}, task={"origin_chat": "room", "audience": "chat:other"})
        self.assertEqual(row["audience"], "owner")


    def test_actual_forge_producer_retains_file_receipt_not_model_locator(self):
        # Compile the real producer without unrelated application startup.
        tree = ast.parse((CORE / "forge.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "emit_unit_event")
        unit = self.base / "memory" / ".forge" / "tasks" / "task-a" / "agents" / "unit-a"
        unit.mkdir(parents=True)
        raw = b'{"status":"done","result":"actual full result"}\n'
        (unit / "result.json").write_bytes(raw)
        namespace = {"_read_json": lambda path, default: {"origin_chat": "owner-dm"} if path.name == "task.json" else {},
            "_task_dir": lambda task_id: unit.parent.parent,
            "_unit_dir": lambda *args: unit, "_dedicated_adapter": lambda row: False,
            "hashlib": hashlib, "log": Mock()}
        from core import events
        with patch.object(events, "emit") as emit:
            exec(compile(ast.Module(body=[function], type_ignores=[]), "forge.py", "exec"), namespace)
            namespace["emit_unit_event"]("task-a", "unit-a", {"status": "done", "path": "MODEL-FORGED-PATH", "audience": "chat:public"})
        self.assertTrue(emit.called)
        notice = notices.pending()[0]
        self.assertEqual(notice["audience"], "owner")
        self.assertEqual(notice["result_ref"], {"path": str(unit / "result.json"), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
        self.assertNotIn("MODEL-FORGED-PATH", json.dumps(notice))
        self.assertNotIn(str(unit), json.dumps(notices.project_node(notice, audience="public")))

    def test_invitation_and_receipt_projection_do_not_bypass_admission(self):
        payload = subagents.normalize("task-secret", "unit-secret", {"status": "done", "result": "SECRET-RESULT"},
            task={"origin_chat": "owner-dm", "goal": "SECRET-GOAL"}, unit_dir="PRIVATE-RESULT-PATH")
        for kwargs in ({"owner": False, "chat_id": "public-room"}, {"owner": True, "audience": "paired"}):
            text = subagents.invitation([payload], **kwargs)
            projection = json.dumps(subagents.project(payload, **kwargs))
            for secret in ("task-secret", "unit-secret", "SECRET-RESULT", "SECRET-GOAL", "PRIVATE-RESULT-PATH", "owner-dm"):
                self.assertNotIn(secret, text + projection)
        self.assertIn("task-secret", subagents.invitation([payload]))


if __name__ == "__main__":
    unittest.main()
