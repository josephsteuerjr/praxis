import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import deskctl
from deskd import readers


class OwnerTelegramControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_owns_one_container_and_returns_the_actual_health(self):
        health = {"State": {"Running": True, "Status": "running",
                            "Health": {"Status": "unhealthy", "Log": [{"Output": "login_required\n"}]}}}
        with patch.object(deskctl, "CONTAINERS", (deskctl.OWNER_TELEGRAM,)), patch.object(
            deskctl, "_docker", AsyncMock(side_effect=[(204, b""), (200, json.dumps(health).encode())])
        ) as docker:
            result = await deskctl.owner_telegram_set(True)
        self.assertEqual(result, {"available": True, "enabled": True, "state": "login_required", "ok": True})
        self.assertEqual([call.args[0] for call in docker.await_args_list],
                         ["/containers/praxis-owner-telegram/start", "/containers/praxis-owner-telegram/json"])

    async def test_unconfigured_service_cannot_mutate_docker(self):
        with patch.object(deskctl, "CONTAINERS", ("praxis",)), patch.object(
            deskctl, "_docker", AsyncMock()
        ) as docker:
            self.assertEqual(await deskctl.owner_telegram_set(False), {"available": False, "ok": False})
            docker.assert_not_awaited()


class OwnerTelegramStepTests(unittest.TestCase):
    def test_saved_receipt_and_nested_tool_are_projected_in_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "manifest.json").write_text("{}", encoding="utf-8")
            result = json.dumps({"status": "ok", "receipt": {"chat": -42, "message_id": 17}})
            events = [
                {"kind": "tool_started", "tool": "python", "call_id": "outer"},
                {"kind": "hand_requested", "tool": "yegor_send", "call_id": "child", "parent_call_id": "outer"},
                {"kind": "tool_started", "tool": "yegor_send", "call_id": "child", "args": {"chat": "-42"}},
                {"kind": "tool_result", "call_id": "child", "result": {"inline": {"head": result}}},
            ]
            (path / "events.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n", encoding="utf-8")
            with patch.object(readers, "run_dir", return_value=path), patch.object(readers, "_run_words", return_value={}):
                detail = readers.run_detail("synthetic")
        tools = detail["iterations"][0]["tools"]
        self.assertEqual(len(tools), 1)
        child, = tools[0]["child_tools"]
        self.assertEqual(child["tool"], "yegor_send")
        self.assertEqual(child["lens"]["message_id"], "17")
        self.assertEqual(child["lens"]["chat"], "-42")
