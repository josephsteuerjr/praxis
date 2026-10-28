import json
import tempfile
import unittest
from pathlib import Path

import telegram_duplicate_audit as audit


class TelegramDuplicateAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "groups"
        self.archive = self.root / "-1001-example" / "archive.jsonl"
        self.archive.parent.mkdir(parents=True)

    def append(self, **overrides):
        row = {
            "schema": audit.MESSAGE_SCHEMA,
            "kind": "message",
            "peer_id": "-1001",
            "topic_id": None,
            "message_id": 1,
            "sender_id": 42,
            "sender_name": "Praxis",
            "reply_to_message_id": None,
            "timestamp": "2026-08-10T12:00:00Z",
            "text": "hello",
            "outgoing": True,
        }
        row.update(overrides)
        with self.archive.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_reports_same_text_on_distinct_routes(self):
        self.append(message_id=10, reply_to_message_id=100, text="same")
        self.append(message_id=11, reply_to_message_id=101, text="same")
        report = audit.build_report(
            self.root, since=audit.parse_timestamp("2026-08-09T00:00:00Z"),
        )
        clusters = report["exact_cross_route_duplicate_clusters"]
        self.assertEqual(len(clusters), 1)
        self.assertEqual([row["message_id"] for row in clusters[0]["messages"]], [10, 11])
        self.assertEqual(clusters[0]["messages"][0]["reply_to_message_id"], 100)
        self.assertEqual(clusters[0]["messages"][1]["reply_to_message_id"], 101)

    def test_same_route_is_not_reported(self):
        self.append(message_id=10, reply_to_message_id=100, text="same")
        self.append(message_id=11, reply_to_message_id=100, text="same")
        report = audit.build_report(
            self.root, since=audit.parse_timestamp("2026-08-09T00:00:00Z"),
        )
        self.assertEqual(report["exact_cross_route_duplicate_clusters"], [])

    def test_ignores_incoming_old_and_malformed_rows(self):
        self.append(message_id=10, outgoing=False, text="same")
        self.append(message_id=11, timestamp="2026-08-01T12:00:00Z", text="same")
        with self.archive.open("a", encoding="utf-8") as handle:
            handle.write("not-json\n")
        report = audit.build_report(
            self.root, since=audit.parse_timestamp("2026-08-09T00:00:00Z"),
        )
        self.assertEqual(report["outgoing_messages"], 0)
        self.assertEqual(report["malformed_records"], 1)
        self.assertEqual(report["exact_cross_route_duplicate_clusters"], [])

    def test_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError):
            audit.parse_timestamp("2026-08-10T12:00:00")


if __name__ == "__main__":
    unittest.main()
