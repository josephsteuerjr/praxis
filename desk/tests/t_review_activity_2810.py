"""The header reflects both review stores, completion, cancellation and stale custody."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import self_review_integration as integration


class Activity(unittest.TestCase):
    def counts(self,root):
        value=integration.activity(root)
        return {key:value[key] for key in ('running','queued')}
    def test_current_workers_only_and_terminal_cache_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for store in ('.self-review', '.source-update-reviews'):
                path = root / 'memory' / store / 'review-test' / 'request.json'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'execution': 'running', 'critics': [
                    {'execution': 'running'}, {'execution': 'queued'}, {'execution': 'done'}]}))
                paths.append(path)
            self.assertEqual(self.counts(root), {'running': 2, 'queued': 2})
            paths[0].write_text(json.dumps({'execution': 'done', 'critics': [{'execution': 'running'}]}))
            self.assertEqual(self.counts(root), {'running': 1, 'queued': 1})
            paths[1].write_text(json.dumps({'execution': 'running', 'cancel_requested': True,
                                         'critics': [{'execution': 'running'}]}))
            self.assertEqual(self.counts(root), {'running': 0, 'queued': 0})
            paths[1].write_text(json.dumps({'execution': 'running', 'critics': [{'execution': 'running'}]}))
            old = time.time() - 60
            os.utime(paths[1], (old, old))
            self.assertEqual(self.counts(root), {'running': 0, 'queued': 0})


if __name__ == '__main__': unittest.main()
