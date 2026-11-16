"""A stopped turn carries the durable status it observed, not only "deferred".

Tim (Helene 2.7.2, local Gemma): a max_tokens cut showed «ждёт подтверждения», a
confirmation that nobody could give. The envelope now says why the run stopped.
"""
import unittest

import agent
import media


class StoppedTurnEnvelope(unittest.TestCase):
    def test_max_tokens_cut_keeps_its_status_and_reason(self):
        for status in ("failed", "in_doubt"):
            with self.subTest(status=status):
                exc = agent.RunStopped("run-x", status, agent.MAX_TOKENS_STOP_REASON)
                env = agent._stopped_turn_envelope(exc, retry_media=False, run_id="run-x")
                self.assertTrue(env.deferred)
                self.assertFalse(env.failed)
                self.assertEqual(env.run_id, "run-x")
                self.assertEqual(env.stop_status, status)
                self.assertIn("max_tokens", env.stop_reason)

    def test_checkpoint_and_cancel_are_distinguishable(self):
        for status in ("paused", "blocked", "cancelled", "in_doubt"):
            env = agent._stopped_turn_envelope(
                agent.RunStopped("run-y", status, "owner"), retry_media=True, run_id="run-y")
            self.assertEqual((env.deferred, env.stop_status, env.retry_media), (True, status, True))

    def test_other_exceptions_stay_failures_without_stop_fields(self):
        env = agent._stopped_turn_envelope(RuntimeError("boom"), retry_media=False, run_id="run-z")
        self.assertEqual((env.deferred, env.failed, env.stop_status, env.stop_reason),
                         (False, True, "", ""))

    def test_envelope_normalizes_and_bounds_stop_fields(self):
        env = media.TurnEnvelope(stop_status=None, stop_reason="x" * 1000)
        self.assertEqual(env.stop_status, "")
        self.assertEqual(len(env.stop_reason), 300)
        self.assertEqual(media.TurnEnvelope().stop_status, "")


if __name__ == "__main__":
    unittest.main()
