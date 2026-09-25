"""Connected ordinary-DM KEAT regressions at native and provider boundaries.

These tests deliberately use the runner's durable Telegram recording/projection and
``agent._model_call`` with only the final LLM transport replaced.  Groups never have
a positive case here: this file proves only the separately enrolled ``dm`` adapter.
"""
import copy
import json
import os
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("PRAXIS_TEST", "1")

import agent
import keat_live
import memory_life as ml
import mtproto_runner as runner
from test_life_places import Base


class OrdinaryDMIngressTests(Base):
    def setUp(self):
        super().setUp()
        self.policy_path = self.tmp / "dm-policy.json"
        self.persisted = patch.object(runner, "_persisted_life_sources", set())
        self.persisted.start()
        self.addCleanup(self.persisted.stop)

    @staticmethod
    def capture(mode="dm", stream="42", **overrides):
        value = {
            "issuer": "test-admin",
            "policy_revision": "dm-p1",
            "audience": ["dm:" + stream] if mode == "dm" else ["owner"],
            "transfer": "none",
            "presence_hidden": False,
        }
        value.update(overrides)
        return {"mode": mode, "stream": stream, "capture": value}

    def policy(self, enrollments=None):
        value = {
            "schema": "keat.capture-policy.v1",
            "namespace": "ordinary-dm-native-test",
            "root": str(self.tmp),
            "enrollments": enrollments or [self.capture()],
        }
        self.policy_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def env(self):
        return patch.dict(os.environ, {
            "PRAXIS_KEAT_CAPTURE": "on",
            "PRAXIS_KEAT_CAPTURE_POLICY": str(self.policy_path),
            "PRAXIS_KEAT": "serve",
            "PRAXIS_KEAT_PAIRS": '[["dm","42"]]',
        }, clear=False)

    @staticmethod
    def ctx(**changes):
        values = dict(chat_id="42", room_id="42", principal_id="9001",
                      is_dm=True, owner=False, known=True, family=False,
                      hide_identity_load=False)
        values.update(changes)
        return agent.ChannelContext(**values)

    def record(self, mid, line, direction="in"):
        return runner._record_life_message(
            "42", line, actor="Praxis" if direction == "out" else "Alice",
            direction=direction, source_id=mid, is_dm=True,
            ts=1700000000 + mid, dedupe_key=f"dm:42:{mid}:{direction}",
            capture_live=True,
        )

    @staticmethod
    def provider_boundary(stack, results=None):
        results = [] if results is None else results
        stack.enter_context(patch.object(
            agent.run_context, "current_run", return_value=SimpleNamespace(run_id="dm-run")))
        stack.enter_context(patch.object(
            agent, "_runs", return_value=SimpleNamespace(
                store_result=lambda *args, **kwargs: results.append((args, kwargs)))))
        stack.enter_context(patch.object(agent, "_run_status_gate"))
        stack.enter_context(patch.object(agent, "_run_event_strict"))
        stack.enter_context(patch.object(agent.frame_trace, "metadata_for", return_value=None))
        stack.enter_context(patch.object(agent.frame_measure, "measure", return_value=None))
        stack.enter_context(patch.object(
            agent.frame_serve, "select", side_effect=lambda **kw: (kw["system"], None)))
        return stack.enter_context(patch.object(
            agent.llm, "chat", return_value=SimpleNamespace(
                blocks=[], text="provider answer", stop_reason="end_turn")))

    @staticmethod
    def voice_construction_boundary(stack):
        """Remove unrelated memory/frame rendering while retaining the real model seam."""
        stack.enter_context(patch.object(
            agent, "_build_prompt_parts", return_value=("persona", "dynamic", "")))
        stack.enter_context(patch.object(agent, "_system", return_value="dm system"))
        stack.enter_context(patch.object(
            agent, "_with_context_evidence", side_effect=lambda message, *_args: message))
        stack.enter_context(patch.object(
            agent.frame_layout, "tape",
            # ИЗДАНИЕ: лента зовётся с `hands=` (ленту-вызовы включает издание) — заглушка его принимает.
            side_effect=lambda rows, hands=False: copy.deepcopy(rows)))
        stack.enter_context(patch.object(agent.frame_shadow, "enabled", return_value=False))

    def test_exact_authority_context_and_trust_tiers_do_not_change_audience(self):
        self.policy()
        with self.env():
            # known, unknown, and family are social facts, not authority widening.
            # The ordinary known/non-family case is also the runner's trusted-contact shape.
            for known, family in ((True, False), (False, False), (True, True)):
                with self.subTest(known=known, family=family):
                    ctx = self.ctx(known=known, family=family)
                    self.assertTrue(ctx.is_dm)
                    self.assertFalse(ctx.owner)
                    self.assertFalse(ctx.owner_audience)
                    self.assertEqual(ctx.room_chat_id, ctx.chat_id)
                    self.assertFalse(ctx.hide_identity_load)
                    state = keat_live.capture_turn(ctx, "hello", [])
                    self.assertIsNotNone(state)
                    self.assertEqual(state.enrollment["mode"], "dm")
                    self.assertEqual(state.enrollment["capture"], {
                        "issuer": "test-admin", "policy_revision": "dm-p1",
                        "audience": ["dm:42"], "transfer": "none",
                        "presence_hidden": False,
                    })

            invalid = (
                self.ctx(is_dm=False), self.ctx(owner=True),
                self.ctx(room_id="other"), self.ctx(hide_identity_load=True),
                self.ctx(principal_id=agent.PRAXIS_SELF_PRINCIPAL),
            )
            for ctx in invalid:
                with self.subTest(ctx=ctx):
                    self.assertIsNone(keat_live.capture_turn(ctx, "hello", []))

        # Each unsafe authority shape is unsupported, rather than silently broadened.
        for override in ({"audience": ["dm:7"]}, {"audience": ["dm:42", "owner"]},
                         {"transfer": "private"}, {"presence_hidden": True}):
            self.policy([self.capture(**override)])
            with self.env(), self.subTest(override=override):
                self.assertIsNone(keat_live.capture_turn(self.ctx(), "hello", []))

    def test_native_capture_selects_owner_or_dm_uniquely_and_adoption_rechecks_context(self):
        self.policy([self.capture(), self.capture(mode="owner")])
        with self.env():
            self.assertIsNone(keat_live.capture_ingress(
                "42", is_dm=True, source_id="1", direction="in",
                payload={"role": "user", "content": "Alice: ambiguous", "actor": "Alice"}))

        self.policy()
        with self.env():
            self.record(1, "Alice: earlier")
            self.record(2, "Praxis: answer", "out")
            self.record(3, "Alice: next")
            ml.rebuild_state("42")
            sidecar = {}
            history, current = runner._dm_dialogue("42", occurrence_sidecar=sidecar)
            self.assertEqual(history, [{"role": "user", "content": "earlier"},
                                       {"role": "assistant", "content": "answer"}])
            self.assertEqual(current, "next")
            state = keat_live.adopt_projection(self.ctx(), history, current, sidecar)
            self.assertIsNotNone(state)
            self.assertEqual(state.enrollment["mode"], "dm")
            for bad in (self.ctx(owner=True), self.ctx(is_dm=False),
                        self.ctx(room_id="7"), self.ctx(hide_identity_load=True)):
                with self.subTest(bad=bad):
                    self.assertIsNone(keat_live.adopt_projection(bad, history, current, sidecar))

    def test_runner_projection_reaches_actual_provider_with_exact_dm_binding(self):
        self.policy()
        with self.env():
            self.record(10, "Alice: old question")
            self.record(11, "Praxis: old answer", "out")
            self.record(12, "Alice: new question")
            ml.rebuild_state("42")
            sidecar = {}
            history, current = runner._dm_dialogue("42", occurrence_sidecar=sidecar)
            state = keat_live.adopt_projection(self.ctx(known=False), history, current, sidecar)
            self.assertIsNotNone(state)
            tape = history + [{"role": "user", "content": current}]
            expected = copy.deepcopy(tape)
            results = []
            with ExitStack() as stack:
                chat = self.provider_boundary(stack, results)
                with keat_live.bind_turn(state, tape):
                    agent._model_call("dm system", tape, [])
                self.assertEqual(chat.call_args.kwargs["messages"], expected)
                self.assertEqual(tape, expected)
            artifacts = [json.loads(args[1]) for args, kw in results
                         if kw.get("event_kind") == "model_input"]
            self.assertEqual(artifacts[0]["keat"]["status"], "served")
            self.assertEqual(artifacts[0]["keat"]["binding"]["mode"], "dm")
            self.assertEqual(artifacts[0]["keat"]["binding"]["stream"], "42")
            self.assertEqual(artifacts[0]["keat"]["binding"]["audience"], ["dm:42"])

    def test_respond_and_voice_impl_preserve_non_owner_context_and_offered_tools_to_provider(self):
        """Exercise public and direct voice ingress through the real model boundary."""
        self.policy()
        real_capture = keat_live.capture_turn
        real_offered = agent.offered_tools_for
        cases = (
            ("unknown", "respond", dict(known=False, principal_id="9001")),
            ("known", "respond", dict(known=True, principal_id="9002")),
            ("family", "voice_impl", dict(known=True, family=True, principal_id="9003")),
            # A trusted body grant is weaker than owner authority.
            ("trusted", "voice_impl", dict(known=True, family=False, principal_id="9004")),
        )
        with self.env():
            for label, ingress, facts in cases:
                with self.subTest(tier=label):
                    seen_contexts, offered, results = [], [], []

                    def capture_spy(*args, **kwargs):
                        ctx = kwargs.get("ctx", args[0] if args else None)
                        seen_contexts.append(("capture", ctx))
                        return real_capture(*args, **kwargs)

                    def offered_spy(ctx):
                        seen_contexts.append(("tools", ctx))
                        value = real_offered(ctx)
                        offered.append(copy.deepcopy(value))
                        return value

                    with ExitStack() as stack:
                        chat = self.provider_boundary(stack, results)
                        self.voice_construction_boundary(stack)
                        stack.enter_context(patch.object(agent.llm, "configured", return_value=True))
                        stack.enter_context(patch.object(
                            keat_live, "capture_turn", side_effect=capture_spy))
                        stack.enter_context(patch.object(
                            agent, "offered_tools_for", side_effect=offered_spy))
                        if label == "trusted":
                            stack.enter_context(patch("computer_access.allowed", return_value=True))

                        if ingress == "respond":
                            answer = agent.respond(
                                "tier question", [], "Alice", chat_id="42",
                                is_owner=False, known=facts["known"],
                                principal_id=facts["principal_id"],
                            )
                            original = seen_contexts[0][1]
                        else:
                            original = self.ctx(**facts)
                            token = agent._KEAT_ORIGINAL_INGRESS.set(True)
                            try:
                                answer = agent._voice_impl(
                                    "tier question", [], "Alice", ctx=original, max_iters=1)
                            finally:
                                agent._KEAT_ORIGINAL_INGRESS.reset(token)

                    self.assertEqual(answer, "provider answer")
                    self.assertEqual([kind for kind, _ctx in seen_contexts],
                                     ["capture", "tools"])
                    self.assertIs(seen_contexts[0][1], original)
                    self.assertIs(seen_contexts[1][1], original)
                    self.assertEqual(original.scope,
                                     "unknown" if label == "unknown" else
                                     "family" if label == "family" else "known")
                    self.assertFalse(original.owner)
                    self.assertFalse(original.owner_audience)
                    self.assertEqual(original.principal_id, facts["principal_id"])

                    provider_tools = chat.call_args.kwargs["tools"]
                    self.assertEqual(provider_tools, offered[0])
                    names = {tool["name"] for tool in provider_tools}
                    self.assertNotIn("admit", names)
                    self.assertNotIn("computer_access", names)
                    artifacts = [json.loads(args[1]) for args, kw in results
                                 if kw.get("event_kind") == "model_input"]
                    self.assertEqual(artifacts[0]["keat"]["status"], "served")
                    self.assertEqual(artifacts[0]["keat"]["binding"]["mode"], "dm")
                    self.assertEqual(artifacts[0]["keat"]["binding"]["stream"], "42")
                    self.assertEqual(artifacts[0]["keat"]["binding"]["audience"], ["dm:42"])

    def test_media_and_tool_pairs_are_preserved_or_provider_gets_exact_legacy(self):
        self.policy()
        media_current = [{"type": "text", "text": "look"},
                         {"type": "image", "source": {"type": "base64",
                                                        "media_type": "image/png",
                                                        "data": "AA=="}}]
        with self.env():
            state = keat_live.capture_turn(self.ctx(family=True), media_current, [])
            tape = [{"role": "user", "content": copy.deepcopy(media_current)}]
            additions = [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1",
                                                       "name": "safe_tool", "input": {"x": 1}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1",
                                                  "content": [{"type": "text", "text": "ok"}]}]},
            ]
            with ExitStack() as stack:
                chat = self.provider_boundary(stack)
                with keat_live.bind_turn(state, tape):
                    self.assertTrue(keat_live.capture_appended(tape, additions))
                    tape.extend(copy.deepcopy(additions))
                    expected = copy.deepcopy(tape)
                    agent._model_call("dm system", tape, [{"name": "safe_tool"}])
                self.assertEqual(chat.call_args.kwargs["messages"], expected)
                self.assertEqual(chat.call_args.kwargs["messages"][0]["content"], media_current)
                self.assertEqual(chat.call_args.kwargs["messages"][1:], additions)

            # Failure at selection restores the caller's original rich legacy object,
            # including block order and the assistant/tool-result pairing.
            fallback = copy.deepcopy(expected)
            candidate = [{"role": "user", "content": "economical candidate"}]
            state = keat_live.capture_turn(self.ctx(), "economical candidate", [])
            with ExitStack() as stack:
                chat = self.provider_boundary(stack)
                stack.enter_context(patch.object(
                    keat_live, "select_provider", side_effect=RuntimeError("private")))
                with keat_live.bind_turn(state, fallback,
                                         candidate_messages=copy.deepcopy(candidate)):
                    agent._model_call("dm system", fallback, [{"name": "safe_tool"}])
                self.assertEqual(chat.call_args.kwargs["messages"], expected)
                self.assertEqual(fallback, expected)


if __name__ == "__main__":
    import unittest
    unittest.main()
