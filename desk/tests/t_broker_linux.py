"""Linux routing, complete tool schema, and the mode API round trip."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))
import broker
import modes


class LinuxBroker(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("HELENE_TREE_SRC"), "requires actual packaged agent tree")
    def test_actual_agent_frame_offers_the_full_schema_with_pointers_on(self):
        sys.path.insert(0, os.environ["HELENE_TREE_SRC"])
        import agent
        import frame_shadow
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "on"}):
            broker.install(agent, Path(root), {"service": {"session0": True}})
            tools = agent.offered_tools_for(agent.ChannelContext(owner=True))
            found = [t for t in tools if t.get("name") == "broker_request"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["input_schema"], broker.TOOL["input_schema"])
            self.assertIn("broker_request", frame_shadow._block_hands(agent.catalog_tools_for(agent.ChannelContext(owner=True))))

    def test_install_offers_executable_tool_and_full_schema_once(self):
        agent = SimpleNamespace(BASE_TOOLS=[], TOOL_IMPL={}, NATIVE_HAND_NAMES=frozenset({"shell"}))
        with tempfile.TemporaryDirectory() as root, patch.object(broker, "LINUX", True), patch.object(broker, "POSIX_PATHS", True), patch.object(broker, "HAS_BROKER", True), patch.object(broker, "TOOL", broker.tool_schema(mac=False, linux=True)):
            for _ in range(2): broker.install(agent, Path(root), {"service": {"session0": True}})
        self.assertEqual([t["name"] for t in agent.BASE_TOOLS], ["broker_request"])
        schema = agent.BASE_TOOLS[0]
        # This is the exact schema consumed by the agent's offered_tools_for frame.
        self.assertIn("broker_request", agent.TOOL_IMPL)
        self.assertIn("broker_request", agent.NATIVE_HAND_NAMES)
        self.assertEqual(schema["input_schema"]["properties"]["op"]["enum"], ["ping", "exec"])
        self.assertIn("без polkit", schema["description"])
        self.assertIn("пароль", schema["description"])
        self.assertIn("timeout_sec", schema["input_schema"]["properties"])

    def test_session0_uses_socket_without_window_or_token_and_retains_failed_command_code(self):
        with tempfile.TemporaryDirectory() as root, patch.object(broker, "LINUX", True), patch.object(broker, "POSIX_PATHS", True), patch.object(broker, "call_linux", return_value={"ok": True, "code": 7, "out": "out", "err": "err", "ms": 5}) as call:
            b = broker.Broker(Path(root), {"service": {"session0": True}})
            with patch.object(b, "desk", side_effect=AssertionError("no window in session0")):
                result = b.ask("exec", "/usr/bin/id", [], "проверка прав", 9, 0)
            self.assertIn("код возврата 7", result)
            self.assertIn("err", result)
            self.assertEqual(call.call_args.args[0]["argv"], ["/usr/bin/id"])
            self.assertEqual(call.call_args.args[0]["timeout_sec"], 9)
            self.assertFalse(broker.asks_path(Path(root)).exists())

    def test_interactive_queues_for_password_window_and_never_uses_root_socket(self):
        with tempfile.TemporaryDirectory() as root, patch.object(broker, "LINUX", True), patch.object(broker, "POSIX_PATHS", True), patch.object(broker, "DIRECT", False), patch.object(broker, "call_linux", side_effect=AssertionError("root socket without consent")):
            b = broker.Broker(Path(root), {"service": {"session0": False}})
            with patch.object(b, "desk", return_value=(True, "window")), patch.object(b, "wait", return_value={"decision": "refused", "note": "пароль не дан"}):
                result = b.ask("exec", "/usr/bin/id", [], "проверка прав", 9, 0)
            self.assertIn("пароль не дан", result)
            self.assertEqual(b.asks()[0]["cmd"], "/usr/bin/id")

    def test_mode_api_preserves_ladder_across_json_roundtrip(self):
        for fence in ("sandbox", "interactive"):
            cfg = {"agent_mode": fence, "service": {"session0": True}}
            picture = modes.describe(modes.resolve(cfg, installed=True))
            reopened = json.loads(json.dumps(picture))
            self.assertEqual(reopened["ladder_name"], "session0")
            self.assertEqual(reopened["name"], fence)
            self.assertTrue(reopened["session0_set"])
            modes.apply(cfg, installed=True)
            self.assertTrue(cfg["service"]["session0"])

if __name__ == "__main__": unittest.main(verbosity=2)
