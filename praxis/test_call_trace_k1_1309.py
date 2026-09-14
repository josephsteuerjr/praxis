"""K1 (13.09): в леджере вызовов рядом с `cached` лежат ключ кэша, отпечаток system и
cache_creation — иначе промах первого вызова хода нечем атрибутировать (байты / ключ / простой).

Запуск: python praxis_test.py test_call_trace_k1_1309 -v
"""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import llm  # noqa: E402


class CallTraceFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "llm_calls.jsonl"
        patch = mock.patch.object(llm, "_CALL_TRACE", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def _last(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])

    def test_fields_written_when_given(self):
        llm._call_trace("voice", "gpt-5.6-sol", ok=True, cached=19456, prompt=49000,
                        out_tokens=200, latency_ms=1200, tools_digest="7b3c45f99602d481",
                        key="praxis:gpt-5.6-sol:room:-1001240718803",
                        sys_sha8="0123abcd", sys_len=23340, cc=0)
        row = self._last()
        self.assertEqual(row["key"], "praxis:gpt-5.6-sol:room:-1001240718803")
        self.assertEqual(row["sys"], "0123abcd")
        self.assertEqual(row["slen"], 23340)
        # ноль — «провайдер сказал: записи в кэш не было», а не «не знаем»
        self.assertEqual(row["cc"], 0)
        # старые поля на месте
        self.assertEqual(row["cached"], 19456)
        self.assertEqual(row["tools"], "7b3c45f99602d481")

    def test_fields_absent_when_unknown(self):
        llm._call_trace("voice", "glm-5.3", ok=False, cached=0, prompt=0, out_tokens=0,
                        latency_ms=5, error="boom")
        row = self._last()
        for key in ("key", "sys", "slen", "cc"):
            self.assertNotIn(key, row, key)
        self.assertEqual(row["err"], "boom")

    def test_long_key_is_clipped_and_sha_is_eight(self):
        llm._call_trace("voice", "m", ok=True, cached=1, prompt=1, out_tokens=1, latency_ms=1,
                        key="k" * 300, sys_sha8="0123456789abcdef", sys_len=7, cc=-1)
        row = self._last()
        self.assertEqual(len(row["key"]), 96)
        self.assertEqual(row["sys"], "01234567")
        self.assertNotIn("cc", row, "-1 = провайдер не сообщил → поля нет")


class _AnthResp:
    def __init__(self, **usage):
        self.stop_reason = "end_turn"
        self.model = "glm-5.3"
        self.content = [types.SimpleNamespace(type="text", text="ок")]
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=3, **usage)


class _FakeAnthropic:
    def __init__(self, resp):
        self.calls: list[dict] = []
        self._resp = resp
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self._resp


class ZeroCacheCreationFromAnthropicSurvives(unittest.TestCase):
    """Её reviewer 13.09 на K1: anthropic-ветка клала `cache_creation` в usage только при
    ненулевом значении (`if _cc:`), и «провайдер сказал 0» доезжало до леджера как «не
    сообщил» (-1) — ровно то различие, которое K1 обещал хранить."""

    def setUp(self) -> None:
        self.seen: dict = {}
        for patch in (mock.patch.object(llm, "_call_trace", lambda role, model, **kw: self.seen.update(kw)),
                      mock.patch.object(llm, "_usage_add"),
                      mock.patch.object(llm, "_brain_note")):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(llm.clear_test_clients)

    def _chat(self, **usage) -> llm.LLMResponse:
        llm.use_test_client(_FakeAnthropic(_AnthResp(**usage)), "anthropic")
        return llm.chat("voice", messages=[{"role": "user", "content": "hi"}])

    def test_zero_from_provider_reaches_the_ledger_as_zero(self):
        out = self._chat(cache_read_input_tokens=5, cache_creation_input_tokens=0)
        self.assertEqual(out.usage.get("cache_creation"), 0)
        self.assertEqual(out.usage.get("cache_read"), 5)
        self.assertEqual(self.seen.get("cc"), 0)
        self.assertEqual(self.seen.get("cached"), 5)

    def test_missing_field_stays_unknown(self):
        out = self._chat()
        self.assertNotIn("cache_creation", out.usage)
        self.assertEqual(self.seen.get("cc"), -1)

    def test_positive_value_still_travels(self):
        out = self._chat(cache_creation_input_tokens=1200)
        self.assertEqual(out.usage.get("cache_creation"), 1200)
        self.assertEqual(self.seen.get("cc"), 1200)


if __name__ == "__main__":
    unittest.main()
