"""The window attaches the live preview only to the still-running call it belongs to."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import readers


def write(path, **values):
    data = {"schema": "helene.model-preview.v1", "call_id": "model-a", "at": "2026-11-16T12:00:00+00:00",
            "thinking": "думаю", "reply": "Привет", "text": "", "thinking_chars": 5, "reply_chars": 6}
    data.update(values)
    (path / readers.MODEL_PREVIEW_FILE).write_text(json.dumps(data, ensure_ascii=False), "utf-8")


class AttachPreview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def iterations(self, **values):
        row = {"call_id": "model-a", "status": "running", "text": "", "tools": []}
        row.update(values)
        return [{"call_id": "model-old", "status": "completed", "text": "раньше"}, row]

    def test_running_call_gets_its_preview(self):
        write(self.path)
        rows = self.iterations()
        readers._attach_model_preview(self.path, rows)
        self.assertEqual(rows[1]["preview"], {"thinking": "думаю", "reply": "Привет", "thinking_chars": 5,
                                              "reply_chars": 6, "at": "2026-11-16T12:00:00+00:00"})
        self.assertNotIn("preview", rows[0])

    def test_finished_other_or_foreign_previews_are_ignored(self):
        for values, preview in (({"status": "completed"}, {}), ({"text": "готово"}, {}),
                                ({}, {"call_id": "model-z"}), ({}, {"schema": "other"}),
                                ({}, {"thinking": "", "reply": "", "text": ""})):
            with self.subTest(values=values, preview=preview):
                write(self.path, **preview)
                rows = self.iterations(**values)
                readers._attach_model_preview(self.path, rows)
                self.assertNotIn("preview", rows[1])

    def test_no_file_and_bounded_values(self):
        rows = self.iterations()
        readers._attach_model_preview(self.path, rows)
        self.assertNotIn("preview", rows[1])
        write(self.path, reply="x" * 9000, reply_chars=-1)
        readers._attach_model_preview(self.path, rows)
        self.assertEqual(len(rows[1]["preview"]["reply"]), 4000)
        self.assertNotIn("reply_chars", rows[1]["preview"])


if __name__ == "__main__":
    unittest.main()
