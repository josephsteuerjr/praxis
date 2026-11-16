"""Only a checkpoint says «ждёт подтверждения»; a max_tokens cut, an unknown effect and a
cancel get their own notices (Tim, Helene 2.7.2 on Windows 10 with a local model).
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import product_notices, runner

CUT = "model response cut by max_tokens; no automatic replay"  # agent.MAX_TOKENS_STOP_REASON


def envelope(status="", reason=""):
    return SimpleNamespace(deferred=True, stop_status=status, stop_reason=reason)


class StoppedNotice(unittest.TestCase):
    def test_each_stop_gets_its_own_notice(self):
        cases = {
            ("failed", CUT): "turn_cut", ("in_doubt", CUT): "turn_cut",
            ("paused", "durability failure"): "paused", ("blocked", ""): "paused",
            ("in_doubt", "durability failure during tool"): "run_in_doubt",
            ("failed", "durability"): "turn_failed",
            ("cancelled", "owner"): "run_stopped", ("unknown", ""): "run_stopped",
        }
        for (status, reason), kind in cases.items():
            with self.subTest(status=status, reason=reason):
                self.assertEqual(runner.stopped_notice(envelope(status, reason)), kind)

    def test_old_core_envelope_keeps_the_checkpoint_words(self):
        self.assertEqual(runner.stopped_notice(SimpleNamespace(deferred=True)), "paused")

    def test_only_the_checkpoint_mentions_confirmation(self):
        catalogs = {code: json.loads((DESK / "lang" / f"{code}.json").read_text("utf-8"))
                    for code in ("ru", "en", "fr", "zh")}
        for kind in ("turn_cut", "run_in_doubt", "run_stopped"):
            self.assertEqual(product_notices.FIELDS[kind], ("run",))
            recipe = product_notices.recipe(kind, run="run-1")
            self.assertIn("run-1", product_notices.legacy(recipe))
            for code, catalog in catalogs.items():
                text = catalog["notice." + kind]
                self.assertIn("{run}", text, (kind, code))
                self.assertNotEqual(text, catalog["notice.paused"])
        self.assertIn("max_tokens", catalogs["ru"]["notice.turn_cut"])
        self.assertIn("ждёт подтверждения", catalogs["ru"]["notice.paused"])
        for kind in ("turn_cut", "run_in_doubt", "run_stopped"):
            self.assertNotIn("ждёт подтверждения", catalogs["ru"]["notice." + kind])

    def test_window_client_knows_the_new_kinds(self):
        text = (DESK / "ui-kit" / "text.ts").read_text("utf-8")
        for kind in ("turn_cut", "run_in_doubt", "run_stopped"):
            self.assertIn(f'{kind}:["run"]', text)


if __name__ == "__main__":
    unittest.main()
