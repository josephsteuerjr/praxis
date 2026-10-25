"""Real initial Telegram activation up to the first fake-provider boundary.

Standalone test: imports must start against the synthetic profile.
No Telegram, paid model, external network, or child process is required.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
CODE = Path(os.environ.get(
    "HELENE_TREE_SRC", str(DESK.parent / "helene/core"))).resolve()

# Do not silently reuse modules bound to another profile by test discovery.
if "agent" in sys.modules or "memory_life" in sys.modules:
    raise RuntimeError("Run this lifecycle test in a fresh Python process")

PROFILE = tempfile.TemporaryDirectory(prefix="helene-telegram-lifecycle-")
TREE = Path(PROFILE.name).resolve() / "data"
TREE.mkdir()
sys.path[:0] = [str(DESK), str(DESK / "localharness")]

# Remove inherited runtime routing before deriving the synthetic profile.
for key in list(os.environ):
    if key.startswith("PRAXIS_"):
        del os.environ[key]
os.environ["PRAXIS_BASE"] = str(TREE)

import boot

CFG = {
    "agent": {"name": "Fixture"},
    "owner": {"name": "Fixture owner"},
    "telegram": {
        "bot_token": "***REDACTED***",
        "owner_id": "11",
        "allow_from": "listed",
        "allowed_ids": ["22"],
    },
    "keat": False,  # This test concerns admission, not KEAT qualification.
}
os.environ.update(boot.env_knobs(CFG, tree=TREE))
os.environ.update(
    PRAXIS_BASE=str(TREE),
    PRAXIS_KEAT="off",
    PRAXIS_KEAT_CAPTURE="off",
)
sys.path.insert(0, str(CODE))

import sitecustomize
import agent
import botapi
from continuity import Continuity
import media
import memory_life
import owner_stop
import run_context
import run_manager
import run_resume
import runner
import telegram_inputs
import transport
import voice_admission


class AtFirstProvider(BaseException):
    """Do not let production Exception handlers turn this into delivery."""


class InitialTelegramLifecycle(unittest.TestCase):
    def test_real_activation_checkpoint_ack_and_first_provider_input(self):
        self.addCleanup(PROFILE.cleanup)
        trace = []
        seen = {}
        manager = run_manager.RunManager(TREE)

        cfg_path = TREE / "helene.json"
        cfg_path.write_text(json.dumps(CFG), encoding="utf-8")
        desks = transport.Desks(TREE, "Fixture owner", "Fixture")

        def keep(context):
            value = context.start()
            self.addCleanup(context.stop)
            return value

        # Keep actual pause/admission logic, but point its latches only at
        # this synthetic profile, never the installed Windows owner latch.
        keep(patch.object(owner_stop, "_tree", TREE))
        keep(patch.object(owner_stop, "seed_file",
                          return_value=TREE / "seed-stop.json"))
        keep(patch.object(owner_stop, "client_file",
                          return_value=TREE / "client-stop.json"))
        keep(patch.object(agent, "_RUN_MANAGER", manager))
        keep(patch.object(
            agent, "_MEDIA_SPOOL", media.MediaSpool(TREE / "media")))
        keep(patch.object(
            agent, "OWNER_INPUT_DRAIN", runner._drain_owner_inputs,
            create=True))

        adapter = Continuity(agent, desks, cfg_path,
                             lambda rid, room: trace.append("created"))
        adapter.install()

        keep(patch.object(runner, "_tree", TREE))
        keep(patch.object(runner, "_install_root", None))
        keep(patch.object(runner, "_life", memory_life))
        keep(patch.object(runner, "_agent", agent))
        keep(patch.object(runner, "_continuity", adapter))

        bot = botapi.BotTransport(SimpleNamespace(), TREE, memory_life, CFG)
        bot.me = {"id": 99}
        bot.client = Mock()
        keep(patch.object(runner, "_bot", bot))

        # UI heartbeat/pulse are not part of source-to-model admission.
        keep(patch.object(runner, "_set_busy"))
        keep(patch("turn_pulse.TurnPulse.start"))
        keep(patch("turn_pulse.TurnPulse.stop"))

        def forbidden(*args, **kwargs):
            raise AssertionError("external network is forbidden")

        keep(patch.object(socket.socket, "connect", side_effect=forbidden))
        keep(patch.object(socket, "create_connection", side_effect=forbidden))

        def persist(client, tree, peer, message):
            source = tree / "memory/telegram-media/1.ogg"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"synthetic-original-voice")
            return {
                "media_path": source.relative_to(tree).as_posix(),
                "media_kind": "audio",
                "media_mime": "audio/ogg",
            }

        def recognize(source):
            return voice_admission.resolve(
                source, lambda _: "FIRST-VOICE-TRANSCRIPT", signature="fixture")

        keep(patch("telegram_media.persist", side_effect=persist))
        recognition = keep(patch.object(
            runner, "_transcribe_path", side_effect=recognize))

        # Real transport ingress creates the actual outstanding record.
        bot._ingest({"message": {
            "message_id": 1, "date": time.time(),
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 11, "first_name": "Fixture owner"},
            "voice": {"file_id": "synthetic-voice", "duration": 1},
        }})
        original = bot.inputs.get("123:1")
        self.assertIsNotNone(original)
        self.assertEqual(original["run_id"], "")

        # A small sibling regression: initial file injection must compose
        # with the Telegram collector instead of being bypassed by return.
        attachment = TREE / (
            "memory/.control/desk_inbox/attachments/local-note/readme.txt")
        attachment.parent.mkdir(parents=True, exist_ok=True)
        attachment.write_bytes(b"synthetic owner attachment")
        initial_binding = runner._INITIAL_FILES.set({
            "room": "123", "source_id": "local-note",
            "paths": ["attachments/local-note/readme.txt"],
        })
        self.addCleanup(runner._INITIAL_FILES.reset, initial_binding)

        real_store = manager.store_result
        def store(*args, **kwargs):
            result = real_store(*args, **kwargs)
            if kwargs.get("event_kind") == "run_checkpoint":
                trace.append("checkpoint")
            return result
        keep(patch.object(manager, "store_result", side_effect=store))

        real_ack = bot.inputs.ack
        def acknowledge(key, nonce, run_id):
            # Read the real persisted, hash-checked checkpoint BEFORE ACK.
            record = bot.inputs.get(key)
            self.assertEqual(key, "123:1")
            self.assertEqual(record["run_id"], run_id)
            self.assertEqual(record["nonce"], original["nonce"])
            checkpoint = next(
                event for event in manager.iter_events(
                    run_id, reverse=True, strict=True)
                if event.get("kind") == "run_checkpoint")
            value = run_resume._checkpoint_value(
                manager, run_id, checkpoint,
                max_result_bytes=64 * 1024 * 1024)
            import run_executor
            self.assertEqual(value['iteration'], 0)
            self.assertEqual(run_executor._checkpoint(SimpleNamespace(checkpoint=value))['iteration'], 0)
            self.assertTrue(telegram_inputs.included(
                record, value["messages"]))
            self.assertIn("FIRST-VOICE-TRANSCRIPT",
                          json.dumps(value["messages"], ensure_ascii=False))
            self.assertIn("checkpoint", trace)
            result = real_ack(key, nonce, run_id)
            trace.append("ack")
            return result
        keep(patch.object(bot.inputs, "ack", side_effect=acknowledge))

        def first_provider(role, *, messages, **kwargs):
            self.assertEqual(role, "voice")
            current = run_context.current_run()
            self.assertIsNotNone(current)
            self.assertEqual(current.kind, "chat_turn")
            self.assertEqual(str(current.principal_id), "11")
            self.assertEqual(str(current.origin_chat_id), "123")
            self.assertEqual(str(current.delivery_chat_id), "123")

            persisted = manager.context(current.run_id)
            self.assertEqual(persisted.principal_id, current.principal_id)
            self.assertEqual(persisted.origin_chat_id, current.origin_chat_id)
            self.assertEqual(persisted.delivery_chat_id, current.delivery_chat_id)
            channel, _snapshot = agent._load_exact_run_channel(
                manager, persisted)
            self.assertTrue(channel.owner)
            self.assertTrue(channel.is_dm)
            self.assertEqual(str(channel.principal_id), "11")

            text = '\n'.join(m['content'] if isinstance(m.get('content'), str) else json.dumps(m.get('content'), ensure_ascii=False) for m in messages)
            self.assertIn("FIRST-VOICE-TRANSCRIPT", text)
            self.assertIn("[Telegram input 123:1;", text)
            target = manager.path(current.run_id) / "files/readme.txt"
            self.assertEqual(target.read_bytes(), attachment.read_bytes())
            self.assertIn(str(target), text)
            self.assertTrue(attachment.is_file())
            self.assertIsNone(bot.inputs.get("123:1"))
            self.assertLess(trace.index("checkpoint"), trace.index("ack"))
            seen["run_id"] = current.run_id
            trace.append("provider")
            raise AtFirstProvider()

        keep(patch.object(agent.llm, "configured", return_value=True))
        provider = keep(patch.object(
            agent.llm, "chat", side_effect=first_provider))

        # No manual RunContext, binding, _dialogue, _voice, projector, drain,
        # durable creation, checkpoint implementation, or activation mock.
        with self.assertRaises(AtFirstProvider):
            runner.handle_bot("123")

        self.assertTrue(seen, "the actual first provider boundary was not reached")
        provider.assert_called_once()
        recognition.assert_called_once()
        self.assertEqual(trace[-1], "provider")
        self.assertIsNone(adapter._activation)
        bot.client.assert_not_called()


if __name__ == "__main__":
    unittest.main()