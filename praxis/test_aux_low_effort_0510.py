"""Retired auxiliary compact calls must not borrow or lower acting voice effort.

The former low-effort generation and retry tests are superseded by acting-author
compaction. A configured high-effort voice is left unchanged and never called by
legacy compact entry points. Actual authored prepare/apply is exercised separately.
"""
from __future__ import annotations

import unittest
from unittest import mock

import llm
import memory_life as ml
from test_llm import Base


class CompactNeverBorrowsEffortTests(Base):
    def setUp(self):
        super().setUp()
        cfg = llm._from_env()
        cfg["roles"]["voice"]["reasoning_effort"] = "high"
        llm.save_config(cfg)

    def test_legacy_compact_refuses_without_channel_or_retry(self):
        before = llm._from_env()
        with mock.patch.object(llm, "chat") as channel:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact([{"id": "evt-1", "line": "source"}],
                                  tier=1, depth=0, continued=False)
        channel.assert_not_called()
        self.assertEqual(llm._from_env(), before)

    def test_unconfigured_channel_is_not_a_fallback_author(self):
        with mock.patch.object(llm, "configured", return_value=False), \
             mock.patch.object(llm, "chat") as channel:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact([], tier=2, depth=2, continued=True)
        channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
