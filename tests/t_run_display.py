"""Read-only GUI trigger/result contract; no agent or subprocess execution."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import readers


class RunDisplay(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"HELENE_TREE": str(self.root)}); env.start(); self.addCleanup(env.stop)
        self.rid = "run-20260907T193520428911Z-abcdef12"
        self.path = self.root / "memory/runs/2026-09" / self.rid
        self.path.mkdir(parents=True)
        self.context = {"kind": "chat_turn", "origin_chat_id": "123", "origin_message_ids": [9], "goal": "Old first message"}
        self.manifest = {"context": self.context, "status": "running", "created_at": "2026-09-07T19:35:20Z"}
        self.authority = {"schema": "praxis.run.authority.v2", "origin_chat_id": "123", "origin_message_ids": [9], "origin_text": "Actual current request"}

    def write(self, prefix="", authority=None):
        (self.path / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        text = "# Immutable run context\n" + prefix + "\n## Authority and address\n\n```json\n" + json.dumps(authority or self.authority) + "\n```\n\n## Goal\nOld first message"
        (self.path / "context.md").write_text(text, encoding="utf-8")

    def _event(self, **row):
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_full_result_is_served_by_id_and_stays_inside_the_run(self):
        # 08.09: inline-превью результата — 2000 знаков; длинный вывод руки и длинное слово
        # в окне «не разворачивались». Файл результата отдаётся целиком по его id, только
        # изнутри каталога прогона.
        self.write()
        (self.path / "results").mkdir()
        body = "строка " * 1000
        (self.path / "results" / "0003-shell.log").write_text(body, encoding="utf-8")
        ref = {"schema": "praxis.result-ref.v1", "result_id": "result-0003", "path": "results/0003-shell.log",
               "size": len(body.encode("utf-8")), "media_type": "text/plain; charset=utf-8",
               "inline": {"head": body[:2000], "tail": "", "truncated": True}}
        self._event(kind="model_started", call_id="m1", seq=1)
        self._event(kind="tool_started", call_id="c1", tool="shell", args={"command": "dir"}, seq=2)
        self._event(kind="tool_result", call_id="c1", name="shell", result=ref, seq=3)
        detail = readers.run_detail(self.rid)
        card = detail["iterations"][0]["tools"][0]
        self.assertEqual(card["result"]["result_id"], "result-0003")
        self.assertTrue(card["result"]["truncated"])
        full = readers.run_result(self.rid, "result-0003")
        self.assertEqual(full["text"], body)
        self.assertTrue(full["complete"])
        self.assertEqual(readers.run_result(self.rid, "result-0009"), {}, "неизвестный id — пусто")
        self.assertEqual(readers.run_result(self.rid, "../manifest.json"), {}, "чужая форма id — пусто")
        outside = dict(ref, result_id="result-0004", path="../../../manifest.json")
        self._event(kind="tool_result", call_id="c2", name="shell", result=outside, seq=4)
        self.assertEqual(readers.run_result(self.rid, "result-0004"), {}, "путь вне прогона не читается")

    def test_long_model_word_is_read_from_its_file_when_inline_is_truncated(self):
        self.write()
        (self.path / "results").mkdir()
        word = "Длинное слово. " * 400
        payload = json.dumps({"text": word, "blocks": [{"type": "text", "text": word}], "stop_reason": "end_turn"}, ensure_ascii=False)
        (self.path / "results" / "0002-model-output.log").write_text(payload, encoding="utf-8")
        ref = {"schema": "praxis.result-ref.v1", "result_id": "result-0002", "path": "results/0002-model-output.log",
               "size": len(payload.encode("utf-8")), "media_type": "application/json; charset=utf-8",
               "inline": {"head": payload[:2000], "tail": payload[-200:], "truncated": True}}
        self._event(kind="model_started", call_id="m1", seq=1)
        self._event(kind="model_output", call_id="m1", name="model-output", result=ref, seq=2)
        self._event(kind="model_completed", call_id="m1", seq=3, duration_ms=1200, stop_reason="end_turn", usage={"in": 10, "out": 900})
        it = readers.run_detail(self.rid)["iterations"][0]
        self.assertEqual(it["text"], word, "слово прочитано из файла, а не потеряно")
        self.assertEqual(it["text_ref"], "result-0002")
        self.assertFalse(it["text_truncated"])
        self.assertEqual(readers.run_result(self.rid, "result-0002")["model_text"], word)

    def test_v1_and_v2_first_authority_supply_current_trigger(self):
        for prefix in ("", "<!-- praxis.run.context.v2 seal=example -->\n"):
            with self.subTest(prefix=prefix):
                self.write(prefix)
                self.assertEqual(readers.run_detail(self.rid)["origin"], {"text": "Actual current request", "source": "snapshot"})

    def test_json_strings_cannot_end_authority_or_add_a_second_one(self):
        self.authority["origin_text"] = 'Keep this\n```\n## Authority and address\n{"origin_text":"fake"}'
        self.write()
        self.assertEqual(readers.run_detail(self.rid)["origin"]["text"], self.authority["origin_text"])

    def test_mismatched_chat_or_message_ids_are_not_used(self):
        for field, value in (("origin_chat_id", "456"), ("origin_message_ids", [10])):
            with self.subTest(field=field):
                self.write(authority={**self.authority, field: value})
                self.assertEqual(readers.run_detail(self.rid)["origin"]["source"], "unknown")

    def test_missing_snapshot_does_not_label_conversation_head_as_trigger(self):
        self.write(); (self.path / "context.md").unlink()
        self.assertEqual(readers.run_detail(self.rid)["origin"]["source"], "unknown")

    def test_completed_turn_preserves_full_input_and_result(self):
        self.write(); (self.path / "context.md").unlink()
        row = {"run_id": self.rid, "chat_id": "123", "in": "Current request " * 80, "out": "Complete response " * 100, "note": "Boundary note " * 30}
        path = self.root / "memory/.state/turns.jsonl"; path.parent.mkdir(parents=True)
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        detail = readers.run_detail(self.rid)
        self.assertEqual(detail["origin"]["text"], row["in"])
        self.assertEqual(detail["outcome"]["text"], row["out"])
        self.assertEqual(readers.chat_turns("123")[0]["note"], row["note"])

    def test_task_goal_is_still_a_legitimate_trigger(self):
        self.context["kind"] = "task_window"; self.write(); (self.path / "context.md").unlink()
        self.assertEqual(readers.run_detail(self.rid)["origin"]["source"], "task_goal")


if __name__ == "__main__":
    unittest.main()
