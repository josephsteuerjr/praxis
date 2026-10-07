"""Synthetic auth: admission, private queue, active primary and redacted receipts."""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
from deskd import relay_auth
import deskapp
import doctor
from aiohttp.test_utils import TestClient, TestServer

AUTH = {"tokens": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                   "id_token": "synthetic-id", "account_id": "synthetic-account"}, "last_refresh": "2026-10-07T00:00:00Z"}


class AuthQueue(unittest.TestCase):
    def test_queued_during_turn_then_updates_actual_primary_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            relay_auth.prepare(tree)
            primary = tree / "relay/local_auth/accounts/primary/auth.json"
            primary.parent.mkdir(parents=True)
            primary.write_text(json.dumps(AUTH))
            new = copy.deepcopy(AUTH)
            new["tokens"]["access_token"] += "-fresh"
            ask = relay_auth.enqueue(tree, new)
            called = []
            def apply(save):
                called.append("stop")
                save()
                called.append("spawn")
                return True
            relay_auth.apply_pending(tree, idle=False, activate=apply)
            self.assertEqual(relay_auth.receipt(tree, ask["id"])["phase"], "queued")
            self.assertEqual(called, [])
            relay_auth.apply_pending(tree, idle=True, activate=apply)
            self.assertEqual(called, ["stop", "spawn"])
            self.assertEqual(json.loads(primary.read_text()), new)
            self.assertEqual(json.loads((tree / "relay/local_auth/auth.json").read_text()), new)
            self.assertEqual(relay_auth.receipt(tree, ask["id"])["phase"], "active")
            for file in [(tree / relay_auth.PUBLIC), *relay_auth.queue_dir(tree).glob("*.receipt")]:
                self.assertNotIn("synthetic-access", file.read_text())
                self.assertNotIn("synthetic-refresh", file.read_text())
            self.assertFalse(list(relay_auth.queue_dir(tree).glob("*.pending")))
            if os.name == "posix":
                self.assertEqual(primary.stat().st_mode & 0o777, 0o600)

    def test_other_account_requires_explicit_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            relay_auth.prepare(tree)
            old = tree / "relay/local_auth/auth.json"
            old.parent.mkdir(parents=True)
            old.write_text(json.dumps(AUTH))
            new = copy.deepcopy(AUTH)
            new["tokens"]["account_id"] = "other"
            activate = lambda save: (save(), True)[1]
            ask = relay_auth.enqueue(tree, new)
            relay_auth.apply_pending(tree, idle=True, activate=activate)
            self.assertEqual(relay_auth.receipt(tree, ask["id"])["phase"], "conflict")
            self.assertEqual(json.loads(old.read_text()), AUTH)
            ask = relay_auth.enqueue(tree, new, replace=True)
            relay_auth.apply_pending(tree, idle=True, activate=activate)
            self.assertEqual(json.loads(old.read_text()), new)

    def test_doctor_distinguishes_presence_process_and_persistence(self):
        out = doctor.decide_relay_auth({"state": "ok", "data": {"login_present": True, "phase": "saved", "persistence": "container-layer"}})
        self.assertEqual(out["verdict"], doctor.VERDICT_WATCH)
        self.assertIn("не подтверждён", out["note"])
        self.assertIn("потерян", out["note"])


class AuthHTTP(unittest.IsolatedAsyncioTestCase):
    async def test_owner_header_can_queue_but_device_cannot(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HELENE_SERVER_AUTH_IMPORT": "1"}), patch.object(deskapp.readers, "tree", return_value=Path(tmp)), patch.object(deskapp, "TOKEN", "synthetic-owner"), patch.object(deskapp, "_device_ok", side_effect=lambda key: key == "synthetic-device"), patch.object(deskapp, "_host_ok", return_value=True), patch.object(deskapp, "_origin_ok", return_value=(True, "")):
            relay_auth.prepare(tmp)
            client = TestClient(TestServer(deskapp.build_app()))
            await client.start_server()
            try:
                for key, expected in [("synthetic-device", 403), ("", 403), ("synthetic-owner", 200)]:
                    response = await client.post("/api/relay/auth/import", headers={"Authorization": "Bearer " + key}, json={"auth": AUTH})
                    self.assertEqual(response.status, expected)
                    self.assertNotIn("synthetic-access", await response.text())
                self.assertEqual(len(list(relay_auth.queue_dir(tmp).glob("*.pending"))), 1)
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()
