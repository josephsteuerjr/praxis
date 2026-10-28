"""Edition legacy statistics keep their list API and tolerate damaged JSONL."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import frame_stats


class LegacyStatistics(unittest.TestCase):
    def test_malformed_event_and_nested_objects_do_not_erase_valid_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'memory/runs/day/run-20261019T000000-test'
            run.mkdir(parents=True)
            (run / 'manifest.json').write_text('{"context": [1]}', encoding='utf-8')
            events = [None, [], 42, {'kind': 'model_input', 'result': [1]},
                      {'kind': 'model_completed', 'usage': [], 'at': '2026-10-19'},
                      {'kind': 'model_input', 'result': {'metadata': [1]}},
                      {'kind': 'model_completed', 'usage': {'in': 7, 'out': 3},
                       'at': '2026-10-19', 'text_chars': 4}]
            (run / 'events.jsonl').write_text('null\n{broken\n' + ''.join(
                json.dumps(row) + '\n' for row in events), encoding='utf-8')
            rows = frame_stats.collect(Path(tmp))
            self.assertIsInstance(rows, list)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]['in'], 7)
            self.assertEqual(frame_stats.group(rows, 'role')[0]['calls'], 2)

    def test_bad_cutoffs_are_usage_errors_not_tracebacks(self):
        for args in (['--days', '4000000'], ['--days', '-1'], ['--since', 'bad']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as caught:
                    frame_stats.main(['--tree', '.', *args])
                self.assertEqual(caught.exception.code, 2)
                self.assertNotIn('Traceback', err.getvalue())


if __name__ == '__main__':
    unittest.main()
