"""Red-first contract tests for the AbstractDL shadow spam detector.

The fixture is intentionally synthetic.  The private canonical archive is used only
for provenance hashes and is never opened by this test suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from moderation_shadow import TARGET_PEER_ID, detect_message

import mtproto_runner


CORPUS_PATH = Path(__file__).with_name("moderation_shadow_corpus.json")


def _corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _detect(case: dict, *, peer_id: int = TARGET_PEER_ID):
    return detect_message(
        peer_id=peer_id,
        text=case["synthetic_text"],
        first_message=case["first_message"],
        repeated_within_hour=case["repeated_within_hour"],
    )


class FrozenCorpusContractTests(unittest.TestCase):
    def test_fixture_is_complete_and_privacy_minimal(self):
        corpus = _corpus()
        self.assertEqual(corpus["schema"], "praxis.moderation.frozen-corpus.v1")
        self.assertEqual(corpus["peer_id"], TARGET_PEER_ID)
        cases = corpus["cases"]
        spam = [case for case in cases if case["label"] == "spam"]
        ham = [case for case in cases if case["label"] == "ham"]
        self.assertEqual(len(spam), 9)  # every known-body deletion in the freeze
        self.assertGreaterEqual(len(ham), 10)
        self.assertEqual(corpus["provenance"]["unknown_body_deletions_excluded"], 4)

        forbidden_keys = {"sender_id", "sender_name", "name", "username", "raw_text"}
        for case in cases:
            self.assertTrue(forbidden_keys.isdisjoint(case), case["case_id"])
            self.assertRegex(case["source_text_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("@", case["synthetic_text"], case["case_id"])

    def test_all_known_deleted_spam_examples_cross_review_threshold(self):
        for case in _corpus()["cases"]:
            if case["label"] != "spam":
                continue
            with self.subTest(case=case["case_id"]):
                result = _detect(case)
                self.assertNotEqual(result.verdict, "pass")
                self.assertGreaterEqual(len(result.matched_features), 2)
                self.assertTrue(
                    set(case["expected_features"]).issubset(result.matched_features)
                )

    def test_representative_neighbors_have_zero_false_positives(self):
        ham = [case for case in _corpus()["cases"] if case["label"] == "ham"]
        flagged = []
        for case in ham:
            result = _detect(case)
            if result.verdict != "pass":
                flagged.append((case["case_id"], result.verdict, result.matched_features))
        self.assertEqual(flagged, [])


class DetectorThresholdTests(unittest.TestCase):
    def detect(self, text: str, *, first: bool = False, repeat: bool = False,
               peer_id: int = TARGET_PEER_ID):
        return detect_message(
            peer_id=peer_id,
            text=text,
            first_message=first,
            repeated_within_hour=repeat,
        )

    def test_single_weak_signal_does_not_cross_threshold(self):
        probes = (
            self.detect("Обычный первый вопрос о проекте", first=True),
            self.detect("В документации указана оплата 5000 ₽"),
            self.detect("Если будет ошибка, напиши мне в личку"),
        )
        for result in probes:
            self.assertEqual(result.verdict, "pass")

    def test_two_independent_signals_cross_threshold_and_are_explained(self):
        result = self.detect("Дополнительный доход 20 тыс.", first=True)
        self.assertNotEqual(result.verdict, "pass")
        self.assertIn("first_message", result.matched_features)
        self.assertIn("money", result.matched_features)

    def test_repeat_is_an_unconditional_explainable_signal(self):
        result = self.detect("Нейтральная повторённая строка", repeat=True)
        self.assertNotEqual(result.verdict, "pass")
        self.assertIn("repeat", result.matched_features)

    def test_homoglyph_normalization_does_not_bypass_money_signal(self):
        # Latin a/o inside a Cyrillic word.
        result = self.detect("Предлагаю зaрaбoток, пиши в лс", first=True)
        self.assertNotEqual(result.verdict, "pass")
        self.assertIn("money", result.matched_features)
        self.assertIn("call_to_action", result.matched_features)

    def test_non_target_peer_is_always_out_of_scope(self):
        result = self.detect(
            "Срочный доход 5000 ₽, ставь + и пиши в личку",
            first=True,
            repeat=True,
            peer_id=-1000000000000,
        )
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.matched_features, ())


class ObservationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import moderation_shadow
        self.module = moderation_shadow
        self.state = Path(self.tmp.name) / "moderation.json"
        self.patch = mock.patch.object(moderation_shadow, "STATE_PATH", self.state)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_review_event_is_privacy_minimal_and_duplicate_safe_after_reload(self):
        with mock.patch("core.events.emit") as emit:
            result = self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=77, sender_id=88,
                text="Срочный доход 5000 ₽, пиши в личку", observed_at=1000,
            )
            self.assertEqual(result.verdict, "review")
            self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=77, sender_id=88,
                text="Срочный доход 5000 ₽, пиши в личку", observed_at=1001,
            )
        emit.assert_called_once()
        args, kwargs = emit.call_args
        self.assertEqual(args[0], "moderation_review")
        payload = args[2]
        self.assertEqual(set(payload), {
            "peer_id", "message_id", "sender_id", "verdict", "matched_features"})
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("доход", serialized.lower())
        self.assertEqual(kwargs["dedup_key"], f"moderation:{TARGET_PEER_ID}:77")

    def test_non_target_and_empty_messages_do_not_write_state_or_events(self):
        with mock.patch("core.events.emit") as emit:
            self.assertIsNone(self.module.observe_message(
                peer_id=-1001, message_id=1, sender_id=2, text="спам"))
            self.assertIsNone(self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=2, sender_id=2, text=""))
        emit.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_same_sender_repeat_within_hour_is_explainable(self):
        with mock.patch("core.events.emit") as emit:
            first = self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=1, sender_id=9,
                text="Нейтральная строка", observed_at=1000)
            second = self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=2, sender_id=9,
                text="Нейтральная строка", observed_at=1200)
        self.assertEqual(first.verdict, "pass")
        self.assertEqual(second.verdict, "review")
        self.assertIn("repeat", second.matched_features)
        emit.assert_called_once()

    def test_emit_failure_does_not_consume_message_and_retry_can_write(self):
        with mock.patch("core.events.emit", side_effect=[None, {"id": "ok"}]) as emit:
            first = self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=78, sender_id=89,
                text="Срочный доход 5000 ₽, пиши в личку", observed_at=1000)
            second = self.module.observe_message(
                peer_id=TARGET_PEER_ID, message_id=78, sender_id=89,
                text="Срочный доход 5000 ₽, пиши в личку", observed_at=1001)
        self.assertEqual(first.verdict, "review")
        self.assertEqual(second.verdict, "review")
        self.assertEqual(emit.call_count, 2)

    def test_malformed_event_does_not_spend_valid_neighbors_attempt_budget(self):
        bad = {"id": "bad", "dedup_key": "bad", "kind": "moderation_review",
               "payload": "not-a-dict"}
        good = {"id": "good", "dedup_key": "good", "kind": "moderation_review",
                "payload": {"peer_id": TARGET_PEER_ID, "message_id": 7,
                            "sender_id": 8, "verdict": "review",
                            "matched_features": ["money", "call_to_action"]}}
        marked = []
        with mock.patch.object(mtproto_runner.agent.llm, "configured", return_value=True), \
             mock.patch("core.events.undelivered", return_value=[bad, good]), \
             mock.patch("core.events.bump_attempts", return_value={"good": 1}) as bump, \
             mock.patch.object(mtproto_runner.agent, "wake_turn", return_value="") as wake, \
             mock.patch("core.events.mark_delivered",
                        side_effect=lambda keys: marked.extend(keys)), \
             mock.patch("core.events.compact"):
            import asyncio
            asyncio.run(mtproto_runner._run_moderation_event_pass())
        bump.assert_called_once_with(["good"])
        wake.assert_called_once()
        self.assertEqual(marked, ["bad", "good"])

    def test_moderation_event_delivery_wakes_with_ids_and_marks_after_turn(self):
        event = {
            "id": "evt-1", "dedup_key": f"moderation:{TARGET_PEER_ID}:77",
            "kind": "moderation_review",
            "payload": {"peer_id": TARGET_PEER_ID, "message_id": 77,
                        "sender_id": 88, "verdict": "review",
                        "matched_features": ["money", "call_to_action"]},
        }
        order = []
        with mock.patch.object(mtproto_runner.agent.llm, "configured", return_value=True), \
             mock.patch("core.events.undelivered", return_value=[event]), \
             mock.patch("core.events.bump_attempts", return_value={event["dedup_key"]: 1}), \
             mock.patch.object(mtproto_runner.agent, "wake_turn",
                               side_effect=lambda goal: order.append(("wake", goal)) or ""), \
             mock.patch("core.events.mark_delivered",
                        side_effect=lambda keys: order.append(("mark", keys))), \
             mock.patch("core.events.compact"):
            import asyncio
            asyncio.run(mtproto_runner._run_moderation_event_pass())
        self.assertEqual([item[0] for item in order], ["wake", "mark"])
        goal = order[0][1]
        self.assertIn('"message_id":77', goal)
        self.assertIn('"sender_id":88', goal)
        self.assertNotIn("доход", goal.lower())
        self.assertEqual(order[1][1], [event["dedup_key"]])

    def test_failed_wake_does_not_mark_event_delivered(self):
        event = {"id": "evt-2", "dedup_key": "moderation:x:1",
                 "kind": "moderation_review", "payload": {}}
        with mock.patch.object(mtproto_runner.agent.llm, "configured", return_value=True), \
             mock.patch("core.events.undelivered", return_value=[event]), \
             mock.patch("core.events.bump_attempts", return_value={event["dedup_key"]: 1}), \
             mock.patch.object(mtproto_runner.agent, "wake_turn", side_effect=RuntimeError("boom")), \
             mock.patch("core.events.mark_delivered") as mark, \
             mock.patch("core.events.compact"):
            import asyncio
            asyncio.run(mtproto_runner._run_moderation_event_pass())
        mark.assert_called_once_with([])

    def test_moderation_pending_marks_next_free_voice_turn_as_priority(self):
        event = {"id": "evt-priority", "dedup_key": "moderation:x:9",
                 "kind": "moderation_review", "payload": {}}
        old_priority = mtproto_runner._MODERATION_PRIORITY_PENDING
        self.addCleanup(setattr, mtproto_runner, "_MODERATION_PRIORITY_PENDING", old_priority)
        with mock.patch("core.events.undelivered", return_value=[event]), \
             mock.patch.object(mtproto_runner, "_passing", {"busy"}), \
             mock.patch.object(mtproto_runner, "_debounce", {}):
            import asyncio
            asyncio.run(mtproto_runner._moderation_events_once())
        self.assertTrue(mtproto_runner._MODERATION_PRIORITY_PENDING)

    def test_moderation_pass_consumes_priority_only_after_it_owns_lock(self):
        old_priority = mtproto_runner._MODERATION_PRIORITY_PENDING
        self.addCleanup(setattr, mtproto_runner, "_MODERATION_PRIORITY_PENDING", old_priority)
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        with mock.patch.object(mtproto_runner.agent.llm, "configured", return_value=True), \
             mock.patch("core.events.undelivered", return_value=[]):
            import asyncio
            asyncio.run(mtproto_runner._run_moderation_event_pass())
        self.assertFalse(mtproto_runner._MODERATION_PRIORITY_PENDING)

    def test_module_contains_no_moderation_actuator(self):
        source = Path(self.module.__file__).read_text(encoding="utf-8")
        for forbidden in ("DeleteMessages", "EditBanned", "send_message(", "client("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
