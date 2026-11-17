import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import model_step
import step_control


class RunManifestLookup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.runs = self.base / 'memory/runs'
        self.run_id = 'run-imported'
        self.context = {'origin_chat_id': '123', 'created_at': '2026-01-01T00:00:00Z'}
        self.stamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
        cancel = self.base / 'memory/.control/run-cancel' / (self.run_id + '.json')
        cancel.parent.mkdir(parents=True)
        cancel.write_text(json.dumps({'run_id': self.run_id, 'action': 'cancel'}))

    def write_manifest(self, bucket, value):
        path = self.runs / bucket / self.run_id / 'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def suppressed(self, stamp=None):
        return step_control.suppresses_replay('123', self.stamp if stamp is None else stamp,
                                              base=self.base)

    def status(self):
        return model_step._status(self.base, self.run_id)

    def test_fresh_exact_lookup_without_enumerating_run_directories(self):
        unrelated = self.runs / '1999-12'
        unrelated.mkdir(parents=True)
        for i in range(200):
            (unrelated / f'run-unrelated-{i}').mkdir()
        path = self.write_manifest('imported', {'context': self.context, 'status': 'paused'})
        scans = []
        scandir = os.scandir

        def record(directory):
            scans.append(Path(directory))
            return scandir(directory)

        with patch('os.scandir', side_effect=record):
            self.assertTrue(self.suppressed())
            self.assertEqual(self.status(), 'paused')
        self.assertNotIn(unrelated, scans)
        self.assertNotIn(path.parent, scans)
        self.assertFalse(self.suppressed(self.stamp + 1))
        path.unlink()
        self.assertFalse(self.suppressed())
        self.assertEqual(self.status(), '')
        self.write_manifest('another-bucket', {'context': self.context, 'status': 'done'})
        self.assertTrue(self.suppressed())
        self.assertEqual(self.status(), 'done')

    def test_ambiguous_and_malformed_records_fail_open_for_replay(self):
        good = self.write_manifest('2026-01', {'context': self.context, 'status': 'cancelled'})
        duplicate = self.write_manifest('2026-02', [])
        self.assertFalse(self.suppressed())
        self.assertEqual(self.status(), '')
        good.unlink()
        self.assertFalse(self.suppressed())
        self.assertEqual(self.status(), '')
        duplicate.write_text(json.dumps({'context': {'created_at': 42}}))
        self.assertFalse(self.suppressed())
        self.assertEqual(self.status(), '')
        self.assertEqual(model_step._status(self.base, '*'), '')


if __name__ == '__main__':
    unittest.main()
