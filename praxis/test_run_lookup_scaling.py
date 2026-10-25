"""Cold/retention lookups must not walk unrelated run directories."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from run_manager import RunManager, RunConflict, RunNotFound


class RunLookupScaling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.manager = RunManager(self.base)

    def manifest(self, bucket, run_id):
        path = self.manager.root / bucket / run_id / 'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}', encoding='utf-8')
        return path

    def test_lookup_does_not_enumerate_sibling_runs(self):
        expected = self.manifest('legacy-bucket', 'custom-id')
        for index in range(40):
            self.manifest('another-bucket', f'unrelated-{index}')
        import os
        original_scandir = os.scandir
        original_listdir = os.listdir
        scanned = []

        def scandir(path):
            scanned.append(Path(path))
            return original_scandir(path)

        def listdir(path):
            scanned.append(Path(path))
            return original_listdir(path)

        with mock.patch('os.scandir', side_effect=scandir), \
             mock.patch('os.listdir', side_effect=listdir):
            self.assertEqual(self.manager.path('custom-id'), expected.parent)
            with self.assertRaises(RunNotFound):
                self.manager.path('retained-away')
        self.assertTrue(scanned)
        self.assertEqual(set(scanned), {self.manager.root})

    def test_duplicate_custom_id_across_buckets_still_conflicts(self):
        self.manifest('old', 'custom-id')
        self.manifest('new', 'custom-id')
        with self.assertRaises(RunConflict):
            self.manager.path('custom-id')

    def test_removed_cached_run_is_not_returned_and_missing_is_not_cached(self):
        path = self.manifest('old', 'custom-id')
        self.assertEqual(self.manager.path('custom-id'), path.parent)
        path.unlink()
        with self.assertRaises(RunNotFound):
            self.manager.path('custom-id')
        moved = self.manifest('new', 'custom-id')
        self.assertEqual(self.manager.path('custom-id'), moved.parent)

    def test_missing_root_and_invalid_ids(self):
        with self.assertRaises(RunNotFound):
            self.manager.path('absent')
        for run_id in ('..', '.', '../other', 'a/b', 'a*b', ''):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                self.manager.path(run_id)
