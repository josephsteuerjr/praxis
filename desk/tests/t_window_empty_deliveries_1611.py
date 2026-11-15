"""A file-only window delivery is archived without an empty conversational event."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness.transport import Desk


class EmptyWindowDeliveries(unittest.TestCase):
    def test_file_only_delivery_keeps_archive_and_receipt_without_blank_life_event(self):
        with tempfile.TemporaryDirectory() as folder:
            life = Mock()
            room = Desk(Path(folder), 'window', 'Owner', 'Window', memory_life=life)
            receipt = room.deliver('', media_path='artifacts/a.patch', media_kind='document')
            row = json.loads((Path(folder) / 'memory/groups/window.jsonl').read_text(encoding='utf-8').splitlines()[-1])
            self.assertEqual(row['media_path'], 'artifacts/a.patch')
            self.assertEqual(row['media_kind'], 'document')
            self.assertTrue(receipt)
            self.assertEqual(room.sent, [''])
            life.record_message.assert_not_called()

    def test_nonempty_whitespace_incoming_and_media_marker_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            life = Mock()
            room = Desk(Path(folder), 'window', 'Owner', 'Window', memory_life=life)
            for value, direction in [(' exact\n', 'out'), (' ', 'out'), ('', 'in'), ('[attachment: a.patch]', 'out')]:
                with self.subTest(value=value, direction=direction):
                    life.reset_mock()
                    room.life(value, direction=direction, actor='Agent', source_id='fixture')
                    life.record_message.assert_called_once()
                    self.assertEqual(life.record_message.call_args.args[:2], ('window', value))


if __name__ == '__main__':
    unittest.main()
