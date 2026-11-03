"""Frozen review search must expose the matching fields in single-line manifests."""
from pathlib import Path
import hashlib
import json
import tempfile
import types
import unittest

from localharness import self_review, source_merge
from localharness.self_review_worker import SnapshotReviewer


class ReviewNavigation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.query = 'command_process.py'
        # Unicode before each match distinguishes character offsets from byte offsets.
        self.document = json.dumps({'padding': 'я' * 12000,
            'files': {'helene/core/' + self.query: 'a' * 64,
                      'live/' + self.query: 'b' * 64},
            'trailing': 'ю' * 12000, 'last': self.query}, ensure_ascii=False)
        source = self.root / 'source'
        source.mkdir()
        (source / 'manifest.json').write_bytes(self.document.encode('utf8'))
        inventory = source_merge.inventory(source)
        service = self_review.Service(self.root / 'home', source, self.root,
                                      types.SimpleNamespace())
        record = service.request(trigger='contested_overlay', operation_id='navigation',
            sources={side: {'root': str(source), 'merge_files': inventory}
                     for side in ('base', 'mine', 'release', 'resolved')},
            overlay={'conflicts': ['manifest.json'], 'merge_identity': {'plan_id': 'a' * 64}})
        self.adapter = SnapshotReviewer({'root': record['snapshot'],
                                         'subject_sha256': record['subject_sha256']})
        self.path = 'resolved/manifest.json'

    def inspect(self, **args):
        return json.loads(self.adapter.dispatch({}, 'inspect', args))

    def test_match_excerpt_and_read_offset_reach_far_into_unicode_manifest(self):
        page = self.inspect(action='search', path=self.path, query=self.query, limit=1)
        row = page['matches'][0]
        self.assertIn(self.query, row['text'])
        self.assertIn('a' * 64, row['text'])
        self.assertLessEqual(len(row['text']), 1000)
        self.assertGreater(row['start_char'], 10000)
        self.assertEqual(self.document[row['start_char']:row['end_char']], row['text'])
        self.assertEqual(self.document[row['match_start']:row['match_end']], self.query)
        read = self.inspect(action='read', path=row['path'], start=row['line'],
                            end=row['line'], start_char=row['start_char'])
        self.assertTrue(read['text'].startswith(row['text']))
        self.assertEqual(read['sha256'], hashlib.sha256(self.document.encode('utf8')).hexdigest())

    def test_all_same_line_occurrences_are_paginated_without_loss_or_duplicates(self):
        rows = []
        offset = 0
        while offset is not None:
            page = self.inspect(action='search', path=self.path, query=self.query,
                                offset=offset, limit=1)
            self.assertEqual(page['total'], self.document.count(self.query))
            self.assertEqual(len(page['matches']), 1)
            rows.extend(page['matches'])
            offset = page['next_offset']
        positions = []
        cursor = 0
        while (position := self.document.find(self.query, cursor)) >= 0:
            positions.append(position)
            cursor = position + len(self.query)
        self.assertEqual([row['match_start'] for row in rows], positions)
        self.assertTrue(all(self.query in row['text'] for row in rows))
        exhausted = self.inspect(action='search', path=self.path, query=self.query,
                                 offset=len(positions), limit=1)
        self.assertEqual(exhausted['matches'], [])
        self.assertIsNone(exhausted['next_offset'])

    def test_path_scope_and_snapshot_integrity_remain_mandatory(self):
        empty = self.inspect(action='search', path='resolved/missing.json', query=self.query)
        self.assertEqual(empty['total'], 0)
        with self.assertRaises(ValueError):
            self.inspect(action='search', path='../outside', query=self.query)
        path = self.adapter.root / self.path
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            self.inspect(action='search', path=self.path, query=self.query)


if __name__ == '__main__':
    unittest.main()
