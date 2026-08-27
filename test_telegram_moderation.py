from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import agent
import mtproto_runner
import telegram_moderation


class FakeClient:
    def __init__(self, *, sender=88, delete_error=None, ban_error=None):
        self.sender = sender
        self.delete_error = delete_error
        self.ban_error = ban_error
        self.deleted = []
        self.banned = []
        self.sent = []

    async def get_entity(self, peer):
        return types.SimpleNamespace(id=1240718803, title="AbstractDL")

    async def get_messages(self, entity, ids):
        return types.SimpleNamespace(id=ids, sender_id=self.sender)

    async def delete_messages(self, entity, ids, revoke=True):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append((entity.id, tuple(ids), revoke))

    async def edit_permissions(self, entity, sender, **permissions):
        if self.ban_error:
            raise self.ban_error
        self.banned.append((entity.id, sender, permissions))

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class ModerationActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "moderation.jsonl"
        self.patch = mock.patch.object(telegram_moderation, "LEDGER", self.ledger)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def run_action(self, client, *, peer=-1001240718803, message=77, sender=88,
                   action="delete"):
        with mock.patch.object(mtproto_runner, "client", client), \
             mock.patch.object(mtproto_runner, "_marked_peer_id", return_value=int(peer)):
            return asyncio.run(mtproto_runner._moderate_abstractdl(
                peer, message, sender, action, "praxis:self"))

    def test_wrong_peer_is_rejected_before_telegram(self):
        client = FakeClient()
        with self.assertRaises(PermissionError):
            self.run_action(client, peer=-100999)
        self.assertEqual(client.deleted, [])

    def test_sender_mismatch_is_fail_closed_and_receipted(self):
        client = FakeClient(sender=999)
        receipt = self.run_action(client)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error"], "sender_mismatch:999")
        self.assertEqual(client.deleted, [])
        self.assertTrue(self.ledger.exists())

    def test_delete_only_writes_completed_receipt_and_does_not_send(self):
        client = FakeClient()
        receipt = self.run_action(client)
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue(receipt["deleted"])
        self.assertFalse(receipt["banned"])
        self.assertEqual(client.banned, [])
        self.assertEqual(client.sent, [])
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_tampered_completed_receipt_cannot_suppress_transport(self):
        forged = {"schema": "praxis.telegram.moderation.v1", "ts": 1,
                  "previous_sha256": "", "idempotency_key":
                  telegram_moderation.operation_key(-1001240718803, 77, 88, "delete"),
                  "actor": "attacker", "peer_id": -1001240718803, "message_id": 77,
                  "sender_id": 88, "action": "delete", "status": "completed",
                  "deleted": True, "banned": False, "error": "",
                  "receipt_sha256": "attacker"}
        self.ledger.write_text(json.dumps(forged) + "\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.run_action(FakeClient())

    def test_concurrent_duplicate_is_serialized(self):
        client = FakeClient(sender=92)
        async def race():
            with mock.patch.object(mtproto_runner, "client", client), \
                 mock.patch.object(mtproto_runner, "_marked_peer_id",
                                   return_value=-1001240718803):
                return await asyncio.gather(
                    mtproto_runner._moderate_abstractdl(
                        -1001240718803, 91, 92, "delete", "praxis:self"),
                    mtproto_runner._moderate_abstractdl(
                        -1001240718803, 91, 92, "delete", "praxis:self"))
        results = asyncio.run(race())
        self.assertEqual(len(client.deleted), 1)
        self.assertTrue(any(result.get("replayed") for result in results))

    def test_crash_after_delete_can_resume_to_ban_when_message_is_gone(self):
        class CrashLedgerClient(FakeClient):
            async def get_messages(self, entity, ids):
                return None
        key = telegram_moderation.operation_key(
            -1001240718803, 93, 94, "delete_and_ban")
        telegram_moderation.append_receipt({
            "idempotency_key": key, "actor": "praxis:self",
            "peer_id": -1001240718803, "message_id": 93, "sender_id": 94,
            "action": "delete_and_ban", "status": "verified",
            "deleted": False, "banned": False, "sender_verified": True, "error": ""})
        client = CrashLedgerClient()
        result = self.run_action(client, message=93, sender=94,
                                 action="delete_and_ban")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["deleted"])
        self.assertTrue(result["banned"])
        self.assertEqual(client.deleted, [])
        self.assertEqual(len(client.banned), 1)

    def test_delete_and_ban(self):
        client = FakeClient()
        receipt = self.run_action(client, action="delete_and_ban")
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue(receipt["deleted"])
        self.assertTrue(receipt["banned"])
        self.assertEqual(client.banned[0][1], 88)
        self.assertEqual(client.banned[0][2], {"view_messages": False})

    def test_duplicate_restart_replays_without_transport(self):
        first = FakeClient()
        self.run_action(first)
        second = FakeClient()
        receipt = self.run_action(second)
        self.assertTrue(receipt["replayed"])
        self.assertEqual(second.deleted, [])

    def test_partial_ban_failure_retries_only_ban(self):
        first = FakeClient(ban_error=RuntimeError("no rights"))
        partial = self.run_action(first, action="delete_and_ban")
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["deleted"])
        second = FakeClient()
        complete = self.run_action(second, action="delete_and_ban")
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(second.deleted, [])
        self.assertEqual(len(second.banned), 1)

    def test_delete_failure_is_receipted_and_retry_rechecks_message(self):
        first = FakeClient(delete_error=RuntimeError("rpc"))
        failed = self.run_action(first)
        self.assertEqual(failed["status"], "failed")
        second = FakeClient()
        completed = self.run_action(second)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(second.deleted), 1)


class ToolBoundaryTests(unittest.TestCase):
    def test_schema_declares_action(self):
        enum = agent.TELEGRAM_ACCOUNT_TOOL["input_schema"]["properties"]["action"]["enum"]
        self.assertIn("moderate_abstractdl", enum)

    def test_tool_dispatches_only_parsed_fields(self):
        with mock.patch.object(agent, "_is_sovereign_actor", return_value=True), \
             mock.patch.dict(agent._TELETHON, {
                 "moderate_abstractdl": lambda **kw: json.dumps(kw["params"])
             }, clear=False):
            out = agent.tool_telegram_account(
                action="moderate_abstractdl",
                params_json=json.dumps({"peer_id": -1001240718803, "message_id": 1,
                                        "sender_id": 2, "decision": "delete"}))
        self.assertIn('"message_id": 1', out)


if __name__ == "__main__":
    unittest.main()
