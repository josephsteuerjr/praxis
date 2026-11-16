"""Text without reply is neither published nor recorded as a chosen silence.

Observed on Windows 10 with a local model: the steps panel showed the model's text, the main
chat showed nothing, and the run receipt said "agent chose silence" although stay_silent was
never called. The note stays internal; the receipt and the window notice now say what happened.
"""
from __future__ import annotations

import unittest

import t_window_word as fixture


class UnspokenReceipt(unittest.TestCase):
    setUp = fixture.WindowTurn.setUp
    tearDown = fixture.WindowTurn.tearDown
    _turn = fixture.WindowTurn._turn

    def test_text_without_reply_is_not_a_declared_silence(self):
        note = "Here is the patch I would apply: Literal Русский 中文"
        outcome, rows, agent = self._turn({"held": "unspoken", "note": note},
                                          fixture._Envelope("run-unspoken"))
        self.assertEqual(outcome, "silent")
        last = rows[-1]
        self.assertTrue(last.get("system"))
        self.assertEqual(last["display_notice"],
                         {"schema": "helene.product-notice.v1", "kind": "closed_unspoken",
                          "values": {}})
        self.assertNotIn(note, "\n".join(str(r.get("text") or "") for r in rows),
                         "the internal note is never published")
        self.assertIn(("completed", True,
                       "turn ended without a reply hand (not a declared silence)"),
                      agent.receipts)
        self.assertNotIn(("completed", True, "agent chose silence"), agent.receipts)

    def test_empty_ending_keeps_the_plain_closed_notice(self):
        outcome, rows, agent = self._turn({"held": "unspoken", "note": ""},
                                          fixture._Envelope("run-empty"))
        self.assertEqual(outcome, "silent")
        self.assertEqual(rows[-1]["display_notice"]["kind"], "closed")
        self.assertIn(("completed", True,
                       "turn ended without a reply hand (not a declared silence)"),
                      agent.receipts)


if __name__ == "__main__":
    unittest.main()
