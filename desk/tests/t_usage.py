"""Usage accounting semantics, provider schema and phone access contract."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import usage
import deskapp


class UsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tree = Path(temp.name)
        self.path = self.tree / "memory" / ".state" / "usage.json"
        self.path.parent.mkdir(parents=True)
        patch = mock.patch.object(usage, "_clock", return_value=(dt.date(2026, 9, 7), "agent"))
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_absent_and_corrupt_are_not_zero_usage(self):
        self.assertIsNone(usage.statistics(self.tree)["today_total"])
        self.path.write_text("[broken", encoding="utf-8")
        self.assertEqual(usage.statistics(self.tree)["status"], "unavailable")

    def test_fresh_and_cached_count_once_not_again_as_model(self):
        counts = {"schema": 2, "in": 50, "out": 10, "cache_read": 20, "cache_creation": 5, "calls": 1}
        self.write({"2026-09-07": {"voice": {**counts, "models": {"model-a": counts}}}})
        result = usage.statistics(self.tree)
        self.assertEqual(result["today_total"]["total_tokens"], 85)
        self.assertEqual(result["week_total"]["calls"], 1)
        self.assertEqual(result["models"][0]["input_tokens"], 75)
        self.assertEqual(len(result["days"]), 7)

    def test_old_semantics_stay_unknown_but_calls_are_visible(self):
        self.write({"2026-09-07": {"voice": {"in": 100, "cache_read": 80, "out": 20, "calls": 2}}})
        row = usage.statistics(self.tree)["today_total"]
        self.assertEqual(row["calls"], 2)
        self.assertIsNone(row["total_tokens"])
        self.assertIsNone(row["cache_percent"])

    def test_seven_days_do_not_include_older_or_future_entries(self):
        self.write({day: {"voice": {"schema": 2, "calls": n}} for day, n in
                    [("2026-08-31", 100), ("2026-09-01", 2), ("2026-09-07", 3), ("2026-09-08", 1000)]})
        self.assertEqual(usage.statistics(self.tree)["week_total"]["calls"], 5)

    def test_glm_credit_windows_preserve_provider_rounding_and_reset(self):
        raw = {"success": True, "data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 10000,
             "currentValue": 879, "remaining": 9120, "percentage": 8, "nextResetTime": 1788809508998}]}}
        row = usage.normalize_glm(raw)[0]
        self.assertEqual(row["window_seconds"], 604800)
        self.assertEqual(row["remaining"], 9120)  # Preserve provider value, not 10000-879.
        self.assertEqual(row["remaining_percent"], 92)
        self.assertEqual(row["resets_at"], 1788809508.998)
        self.assertEqual(row["unit"], "credits")

    def test_unknown_glm_unit_is_not_labeled_week(self):
        row = usage.normalize_glm({"data": {"limits": [{"type": "CREDIT_LIMIT", "unit": 99, "number": 1}]}})[0]
        self.assertIsNone(row["window_seconds"])
        self.assertIsNone(row["used_percent"])
        self.assertNotEqual(row["label"], "Неделя")

    def test_codex_nullable_windows_and_account_buckets(self):
        self.assertEqual(usage.normalize_codex({"rate_limit": {"primary_window": None}}), [])
        rows = usage.normalize_codex({"rateLimitsByLimitId": {"codex": {
            "primary": None, "secondary": {"usedPercent": 25, "windowDurationMins": 10080, "resetsAt": 1788809508}}}})
        self.assertEqual(rows[0]["remaining_percent"], 75)
        self.assertEqual(rows[0]["window_seconds"], 604800)

    def test_paired_phone_can_read_both_endpoints(self):
        self.assertIn("/api/usage", deskapp._DEVICE_PATHS)
        self.assertIn("/api/allowances", deskapp._DEVICE_PATHS)

    def test_codex_relay_week_can_be_primary_and_null_secondary_is_absent(self):
        rows = usage.normalize_codex({"rate_limit": {
            "primary_window": {"used_percent": 68, "limit_window_seconds": 604800},
            "secondary_window": {"used_percent": None, "limit_window_seconds": None, "reset_at": None}}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Неделя")
        self.assertEqual(rows[0]["remaining_percent"], 32)

    def test_codex_legacy_and_additional_limits(self):
        window = {"usedPercent": 0, "windowDurationMins": 300}
        self.assertEqual(usage.normalize_codex({"rateLimits": {"primary": window}})[0]["remaining_percent"], 100)
        rows = usage.normalize_codex({"additional_rate_limits": [{"limit_name": "review",
            "rate_limit": {"primary_window": {"used_percent": 40, "limit_window_seconds": 604800}}}]})
        self.assertEqual(rows[0]["bucket"], "review")
        self.assertEqual(rows[0]["remaining_percent"], 60)


if __name__ == "__main__":
    unittest.main()
