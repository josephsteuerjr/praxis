"""Live preview of a running model call (Tim, Helene 2.7.2, local Gemma at ~6 tokens/s).

The window used to show the answer only after the call ended. The core now feeds the
words being generated — reasoning, plain text and the text argument of a reply call — to an
observer that writes one small file next to the run. Without an observer nothing changes.
"""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import agent
import llm
import model_preview


def chunk(content=None, reasoning=None, tool=None, finish=None):
    calls = []
    if tool:
        index, name, arguments = tool
        calls = [types.SimpleNamespace(index=index, id="call-1" if name else None,
                                       function=types.SimpleNamespace(name=name, arguments=arguments))]
    delta = types.SimpleNamespace(content=content, tool_calls=calls, reasoning_content=reasoning)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


STREAM = [chunk(reasoning="Проверю "), chunk(reasoning="файл."),
          chunk(content="заметка"),
          chunk(tool=(0, "reply", '{"text": "Привет, \\"Тим\\"\\n')),
          chunk(tool=(0, "", 'файл \\u0433отов')),
          chunk(tool=(0, "", '", "reply_to": "1"}')),
          chunk(finish="tool_calls")]


class Preview(unittest.TestCase):
    def test_without_an_observer_feeding_is_a_noop_and_the_result_is_identical(self):
        plain = llm._openai_from_stream(iter(STREAM), "fake")
        seen = []
        with model_preview.observe(seen.append, interval=0):
            observed = llm._openai_from_stream(iter(STREAM), "fake")
        self.assertEqual(plain.blocks, observed.blocks)
        self.assertEqual(plain.stop_reason, observed.stop_reason)
        self.assertEqual(plain.continuation, observed.continuation)
        model_preview.feed("text", "nobody listens")  # no observer: no error, no effect
        self.assertTrue(seen)

    def test_stream_feeds_thinking_text_and_the_reply_being_written(self):
        seen = []
        with model_preview.observe(seen.append, interval=0):
            llm._openai_from_stream(iter(STREAM), "fake")
        last = seen[-1]
        self.assertEqual(last["thinking"], "Проверю файл.")
        self.assertEqual(last["text"], "заметка")
        self.assertEqual(last["reply"], 'Привет, "Тим"\nфайл готов')
        self.assertEqual(last["reply_chars"], len(last["reply"]))
        # mid-stream the reply is decoded only as far as it has arrived
        partial = [row["reply"] for row in seen if row["reply"]]
        self.assertTrue(partial[0].startswith("Привет"))

    def test_partial_json_decoding_stops_at_incomplete_escapes(self):
        f = model_preview.partial_text_field
        self.assertEqual(f('{"reply_to":"1","text":"ab'), "ab")
        self.assertEqual(f('{"text":"ab\\'), "ab")
        self.assertEqual(f('{"text":"ab\\u04'), "ab")
        self.assertEqual(f('{"text":"\\ud83d\\ude00!"}'), "😀!")
        self.assertEqual(f('{"note":"x"}'), "")

    def test_restart_clears_a_retried_attempt_and_throttle_and_tail_hold(self):
        seen = []
        with model_preview.observe(seen.append, interval=3600):
            model_preview.feed("text", "a")
            model_preview.feed("text", "b")  # throttled
            self.assertEqual(len(seen), 1)
            model_preview.restart()
        with model_preview.observe(seen.append, interval=0):
            model_preview.feed("text", "x" * (model_preview.LIMIT + 10))
        self.assertEqual(len(seen[-1]["text"]), model_preview.LIMIT)
        self.assertEqual(seen[-1]["text_chars"], model_preview.LIMIT + 10)

    def test_a_failing_sink_never_breaks_the_call(self):
        def boom(_):
            raise OSError("reader holds the file")
        with model_preview.observe(boom, interval=0):
            out = llm._openai_from_stream(iter(STREAM), "fake")
        self.assertEqual(out.stop_reason, "tool_use")


class AnthropicStream:
    """messages.stream(): iterable events, then get_final_message (SDK MessageStream shape)."""
    def __init__(self, events, final):
        self.events, self.final, self.calls = events, final, []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        outer = self

        class Context:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter(outer.events)

            def get_final_message(self):
                return outer.final
        return Context()


def event(kind, index=0, **values):
    return types.SimpleNamespace(type=kind, index=index, **values)


class AnthropicPreview(unittest.TestCase):
    def test_deltas_are_previewed_and_the_final_message_is_unchanged(self):
        final = types.SimpleNamespace(stop_reason="tool_use", model="m",
            content=[types.SimpleNamespace(type="tool_use", id="t1", name="reply", input={"text": "Привет"})],
            usage=types.SimpleNamespace(input_tokens=5, output_tokens=2))
        events = [event("content_block_start", 0, content_block=types.SimpleNamespace(type="thinking")),
                  event("content_block_delta", 0, delta=types.SimpleNamespace(type="thinking_delta", thinking="думаю")),
                  event("content_block_start", 1, content_block=types.SimpleNamespace(type="tool_use", name="reply")),
                  event("content_block_delta", 1, delta=types.SimpleNamespace(type="input_json_delta", partial_json='{"text": "При')),
                  event("content_block_delta", 1, delta=types.SimpleNamespace(type="input_json_delta", partial_json='вет"}'))]
        args = dict(system="s", messages=[{"role": "user", "content": "hi"}], tools=None, max_tokens=100, thinking=None)
        plain = llm._call_anthropic(AnthropicStream(events, final), "m", **args)
        seen = []
        with model_preview.observe(seen.append, interval=0):
            observed = llm._call_anthropic(AnthropicStream(events, final), "m", **args)
        self.assertEqual(plain.blocks, observed.blocks)
        self.assertEqual((seen[-1]["thinking"], seen[-1]["reply"]), ("думаю", "Привет"))


class AgentSink(unittest.TestCase):
    def test_sink_writes_a_scrubbed_preview_next_to_the_run_and_clears_it(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            runs = types.SimpleNamespace(path=lambda run_id: run_dir)
            current = types.SimpleNamespace(run_id="run-1")
            with mock.patch.object(agent, "_runs", return_value=runs):
                sink = agent._model_preview_sink(current, "model-abc", ("sk-SECRET-123",))
                sink({"text": "", "thinking": "key sk-SECRET-123", "reply": "ok",
                      "text_chars": 0, "thinking_chars": 16, "reply_chars": 2})
                data = json.loads((run_dir / agent.MODEL_PREVIEW_FILE).read_text("utf-8"))
                self.assertEqual((data["schema"], data["call_id"], data["reply"]),
                                 ("helene.model-preview.v1", "model-abc", "ok"))
                self.assertNotIn("sk-SECRET-123", json.dumps(data))
                self.assertIn("T", data["at"])
                agent._clear_model_preview(current)
                self.assertFalse((run_dir / agent.MODEL_PREVIEW_FILE).exists())
                self.assertEqual(list(run_dir.iterdir()), [])
        self.assertIsNone(agent._model_preview_sink(None, "model-x", ()))


if __name__ == "__main__":
    unittest.main()
