# active_workspace:praxis/test_frame_literal.py
"""Дословный рендер валидируется настоящим durable-писателем и падает честно.

Фикстуры пишутся реальным RunManager (как в test_keat_source.py), а не синтезом
руками: формат manifest/events/ResultRef проверяется тем же путём, каким его
пишет прод. Никакого провайдера, сети или живой памяти.
"""
import json
from pathlib import Path
import tempfile
import unittest

import frame_literal
from run_context import RunContext
from run_manager import RunManager


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


MODEL_INPUT = {
    "system": [{"type": "text", "text": "# Конституция синтетики\n\nДословно."}],
    "tools": [
        {"name": "fixture", "input_schema": {"type": "object",
                                             "properties": {"a": {}, "b": {}}}},
        {"name": "plain", "input_schema": {"type": "object"}},
    ],
    "messages": [
        {"role": "user", "content": "привет дословно"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "fixture", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "синтетика"}]},
    ],
}


class FrameLiteralBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = RunManager(self.base)
        ctx = self.manager.create(RunContext.create(
            run_id="run-synthetic-literal", kind="chat", goal="synthetic fixture",
            principal_id="synthetic:owner", scope="owner",
            origin_chat_id="100", delivery_chat_id="100"), "# Synthetic only\n")
        self.run_id = ctx.run_id
        self.run_dir = self.manager.path(ctx.run_id)

    def store_input(self, call_id, payload):
        self.manager.store_result(
            self.run_id, encoded(payload).decode(), call_id=call_id,
            name="model-input", media_type="application/json; charset=utf-8",
            event_kind="model_input", idempotent=True)

    def input_path(self, number=1):
        return self.run_dir / "results" / f"{number:04d}-model-input.log"

    def render(self, call_id=None):
        return frame_literal.render_run(self.run_dir, call_id)




class TestRenderSuccess(FrameLiteralBase):
    def setUp(self):
        super().setUp()
        self.store_input("call-first", MODEL_INPUT)
        second = json.loads(json.dumps(MODEL_INPUT))
        second["system"] = [{"type": "text", "text": "# Второй кадр\n\nПоследний."}]
        self.store_input("call-second", second)

    def test_last_call_rendered_verbatim_by_default(self):
        text = self.render()
        self.assertIn("# Второй кадр", text)
        self.assertNotIn("# Конституция синтетики", text)
        self.assertIn("call-second", text)

    def test_explicit_call_id_render_contains_system_and_roles(self):
        text = self.render("call-first")
        self.assertIn("# Конституция синтетики", text)
        self.assertIn("### [0] user", text)
        self.assertIn("### [1] assistant", text)
        self.assertIn("### [2] user", text)
        self.assertIn("привет дословно", text)
        self.assertIn("run-synthetic-literal", text)
        self.assertIn("result-0001", text)

    def test_tools_summarized_not_dumped(self):
        text = self.render("call-first")
        self.assertIn("Всего схем: 2", text)
        self.assertIn("- fixture — object; параметры: a, b", text)
        self.assertIn("- plain — object; без свойств", text)
        # Схемы не дампятся целиком — их полный JSON остался в durable-файле.
        self.assertNotIn('"input_schema"', text)

    def test_scrub_boundary_note_names_marker_and_limit(self):
        text = self.render("call-first")
        self.assertIn("scrub boundary:", text)
        self.assertIn("scrubbed_possible", text)
        self.assertIn("признак в событии отсутствует", text)


class TestRenderFailures(FrameLiteralBase):
    def setUp(self):
        super().setUp()
        self.store_input("call-first", MODEL_INPUT)

    def test_sha_mismatch_is_an_error(self):
        # Подмена байтов файла после записи события: ResultRef в журнале всё ещё
        # держит старый sha256 — валидация обязана упасть, частичный рендер не отдаётся.
        self.input_path(1).write_bytes(encoded(MODEL_INPUT) + b" ")
        with self.assertRaises(frame_literal.FrameLiteralError) as raised:
            self.render()
        self.assertIn("keat_source не принял", str(raised.exception))

    def test_unknown_call_id_is_an_error(self):
        with self.assertRaises(frame_literal.FrameLiteralError) as raised:
            self.render("call-nowhere")
        self.assertIn("call-nowhere", str(raised.exception))
        self.assertIn("call-first", str(raised.exception))

    def test_no_model_input_events_is_an_error(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        manager = RunManager(Path(empty.name))
        manager.create(RunContext.create(
            run_id="run-empty-literal", kind="chat", goal="no model calls",
            principal_id="synthetic:owner", scope="owner"), "# empty\n")
        with self.assertRaises(frame_literal.FrameLiteralError) as raised:
            frame_literal.render_run(manager.path("run-empty-literal"))
        self.assertIn("нет событий model_input", str(raised.exception))


class TestCli(FrameLiteralBase):
    def setUp(self):
        super().setUp()
        self.store_input("call-first", MODEL_INPUT)

    def snapshot(self):
        return sorted(p.name for p in self.run_dir.rglob("*") if p.is_file())

    def test_stdout_by_default_and_no_files_written(self):
        before = self.snapshot()
        code = frame_literal.main([str(self.run_dir)])
        self.assertEqual(code, 0)
        self.assertEqual(self.snapshot(), before)

    def test_out_writes_file_and_bad_call_id_returns_two(self):
        target = self.base / "render.md"
        code = frame_literal.main([str(self.run_dir), "--out", str(target)])
        self.assertEqual(code, 0)
        self.assertIn("# Конституция синтетики", target.read_text(encoding="utf-8"))
        code = frame_literal.main([str(self.run_dir), "call-nowhere"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
