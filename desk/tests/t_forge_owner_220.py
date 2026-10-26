# -*- coding: utf-8 -*-
"""Recorded Forge -> owner routes; no Forge processes, models or network.

Run with aiohttp available: python desk/tests/t_forge_owner_220.py
Temporary synthetic records live inside this source checkout and are removed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desk"))
sys.path.insert(0, str(ROOT / "helene" / "core"))
from deskd import forge_reader as reader, forge_control as control, readers
import deskapp
import forge_runtime
from aiohttp import web


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def fixture(base):
    task = base / "memory/.forge/tasks/code-fixture"
    put(task / "task.json", {"id": "code-fixture", "goal": "Fix a long coding task with independent review", "status": "active"})
    unit = task / "agents/agent-fixture"
    request = {"id": unit.name, "task_id": task.name, "role": "worker", "model": "fixture-model", "brief": "Implement the parser, retain source edits", "status": "running"}
    put(unit / "request.json", request)
    # The ACTUAL worker journal writer produces this fixture, not a second UI schema.
    journal = forge_runtime.Journal(unit)
    journal.start(request, {"next_boundary": "model_ready", "iteration": 1, "model": "fixture-model", "messages": [], "trace": []})
    input_ref = journal.payload('{"operation":"verify","file":"parser.py"}')
    output = "Проверка 🐍\n" + "full output line\n" * 1200
    output_ref = journal.payload(output)
    journal.event("tool_start", tool="coding_verify", call_id="call-one", input_ref=input_ref)
    journal.event("tool_result", tool="coding_verify", call_id="call-one", input_ref=input_ref, output_ref=output_ref)
    journal.save("model_ready", "ready")
    journal.close()
    sha = put(unit / "result.json", {"status": "done", "result": "Private source review", "diff_ref": output_ref})
    put(unit / f"disposition-root-{sha}-r000001.json", {"parent_unit_id": "", "child_unit_id": unit.name, "result_sha256": sha, "disposition": "deferred", "revision": 1})
    put(unit / f"disposition-root-{sha}-r000002.json", {"parent_unit_id": "", "child_unit_id": unit.name, "result_sha256": sha, "disposition": "integrated", "revision": 2})
    return task, unit, output_ref, output


class OwnerForge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".forge-ui-test-", dir=ROOT)
        self.base = Path(self.temp.name)
        self.task, self.unit, self.ref, self.output = fixture(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_journal_tree_generation_and_revision_acceptance(self):
        row = reader.tasks(self.base)[0]
        unit = row["agents"][0]
        self.assertEqual(unit["current_disposition"], "integrated")
        self.assertEqual(unit["observation"]["phase"], "ready")
        self.assertEqual(unit["status"], "done")
        self.assertEqual(unit["process_liveness"], "not_probed")
        self.assertEqual(unit["payload_refs"]["diff_ref"]["sha256"], self.ref["sha256"])
        self.assertNotIn(self.output, json.dumps(row))
        put(self.unit / "stop.json", {"state": "pending"})
        self.assertEqual(reader.tasks(self.base)[0]["agents"][0]["status"], "unknown")
        with mock.patch.object(readers, "tree", return_value=self.base):
            self.assertEqual(readers.forge_tasks()[0]["agents"][0]["custody"], "unresolved_stop")

    def test_exact_unicode_content_pages_and_stale_hash(self):
        offset, parts = 0, []
        while True:
            page = reader.payload(self.base, self.task.name, self.unit.name, self.ref["sha256"], offset=offset, limit=1000)
            parts.append(page["text"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(parts), self.output)
        (self.unit / "payloads" / (self.ref["sha256"] + ".txt")).write_text("changed", encoding="utf-8")
        with self.assertRaises(reader.ReadError) as error:
            reader.payload(self.base, self.task.name, self.unit.name, self.ref["sha256"])
        self.assertEqual(error.exception.status, 409)

    def test_events_page_boundaries_generation_and_corruption(self):
        first = reader.events(self.base, self.task.name, self.unit.name, limit=2)
        self.assertEqual(len(first["events"]), 2)
        self.assertTrue(first["has_more"])
        second = reader.events(self.base, self.task.name, self.unit.name, first["next_offset"], generation=first["generation"], after_seq=first["next_after_seq"])
        self.assertEqual(second["events"][0]["seq"], 3)
        self.assertFalse(second["has_more"])
        journal = self.unit / "events.jsonl"
        with journal.open("ab") as stream:
            stream.write(b'{"seq":4')
        tail = reader.events(self.base, self.task.name, self.unit.name, second["next_offset"], after_seq=3)
        self.assertTrue(tail["pending_tail"])
        self.assertFalse(tail["has_more"], "partial tail must not disable automatic live polling")
        for kwargs in ({"generation": "old-generation"}, {"offset": 1}):
            with self.assertRaises(reader.ReadError):
                reader.events(self.base, self.task.name, self.unit.name, **kwargs)
        (self.unit / "events.jsonl").write_bytes(b"not-json\n")
        with self.assertRaises(reader.ReadError):
            reader.events(self.base, self.task.name, self.unit.name)

    def test_streamed_unicode_page_across_decode_chunk_and_cached_result_refresh(self):
        text = "a" * (128 * 1024 - 1) + "🐍" + "z" * 150000
        raw = text.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        (self.unit / "payloads" / (sha + ".txt")).write_bytes(raw)
        page = reader.payload(self.base, self.task.name, self.unit.name, sha, offset=128 * 1024 - 2, limit=8)
        self.assertEqual(page["text"], text[128 * 1024 - 2:128 * 1024 + 6])
        self.assertEqual(page["total_chars"], len(text))
        old = reader.tasks(self.base)[0]["agents"][0]["result_ref"]["sha256"]
        new = put(self.unit / "result.json", {"status": "error", "result": "a changed terminal receipt for cache regression"})
        row = reader.tasks(self.base)[0]["agents"][0]
        self.assertNotEqual(old, new)
        self.assertEqual(row["result_ref"]["sha256"], new)
        self.assertEqual(row["status"], "error")

    def test_core_refusal_and_unknown_outcome_are_not_successful_receipts(self):
        for i, (status, state) in enumerate((('refused', 'rejected'), ('unknown', 'unknown')), 5):
            command_id = str(i) * 32
            control.enqueue(self.base, self.task.name, 'stop', command_id=command_id)
            observed = {'status': status, 'reason': 'controlled core result'}
            callback = mock.Mock(return_value=observed)
            result = control.consume_pending(self.base, callback)
            self.assertEqual(len(result), 1)
            self.assertFalse(result[0]['ok'])
            self.assertEqual(result[0]['state'], state)
            self.assertEqual(result[0]['reason'], observed['reason'])
            self.assertEqual(control.receipt(self.base, command_id)['result'], observed)
            self.assertEqual(control.consume_pending(self.base, callback), [])
            callback.assert_called_once()

    def test_arbitrary_paths_identity_mismatch_and_read_failures_not_empty(self):
        for value in ("../code-fixture", "C:\\secret", "/secret", "..", "a/b"):
            with self.assertRaises(reader.ReadError):
                reader.task_detail(self.base, value)
        with self.assertRaises(reader.ReadError):
            reader.payload(self.base, self.task.name, self.unit.name, self.ref["sha256"], kind="../../checkpoint")
        put(self.unit / "request.json", {"id": "different", "task_id": self.task.name})
        with self.assertRaises(reader.ReadError) as error:
            reader.task_detail(self.base, self.task.name)
        self.assertEqual(error.exception.status, 409)
        (self.task / "task.json").write_text("invalid", encoding="utf-8")
        with self.assertRaises(reader.ReadError):
            reader.tasks(self.base)

    def test_owner_and_device_handler_scope_parity(self):
        handlers = (deskapp._r_forge_tasks, deskapp._r_forge_task, deskapp._r_forge_events,
                    deskapp._r_forge_payload, deskapp._r_forge_control, deskapp._r_forge_receipt)
        query = {"task_id": self.task.name, "unit_id": self.unit.name, "sha256": self.ref["sha256"]}
        body = {"task_id": self.task.name, "action": "stop", "command_id": "a" * 32}
        with mock.patch.object(readers, "tree", return_value=self.base):
            for handler in handlers:
                with self.assertRaises(web.HTTPForbidden):
                    asyncio.run(handler(deskapp.Call({}, query, body, True, "device")))
            owner = asyncio.run(deskapp._r_forge_tasks(deskapp.Call({}, {}, None, False, "owner")))
            self.assertEqual(owner[0]["id"], self.task.name)
            device = asyncio.run(deskapp._r_forge(deskapp.Call({}, {}, None, False, "device")))
        text = json.dumps(device)
        for private in (self.task.name, self.unit.name, "fixture-model", "Private source review", "parser.py"):
            self.assertNotIn(private, text)
        for path in ("/api/forge/tasks", "/api/forge/task", "/api/forge/events", "/api/forge/payload", "/api/forge/control"):
            self.assertFalse(deskapp._scope_ok("device", path))
            self.assertIsNotNone(deskapp.match_route("GET", path)[0])

    def test_queue_returns_intent_then_actual_receipt_once(self):
        generation = reader.tasks(self.base)[0]["agents"][0]["observation"]["generation"]
        kwargs = dict(agent_id=self.unit.name, expected_generation=generation, command_id="1" * 32)
        queued = control.enqueue(self.base, self.task.name, "stop", **kwargs)
        self.assertEqual(queued["state"], "queued")
        callback = mock.Mock(return_value={"status": "unknown", "stops": [{"state": "unresolved"}]})
        outcome = control.consume_pending(self.base, callback)
        self.assertEqual(outcome[0]["result"]["status"], "unknown")
        self.assertEqual(outcome[0]["state"], "unknown")
        self.assertFalse(outcome[0]['ok'])
        control.consume_pending(self.base, callback)
        self.assertEqual(callback.call_count, 1)
        put(self.unit / "progress.json", {"generation": "later"})
        self.assertEqual(control.enqueue(self.base, self.task.name, "stop", **kwargs)["state"], "unknown")
        self.assertEqual(control.recent(self.base, self.task.name)["commands"][0]["command_id"], "1" * 32)
        with self.assertRaises(reader.ReadError):
            control.enqueue(self.base, self.task.name, "resume", **kwargs)
        with self.assertRaises(reader.ReadError):
            control.enqueue(self.base, self.task.name, [], command_id="2" * 32)

    def test_generation_change_after_queue_and_claim_crash_never_replays(self):
        generation = reader.tasks(self.base)[0]["agents"][0]["observation"]["generation"]
        control.enqueue(self.base, self.task.name, "pause", self.unit.name, generation, "3" * 32)
        put(self.unit / "progress.json", {"generation": "later"})
        callback = mock.Mock()
        self.assertEqual(control.consume_pending(self.base, callback)[0]["state"], "rejected")
        callback.assert_not_called()
        control.enqueue(self.base, self.task.name, "stop", command_id="4" * 32)
        folder = self.base / "memory/.control/forge"
        (folder / "processing").mkdir(exist_ok=True)
        (folder / "commands" / ("4" * 32 + ".json")).replace(folder / "processing" / ("4" * 32 + ".json"))
        outcome = control.consume_pending(self.base, callback)
        self.assertEqual(outcome[0]["state"], "unknown")
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
