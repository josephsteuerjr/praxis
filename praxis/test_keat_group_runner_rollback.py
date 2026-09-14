"""Runner-to-provider rollback proofs for the ordinary AbstractDL root group.

The transport, voice envelope, agent prompt assembly and provider selection are real.  Only
Telegram delivery, expensive prompt sources/durability and the model itself are stand-ins.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
import time
import unittest
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
import keat_live
import media
import mtproto_runner as runner


ROOT = "-1001240718803"


class _Client:
    async def send_message(self, *args, **kwargs):
        return SimpleNamespace(id=9001)


class RunnerRollback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy = Path(self.tmp.name) / "policy.json"
        self.policy_doc = {
            "schema": "keat.capture-policy.v1",
            "namespace": "runner-rollback",
            "root": self.tmp.name,
            "enrollments": [{
                "mode": "group", "stream": ROOT,
                "capture": {"issuer": "offline-test", "policy_revision": "root.v1",
                            "audience": ["group:" + ROOT], "transfer": "none",
                            "presence_hidden": False},
            }],
        }
        self.policy.write_text(json.dumps(self.policy_doc), encoding="utf-8")
        self.env = {
            "PRAXIS_KEAT_CAPTURE": "on",
            "PRAXIS_KEAT_CAPTURE_POLICY": str(self.policy),
            "PRAXIS_KEAT_ROOT": self.tmp.name,
            "PRAXIS_KEAT": "serve",
            "PRAXIS_KEAT_PAIRS": '[["group","-1001240718803"]]',
            "PRAXIS_DIALOGUE_ROLES": "1",
            "PRAXIS_FRAME_V6": "off",
            "PRAXIS_CHAT_REPLY_HAND": "off",
        }

    def _capture(self, mid: int, role: str, content: str, principal: int):
        return keat_live.capture_ingress(
            ROOT, is_dm=False, source_id=str(mid),
            direction="out" if role == "assistant" else "in",
            payload={"role": role, "content": content,
                     "actor": "Praxis" if role == "assistant" else "Member"},
            root_chat_id=ROOT, principal_id=str(principal), ordinary_root=True,
        )

    def _wake(self, sidecar, *, media_refs=()):
        frozen_turns = (
            (True, "Praxis: old answer", "old answer"),
            (False, "Bob: frozen question", "Bob: frozen question"),
        )
        return runner.GroupWake(
            message_id=2, message_ts=time.time(), kind="reply", speaker="Bob",
            sender_id=222, owner=False, known=True, family=False,
            context_snapshot="Praxis: old answer\nBob: frozen question",
            reply_targets_snapshot=(), media_snapshot=tuple(media_refs),
            turns_snapshot=frozen_turns, occurrence_sidecar=copy.deepcopy(sidecar),
        )

    async def _pass(self, sidecar, *, env=None, media_refs=(), final_fault=None):
        """Run the real _run_pass -> voice_turn_envelope -> _voice_impl -> _model_call path."""
        wake = self._wake(sidecar, media_refs=media_refs)
        current_turns = wake.turns_snapshot + (
            (False, "Alice: late traffic", "Alice: late traffic"),
        )
        canonical = {}
        provider = {}
        final_intents = []

        def terminal(**kwargs):
            # This is the sole narrow splice: retain the real provider gate while avoiding
            # the unrelated tool loop.  It also observes the canonical tape before KEAT.
            canonical.update(system=copy.deepcopy(kwargs["system"]),
                             messages=copy.deepcopy(kwargs["messages"]),
                             tools=copy.deepcopy(kwargs["tools"]))
            response = agent._model_call(kwargs["system"], kwargs["messages"], kwargs["tools"])
            return str(response.text or "")

        def model(*args, **kwargs):
            system = kwargs.get("system", args[0] if len(args) > 0 else None)
            messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
            tools = kwargs.get("tools", args[2] if len(args) > 2 else None)
            provider.update(system=copy.deepcopy(system), messages=copy.deepcopy(messages),
                            tools=copy.deepcopy(tools))
            return SimpleNamespace(blocks=[], text="", stop_reason="end_turn")

        active_env = dict(self.env if env is None else env)
        meta = {ROOT: {"entity": int(ROOT), "peer_id": ROOT, "is_dm": False,
                       "is_owner": False, "known": True, "family": False,
                       "name": "Bob", "sender_id": 222, "title": "AbstractDL",
                       "size": 20, "addressed": True, "addressed_mid": 2,
                       "room_mode": "normal", "room_nature": False}}
        def store_result(*args, **kwargs):
            if final_fault and kwargs.get("event_kind") == "model_input":
                intent = json.loads(args[1])
                final_intents.append(intent)
                if sidecar is not None:
                    self.assertTrue(intent["keat"]["served"])
                    self.assertNotIn("Alice: late traffic", str(intent["messages"]))
                    self.assertIn("frame_v6_live_system", intent)
                # Mutation after successful selection, before final provider authority.
                os.environ["PRAXIS_KEAT_ROOT"] = self.tmp.name + "/revoked-root"
        store = SimpleNamespace(store_result=store_result)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for mocked in (
            patch.dict(os.environ, active_env, clear=True),
            patch.object(runner, "client", _Client()),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", {ROOT: wake}),
            patch.object(runner, "_last_pass", defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", defaultdict(lambda: deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", defaultdict(lambda: deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_resolve_room_mode", return_value=("normal", False)),
            patch.object(runner, "_group_context_frozen",
                         return_value=("Praxis: old answer\nBob: frozen question\nAlice: late traffic",
                                       current_turns)),
            patch.object(runner, "_maybe_compact", return_value=None),
            patch.object(runner, "_buf_push", return_value=None),
            patch.object(agent.llm, "configured", return_value=True),
            patch.object(agent.llm, "chat", side_effect=model),
            patch.object(agent, "_create_durable_run", return_value=None),
            patch.object(agent.turns, "begin", return_value={"tools": [], "out": ""}),
            patch.object(agent.turns, "record"),
            patch.object(agent, "_archive_run_media", return_value={}),
            patch.object(agent, "_presence_evidence", return_value=""),
            patch.object(agent, "_presence_frame", return_value=""),
            patch.object(agent, "_build_prompt_parts", return_value=("PERSONA", "DYNAMIC", "")),
            patch.object(agent, "offered_tools_for", return_value=[]),
            patch.object(agent, "_terminal_tool_loop", side_effect=terminal),
            patch.object(agent.frame_shadow, "enabled", return_value=False),
            patch.object(agent.frame_shadow, "_read", return_value="K"),
            patch.object(agent.frame_measure, "assemble", return_value={
                "system": "K\n\n---\nK" + agent.frame_shadow._SEP_E + " economical"}),
            patch.object(agent.run_context, "current_run", return_value=SimpleNamespace(run_id="r")),
            patch.object(agent, "_runs", return_value=store),
            patch.object(agent, "_run_status_gate"),
            patch.object(agent, "_run_event_strict"),
        ):
            stack.enter_context(mocked)
        # Media parsing/durable spooling is orthogonal to rollback. Preserve the real
        # model_text chosen by the envelope; a non-empty ref still disables adoption.
        def media_prompt(convo_text, refs, ctx, *, model_text=None):
            text = model_text if model_text is not None else convo_text
            return convo_text, ([{"type": "text", "text": text},
                                  {"type": "image", "path": "/offline/image.png"}]
                                 if refs else text)
        stack.enter_context(patch.object(agent, "_media_prompt", side_effect=media_prompt))
        stack.enter_context(patch.object(
            agent, "_prepare_outbound_guard", return_value=([], "", (), "")))
        try:
            await runner._run_pass(ROOT)
        finally:
            stack.close()
        if final_fault:
            self.assertTrue(final_intents, "fault must occur after selected intent persistence")
        self.assertTrue(canonical and provider, "pass did not reach the model")
        return canonical, provider

    async def test_success_uses_frozen_native_current_but_keeps_late_canonical(self):
        with patch.dict(os.environ, self.env, clear=True):
            old = self._capture(1, "assistant", "Praxis: old answer", 999)
            current = self._capture(2, "user", "Bob: frozen question", 222)
        sidecar = {"history": [[old]], "current": [current],
                   "projection_history": [{"role": "assistant", "content": "Praxis: old answer"}],
                   "projection_current": "Bob: frozen question"}
        baseline, baseline_provider = await self._pass(None)
        canonical, selected = await self._pass(sidecar)

        self.assertEqual(canonical, baseline)
        self.assertEqual(baseline_provider, baseline)
        self.assertIn("Alice: late traffic", str(canonical["messages"]))
        self.assertNotIn("Alice: late traffic", str(selected["messages"]))
        self.assertEqual(selected["messages"][-1]["content"], "Bob: frozen question")
        self.assertEqual(selected["system"], baseline["system"])
        self.assertEqual(selected["tools"], baseline["tools"])

    async def test_frame_candidate_rejections_restore_whole_legacy_request(self):
        self.env["PRAXIS_FRAME_V6"] = "serve"
        await self.test_adversarial_states_are_byte_exact_no_sidecar_legacy()

    async def test_readiness_loss_after_selection_restores_whole_legacy(self):
        self.env["PRAXIS_FRAME_V6"] = "serve"
        with patch.dict(os.environ, self.env, clear=True):
            old = self._capture(1, "assistant", "Praxis: old answer", 999)
            current = self._capture(2, "user", "Bob: frozen question", 222)
        sidecar = {"history": [[old]], "current": [current],
                   "projection_history": [{"role": "assistant", "content": "Praxis: old answer"}],
                   "projection_current": "Bob: frozen question"}
        baseline, _ = await self._pass(None)
        canonical, actual = await self._pass(sidecar, final_fault=True)
        self.assertEqual(canonical, baseline)
        self.assertEqual(json.dumps(actual, ensure_ascii=False).encode(),
                         json.dumps(baseline, ensure_ascii=False).encode())

    async def test_adversarial_states_are_byte_exact_no_sidecar_legacy(self):
        scenarios = ("revoked_edit", "missing_policy", "invalid_policy",
                     "media", "selector_rollback", "readiness_mismatch")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                # A distinct namespace prevents a revocation subcase contaminating another.
                self.policy_doc["namespace"] = "runner-rollback-" + scenario
                self.policy.write_text(json.dumps(self.policy_doc), encoding="utf-8")
                with patch.dict(os.environ, self.env, clear=True):
                    old = self._capture(1, "assistant", "Praxis: old answer", 999)
                    current = self._capture(2, "user", "Bob: frozen question", 222)
                sidecar = {"history": [[old]], "current": [current],
                           "projection_history": [{"role": "assistant",
                                                   "content": "Praxis: old answer"}],
                           "projection_current": "Bob: frozen question"}
                env = dict(self.env)
                refs = ()
                if scenario == "revoked_edit":
                    with patch.dict(os.environ, self.env, clear=True):
                        keat_live.invalidate_native(ROOT, 2)
                elif scenario == "missing_policy":
                    env.pop("PRAXIS_KEAT_CAPTURE_POLICY")
                elif scenario == "invalid_policy":
                    bad = copy.deepcopy(self.policy_doc)
                    bad["enrollments"][0]["capture"]["audience"] = ["group:wrong"]
                    self.policy.write_text(json.dumps(bad), encoding="utf-8")
                elif scenario == "media":
                    refs = (object(),)
                elif scenario == "readiness_mismatch":
                    env["PRAXIS_KEAT_ROOT"] = self.tmp.name + "/wrong-root"
                elif scenario == "selector_rollback":
                    env["PRAXIS_KEAT_PAIRS"] = '[["group","-1001240718804"]]'

                baseline, baseline_provider = await self._pass(None, env=env, media_refs=refs)
                canonical, actual = await self._pass(sidecar, env=env, media_refs=refs)
                self.assertEqual(canonical, baseline)
                self.assertEqual(actual, baseline_provider)
                self.assertEqual(json.dumps(actual, ensure_ascii=False).encode(),
                                 json.dumps(baseline, ensure_ascii=False).encode())
                self.assertEqual(actual, baseline,
                                 "fallback changed system/messages/tools bytes")
                self.assertIn("Alice: late traffic", str(actual["messages"]))


if __name__ == "__main__":
    unittest.main()
