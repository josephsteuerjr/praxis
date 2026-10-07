# -*- coding: utf-8 -*-
"""Настоящий OpenAI — через Responses API; рукопожатие — с рукой (28.09.2026).

ЧТО ЭТО ЛОВИТ. Баг-репорт Йоно (агент Дмитрия К) через Arête: мозг по ключу OpenAI на
gpt-5.6-terra (и gpt-6-sol) — первый же ход с инструментами падал 400 «Function tools with
reasoning_effort are not supported … in /v1/chat/completions. To use function tools, use
/v1/responses». Рукопожатие switch_brain шло без рук и проходило, так что агент сам ставил
себе мозг, на котором не мог двигаться. Здесь держится: к настоящему OpenAI — Responses
(инструменты плоской формой, глубина в `reasoning`), реле — по-прежнему chat/completions;
рукопожатие несёт руку и ступень роли; несовместимость модели уводит ход на запасную.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import llm


class _Stop(Exception):
    pass


def _client(base_url: str, answer=None):
    seen: dict = {}

    class _Responses:
        def create(self, **kw):
            seen["responses"] = kw
            if answer is None:
                raise _Stop()
            return answer

    class _Completions:
        def create(self, **kw):
            seen["chat"] = kw
            raise _Stop()

    client = mock.Mock()
    client.base_url = base_url
    client.responses = _Responses()
    client.chat.completions = _Completions()
    return client, seen


TOOLS = [{"name": "reply", "description": "say it", "input_schema": {
    "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]

HISTORY = [
    {"role": "user", "content": "привет"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "сейчас"},
        {"type": "tool_use", "id": "call_1", "name": "reply", "input": {"text": "привет!"}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "доставлено"},
        {"type": "text", "text": "дальше"}]},
]


class DirectOpenAIGoesToResponses(unittest.TestCase):
    def _send(self, **kw):
        client, seen = _client("https://api.openai.com/v1")
        with mock.patch.object(llm, "cache_address", return_value="praxis:x"):
            try:
                llm._call_openai(client, "gpt-5.6-terra", system="ты — агент", messages=HISTORY,
                                 tools=TOOLS, max_tokens=4096, **kw)
            except _Stop:
                pass
        return seen

    def test_tools_and_effort_travel_together(self):
        seen = self._send(thinking=4096)
        self.assertNotIn("chat", seen, "к настоящему OpenAI — не chat/completions")
        sent = seen["responses"]
        self.assertEqual(sent["reasoning"], {"effort": "medium"})
        self.assertEqual(sent["instructions"], "ты — агент")
        self.assertEqual(sent["max_output_tokens"], 4096)
        self.assertIs(sent["store"], False)
        self.assertEqual(sent["extra_body"], {"prompt_cache_key": "praxis:x"})
        tool = sent["tools"][0]
        self.assertEqual((tool["type"], tool["name"], tool["strict"]), ("function", "reply", False))
        self.assertIn("text", tool["parameters"]["properties"])

    def test_role_effort_when_no_thinking(self):
        sent = self._send(thinking=None, reasoning_effort="high")["responses"]
        self.assertEqual(sent["reasoning"], {"effort": "high"})

    def test_history_becomes_input_items(self):
        items = self._send(thinking=None)["responses"]["input"]
        self.assertEqual(items[0], {"role": "user", "content": "привет"})
        self.assertEqual(items[1], {"role": "assistant", "content": "сейчас"})
        self.assertEqual(items[2]["type"], "function_call")
        self.assertEqual((items[2]["call_id"], items[2]["name"]), ("call_1", "reply"))
        self.assertEqual(json.loads(items[2]["arguments"]), {"text": "привет!"})
        self.assertNotIn("id", items[2], "id принадлежит сохранённому ответу; store=False")
        self.assertEqual(items[3], {"type": "function_call_output", "call_id": "call_1",
                                    "output": "доставлено"})
        self.assertEqual(items[4], {"role": "user", "content": "дальше"})

    def test_relay_stays_on_chat_completions(self):
        client, seen = _client("http://127.0.0.1:5011/v1")
        with mock.patch.object(llm, "cache_address", return_value=""):
            try:
                llm._call_openai(client, "gpt-5.6-terra", system="s", messages=HISTORY,
                                 tools=TOOLS, max_tokens=100, thinking=None, reasoning_effort="low")
            except _Stop:
                pass
        self.assertIn("chat", seen)
        self.assertNotIn("responses", seen)
        self.assertEqual(seen["chat"]["extra_body"]["reasoning_effort"], "low")


class ResponsesAnswerIsRead(unittest.TestCase):
    def _read(self, answer):
        client, _ = _client("https://api.openai.com/v1", answer=answer)
        with mock.patch.object(llm, "cache_address", return_value=""):
            return llm._call_openai(client, "gpt-6-sol", system="", messages=[{"role": "user", "content": "x"}],
                                    tools=TOOLS, max_tokens=100, thinking=None)

    def test_text_and_call(self):
        out = self._read({
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "Готово."}]},
                {"type": "function_call", "call_id": "call_9", "name": "reply", "arguments": '{"text": "ok"}'},
            ],
            "usage": {"input_tokens": 1000, "output_tokens": 50,
                      "input_tokens_details": {"cached_tokens": 800}},
        })
        self.assertEqual(out.text, "Готово.")
        self.assertEqual(out.stop_reason, "tool_use")
        self.assertEqual(out.blocks[1], {"type": "tool_use", "id": "call_9", "name": "reply",
                                         "input": {"text": "ok"}})
        self.assertEqual((out.usage["in"], out.usage["out"], out.usage["cache_read"]), (200, 50, 800))
        self.assertEqual(out.framework, "openai")

    def test_budget_stop_is_named(self):
        out = self._read({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                          "output": [{"type": "reasoning"}], "usage": {}})
        self.assertEqual(out.stop_reason, "max_tokens")

    def test_broken_arguments_stay_visible(self):
        out = self._read({"status": "completed", "output": [
            {"type": "function_call", "call_id": "c", "name": "reply", "arguments": "{oops"}], "usage": {}})
        self.assertTrue(llm.is_malformed_json_input(out.blocks[0]["input"]))

    def test_failed_response_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self._read({"status": "failed", "error": {"code": "server_error", "message": "boom"}})


class _Http(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status_code = status


class HandshakeAndFallback(unittest.TestCase):
    def test_ping_carries_a_hand_and_the_role_effort(self):
        cfg = {"roles": {"voice": {"framework": "openai", "model": "gpt-6-sol", "reasoning_effort": "high"}}}
        seen = {}

        def fake_call(framework, model, **kw):
            seen.update(kw)
            return llm.LLMResponse(text="ok", blocks=[{"type": "text", "text": "ok"}])

        with mock.patch.object(llm, "_config", return_value=cfg), \
                mock.patch.object(llm, "_call", side_effect=fake_call), \
                mock.patch.object(llm, "_usage_add"):
            ok, err = llm.ping("voice")
        self.assertTrue(ok, err)
        self.assertEqual([t["name"] for t in seen["tools"]], ["handshake_probe"])
        self.assertEqual(seen["reasoning_effort"], "high")

    def test_incompatible_model_falls_back(self):
        self.assertTrue(llm._fallbackable(_Http(400, "Function tools with reasoning_effort are not "
                                                     "supported for gpt-6-sol in /v1/chat/completions")))
        self.assertTrue(llm._fallbackable(_Http(404, "The model `gpt-9` does not exist")))
        self.assertFalse(llm._fallbackable(_Http(400, "Invalid 'messages[3].content': empty")))


if __name__ == "__main__":
    unittest.main()
