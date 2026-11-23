"""Telegram authority for nested hands and resume in the edition (Atlas, AbstractDL, 15-16.11).

The core re-checks the turn's human authority before every nested h.hand and every resume,
against PRAXIS_OWNER_ID and social.known_ids. The edition sets neither: its owner is
telegram.owner_id in helene.json and its gate is the bot's allow_from/allowed_ids/admitted.
Only the owner's private chat had an edition check, so the owner writing in a group was
refused as "no longer the configured owner" and every nested hand there ended InDoubt
before it started. Stub agent and bot; no model, network or user data.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "localharness"), str(ROOT)]
from continuity import Continuity  # noqa: E402


class DurableExecutionError(RuntimeError):
    pass


class Runtime:
    def __init__(self, channel, context):
        self.channel, self.plan = channel, SimpleNamespace(context=context, run_id=context.run_id)


def context(principal, room="-100777", scope="group"):
    return SimpleNamespace(run_id="run-fixture", principal_id=principal, delivery_chat_id=room,
                           origin_chat_id=room, scope=scope)


def channel(**flags):
    values = dict(owner=False, known=True, family=False, is_dm=False)
    values.update(flags)
    return SimpleNamespace(**values)


class Bot:
    def __init__(self, allowed):
        self.allowed = set(allowed)

    def is_allowed(self, sender):
        return str(sender) in self.allowed


class TelegramAuthority(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / "helene.json"
        self.write(owner="101")
        self.bot = Bot({"101", "202"})
        self.core = []

        def core_authority(runtime):
            self.core.append(runtime)
            raise DurableExecutionError("core: paused human-owner run no longer belongs to the configured owner")

        self.agent = SimpleNamespace(
            DurableExecutionError=DurableExecutionError,
            _create_durable_run=lambda **kwargs: None,
            resume_durable_run=lambda run_id: None,
            _AgentResumeRuntime=type("_AgentResumeRuntime", (), {"_validate_current_authority": core_authority}),
            run_snapshot=SimpleNamespace(write=lambda **kwargs: None),
            _runs=lambda: SimpleNamespace(create=lambda context, markdown: None))
        self.continuity = Continuity(self.agent, None, self.config, lambda *args: None,
                                     telegram=lambda: self.bot)
        self.continuity.install()

    def write(self, owner):
        self.config.write_text(json.dumps({"owner": {"name": "Egor"}, "telegram": {"owner_id": owner}}),
                               encoding="utf-8")

    def check(self, runtime):
        return self.agent._AgentResumeRuntime._validate_current_authority(runtime)

    def test_owner_in_a_group_passes_without_praxis_owner_id(self):
        self.check(Runtime(channel(owner=True), context("101")))
        self.assertEqual(self.core, [])

    def test_owner_change_is_seen_before_the_next_nested_effect(self):
        self.write(owner="555")
        with self.assertRaisesRegex(DurableExecutionError, "configured owner"):
            self.check(Runtime(channel(owner=True), context("101")))
        self.write(owner="")
        with self.assertRaisesRegex(DurableExecutionError, "configured owner"):
            self.check(Runtime(channel(owner=True), context("101")))

    def test_admitted_group_member_passes_and_revocation_refuses(self):
        runtime = Runtime(channel(known=True), context("202"))
        self.check(runtime)
        self.bot.allowed.discard("202")
        with self.assertRaisesRegex(DurableExecutionError, "no longer admitted"):
            self.check(runtime)
        self.bot = None
        with self.assertRaisesRegex(DurableExecutionError, "no longer admitted"):
            self.check(Runtime(channel(known=True), context("101")))

    def test_unknown_sender_keeps_the_core_rules(self):
        with self.assertRaises(DurableExecutionError):
            self.check(Runtime(channel(known=False), context("42")))
        self.assertEqual(len(self.core), 1)

    def test_window_and_owner_private_chat_keep_their_existing_checks(self):
        calls = []
        self.continuity.verify_local_owner = lambda run_id, ctx: calls.append(("window", run_id))
        self.continuity.verify_delivery_owner = lambda run_id, ctx: calls.append(("dm", run_id))
        import transport
        room = transport.ROOM_DEFAULT
        self.check(Runtime(channel(owner=True, is_dm=True), context("unknown", room=room, scope="owner")))
        self.check(Runtime(channel(owner=True, is_dm=True), context("101", room="101", scope="owner")))
        self.assertEqual(calls, [("window", "run-fixture"), ("dm", "run-fixture")])
        self.assertEqual(self.core, [])


if __name__ == "__main__":
    unittest.main()
