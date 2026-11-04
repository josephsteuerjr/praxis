import json, pathlib, tempfile, unittest
from unittest.mock import patch
import agent, group_context

class RoomArchiveTailTests(unittest.TestCase):
    def test_large_archive_tail_equals_original_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/"archive.jsonl"
            for ending in ("\n", ""):
                rows=[{"sender_id": i, "text": "Юникод🙂"*(12000 if i==998 else 3)} for i in range(1000)]
                text="\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+ending
                path.write_text(text,encoding="utf-8")
                with patch.object(group_context,"archive_path",return_value=path), patch.object(agent,"MENTION_WINDOW_MESSAGES",3):
                    self.assertEqual(agent._room_window("room"),rows[-3:])
    def test_invalid_rows_do_not_expand_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/"archive.jsonl"
            path.write_bytes(b'{"sender_id":1}\r\ninvalid\r\n{"sender_id":2}\r\n')
            with patch.object(group_context,"archive_path",return_value=path), patch.object(agent,"MENTION_WINDOW_MESSAGES",2):
                self.assertEqual(agent._room_window("room"),[{"sender_id":2}])
    def test_empty_and_short_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/"archive.jsonl"
            for text,expected in [("",[]),('{"sender_id":4}',[{"sender_id":4}])]:
                path.write_text(text)
                with patch.object(group_context,"archive_path",return_value=path):
                    self.assertEqual(agent._room_window("room"),expected)
