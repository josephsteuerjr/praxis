"""Large Markdown reads must never authorize replacing a file with its tail."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import readers


class MarkdownTail(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'memory/notes').mkdir(parents=True)
        env = patch.dict(os.environ, {'HELENE_TREE': str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def test_every_large_file_rejects_tail_write_with_or_without_stamp(self):
        # The original size-minus-cap guard accepted tails for 400001..600000 bytes.
        for size in (400001, 500001, 600000, 600001, 1000000):
            with self.subTest(size=size):
                path = self.root / 'memory/notes/log.md'
                original = b'BEGIN\n' + b'x' * (size - 10) + b'END\n'
                path.write_bytes(original)
                doc = readers.safe_read_md('memory/notes/log.md')
                self.assertTrue(doc['tail'])
                self.assertEqual(len(doc['text'].encode('utf-8')), 200000)
                self.assertTrue(doc['text'].endswith('END\n'))
                for stamp in (doc['mtime_ns'], None):
                    answer = readers.safe_write_md('memory/notes/log.md', doc['text'], stamp)
                    self.assertEqual(answer.get('code'), 'tail_readonly')
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(path.with_suffix('.md.bak').exists())

    def test_large_complete_write_is_also_readonly(self):
        path = self.root / 'memory/notes/log.md'
        path.write_text('x' * 500001, encoding='utf-8')
        self.assertEqual(readers.safe_write_md('memory/notes/log.md', 'y' * 500001)['code'], 'tail_readonly')
        self.assertEqual(path.read_text(), 'x' * 500001)

    def test_utf8_tail_drops_only_partial_start_character(self):
        path = self.root / 'memory/notes/log.md'
        path.write_text('я' * 250001, encoding='utf-8')
        doc = readers.safe_read_md('memory/notes/log.md')
        self.assertNotIn('\ufffd', doc['text'])
        self.assertEqual(doc['text'], 'я' * 100000)
        path.write_text('я' * 250001 + 'z', encoding='utf-8')
        doc = readers.safe_read_md('memory/notes/log.md')
        self.assertNotIn('\ufffd', doc['text'])
        self.assertTrue(doc['text'].endswith('яz'))

    def test_cap_size_and_new_small_file_remain_editable(self):
        path = self.root / 'memory/notes/log.md'
        path.write_text('x' * readers._MD_CAP, encoding='utf-8')
        doc = readers.safe_read_md('memory/notes/log.md')
        self.assertFalse(doc.get('tail'))
        self.assertNotIn('error', readers.safe_write_md('memory/notes/log.md', 'small', doc['mtime_ns']))
        self.assertNotIn('error', readers.safe_write_md('memory/notes/new.md', 'new'))


if __name__ == '__main__':
    unittest.main()
