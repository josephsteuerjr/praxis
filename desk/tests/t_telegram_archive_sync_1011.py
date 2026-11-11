"""Failed flush/replace must preserve the previous Telegram archive generation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
import botapi


class ArchiveSync(unittest.TestCase):
    def test_failed_flush_does_not_publish_or_destroy_previous_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'chat.jsonl';before=b'{"text":"retained"}\n';target.write_bytes(before)
            with patch.object(botapi.os,'fsync',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):botapi._replace_archive(target,[{'text':'new'}])
            self.assertEqual(target.read_bytes(),before)
            self.assertEqual(list(target.parent.glob('.telegram-*')),[])

    def test_failed_publication_preserves_old_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'chat.jsonl';before=b'{"text":"retained"}\n';target.write_bytes(before)
            with patch.object(botapi.os,'replace',side_effect=PermissionError('busy')):
                with self.assertRaises(PermissionError):botapi._replace_archive(target,[{'text':'new'}])
            self.assertEqual(target.read_bytes(),before)
            self.assertEqual(list(target.parent.glob('.telegram-*')),[])

    def test_published_generation_contains_complete_unicode_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'chat.jsonl'
            rows=[{'text':'Привет\nмир','source_message_id':'1'},{'text':'Ответ','outgoing':True}]
            botapi._replace_archive(target,rows)
            self.assertEqual([json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()],rows)
            self.assertTrue(target.read_bytes().endswith(b'\n'))
            self.assertEqual(list(target.parent.glob('.telegram-*')),[])


if __name__=='__main__':unittest.main()
