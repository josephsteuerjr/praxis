"""Real inbox publication -> archive -> reader identity; temporary data only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))
import deskapp
from deskd import readers, control
from transport import Desk


class MessageIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_identity_matches_published_filename_and_survives_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(readers, "tree", return_value=root), \
                 patch.object(readers, "reader_status", return_value={"alive": True, "busy": True, "run": "run"}), \
                 patch.object(deskapp, "_ledger", return_value=None):
                for room in ("", "window", "window-aabbccdd"):
                    answer = await deskapp._say("Повтор", chat=room)
                    sid = answer["source_id"]
                    note = root / "memory/.control/desk_inbox" / (sid.removeprefix("note:") + ".md")
                    self.assertTrue(note.is_file())
                    self.assertIn("Повтор", note.read_text(encoding="utf-8"))
                    stream = room or "window"
                    desk = Desk(root, stream, "Егор", "Чат")
                    # A transcribed or enriched text may differ from what the UI sent.
                    desk.archive("Повтор\n[вложения] готовый файл", outgoing=False, source_id=sid)
                    row = readers.chat_tail(stream)[-1]
                    self.assertEqual(row["source_id"], sid)
                first, second = await deskapp._say("Повтор"), await deskapp._say("Повтор")
                self.assertNotEqual(first["source_id"], second["source_id"])

    async def test_legacy_archive_does_not_invent_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Desk(root, "window", "Егор", "Чат").archive("Старое", outgoing=False)
            row = json.loads((root / "memory/groups/window.jsonl").read_text(encoding="utf-8"))
            self.assertNotIn("source_id", row)

    async def test_read_now_cannot_interrupt_successor_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "memory/.control"
            folder.mkdir(parents=True)
            (folder / "current-step.json").write_text(json.dumps({"token":"token-new", "run_id":"run-new"}))
            self.assertTrue(control.interrupt_step(root, "run-old")["ok"])
            self.assertFalse((folder / "step-interrupt.json").exists())
            self.assertTrue(control.interrupt_step(root, "run-new")["ok"])
            self.assertEqual(json.loads((folder / "step-interrupt.json").read_text())["token"], "token-new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
