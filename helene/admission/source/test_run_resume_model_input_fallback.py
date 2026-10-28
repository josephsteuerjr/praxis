from __future__ import annotations

import json

from test_run_resume import RunResumeBase
from run_resume import plan_resume


class TestModelInputFallbackResume(RunResumeBase):
    def _input(self, context, value: dict, *, name: str = "model-input",
               kind: str = "model_input") -> None:
        self.manager.store_result(
            context.run_id, json.dumps(value, ensure_ascii=False, indent=2),
            call_id="model-one", name=name,
            media_type="application/json; charset=utf-8", event_kind=kind,
        )

    def _intent(self, context) -> dict:
        value = {
            "system": "economic system",
            "messages": [{"role": "user", "content": "economic candidate"}],
            "tools": [{"name": "fs_read"}],
            "keat_economy_legacy_messages": [
                {"role": "user", "content": "canonical legacy at selection"},
            ],
        }
        self._input(context, value)
        self.manager.append_event(
            context.run_id, "model_started", call_id="model-one", role="voice",
            message_count=1, tool_count=1,
        )
        return value

    def _fallback(self, context, value: dict) -> None:
        self._input(
            context, value, name="model-input-fallback",
            kind="model_input_fallback",
        )

    def _output(self, context) -> None:
        value = {
            "text": "answer", "blocks": [{"type": "text", "text": "answer"}],
            "stop_reason": "end_turn", "framework": "test", "model": "test-model",
            "usage": {"input_tokens": 3},
        }
        self.manager.store_result(
            context.run_id, json.dumps(value), call_id="model-one",
            name="model-output", media_type="application/json; charset=utf-8",
            event_kind="model_output",
        )
        self.manager.append_event(
            context.run_id, "model_completed", call_id="model-one", role="voice",
            stop_reason="end_turn",
        )

    def test_resume_prefers_exact_fallback_including_post_intent_additions(self):
        context = self.create("input-fallback")
        intent = self._intent(context)
        actual = {
            "system": "legacy system after revocation",
            "messages": [
                {"role": "user", "content": "canonical legacy at selection"},
                {"role": "assistant", "content": "tool call"},
                {"role": "user", "content": "tool result added after intent"},
            ],
            "tools": [{"name": "fs_read"}, {"name": "new_tool"}],
            "reason": "keat_final_revocation",
        }
        self._fallback(context, actual)
        self._output(context)
        self.recovery_pause(context)

        plan = plan_resume(self.manager, context.run_id)

        self.assertEqual(plan.kind, "authored_output")
        self.assertEqual(plan.model_input, actual)
        self.assertNotEqual(plan.model_input["messages"],
                            intent["keat_economy_legacy_messages"])

    def test_fallback_before_model_started_is_rejected(self):
        context = self.create("fallback-before-start")
        self._input(context, {
            "system": "selected", "messages": [], "tools": [],
            "keat_economy_legacy_messages": [],
        })
        self._fallback(context, {
            "system": "legacy", "messages": [], "tools": [],
            "reason": "keat_final_revocation",
        })
        self.manager.append_event(
            context.run_id, "model_started", call_id="model-one", role="voice",
            message_count=0, tool_count=0,
        )
        self._output(context)
        self.recovery_pause(context)

        plan = plan_resume(self.manager, context.run_id)

        self.assertEqual(plan.kind, "blocked")
        self.assertIn("model_input_fallback precedes", plan.reason)

    def test_duplicate_fallback_is_rejected(self):
        context = self.create("fallback-duplicate")
        self._intent(context)
        value = {
            "system": "legacy", "messages": [], "tools": [],
            "reason": "keat_final_revocation",
        }
        self._fallback(context, value)
        self._input(context, value, name="second-model-input-fallback",
                    kind="model_input_fallback")
        self._output(context)
        self.recovery_pause(context)

        plan = plan_resume(self.manager, context.run_id)

        self.assertEqual(plan.kind, "blocked")
        self.assertIn("duplicate model_input_fallback", plan.reason)

    def test_fallback_after_output_is_rejected(self):
        context = self.create("fallback-after-output")
        self._intent(context)
        self._output(context)
        self._fallback(context, {
            "system": "legacy", "messages": [], "tools": [],
            "reason": "keat_final_revocation",
        })
        self.recovery_pause(context)

        plan = plan_resume(self.manager, context.run_id)

        self.assertEqual(plan.kind, "blocked")
        self.assertIn("model_input_fallback follows model_output", plan.reason)

    def test_wrong_fallback_reason_is_rejected(self):
        context = self.create("fallback-reason")
        self._intent(context)
        self._fallback(context, {
            "system": "legacy", "messages": [], "tools": [],
            "reason": "some_other_reason",
        })
        self._output(context)
        self.recovery_pause(context)

        plan = plan_resume(self.manager, context.run_id)

        self.assertEqual(plan.kind, "blocked")
        self.assertIn("invalid reason", plan.reason)
