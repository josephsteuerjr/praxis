# -*- coding: utf-8 -*-
"""Имя потолка ответа выбирает АДРЕСАТ, а не имя модели (19.09.2026).

ЧТО ЭТО ЛОВИТ. Живой случай: у пользователя Элен 0.7.1 клиент смотрит прямо в
api.openai.com, мимо реле, и каждый запрос падал с 400 «Unsupported parameter:
max_tokens is not supported with this model. Use max_completion_tokens instead».
Имя модели у него ровно то же, что у реле, — различает адресат, то есть хост
`base_url` клиента. Реле по-прежнему получает `max_tokens` (у него нет
`max_completion_tokens` в `ChatRequest`; стенд `test_relay_request_contract`
держит эту сторону).
"""
from __future__ import annotations

import unittest
from unittest import mock

import llm


class _Stop(Exception):
    pass


def _sent(base_url) -> dict:
    """Что ушло в `chat.completions.create` у клиента с таким base_url."""
    seen: dict = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            raise _Stop()

    client = mock.Mock()
    client.chat.completions = _Completions()
    if base_url is None:
        del client.base_url            # клиент без адреса: реле по умолчанию
    else:
        client.base_url = base_url
    with mock.patch.object(llm, "cache_address", return_value=""):
        try:
            llm._call_openai(client, "gpt-5.6-sol", system="s",
                             messages=[{"role": "user", "content": "x"}],
                             tools=None, max_tokens=777, thinking=0)
        except _Stop:
            pass
    return seen


class _Url:
    """Как `httpx.URL` у настоящего клиента openai: не str, но str() даёт адрес."""

    def __init__(self, text: str):
        self._text = text

    def __str__(self) -> str:
        return self._text


class TheAddresseeNamesTheField(unittest.TestCase):
    def test_direct_openai_gets_max_completion_tokens(self):
        sent = _sent("https://api.openai.com/v1")
        self.assertEqual(sent.get("max_completion_tokens"), 777)
        self.assertNotIn("max_tokens", sent, "настоящему OpenAI max_tokens нельзя — 400")

    def test_httpx_style_url_object_counts_too(self):
        sent = _sent(_Url("https://api.openai.com/v1/"))
        self.assertEqual(sent.get("max_completion_tokens"), 777)
        self.assertNotIn("max_tokens", sent)

    def test_host_is_matched_not_the_substring(self):
        # Чужой хост с «openai.com» в имени — не OpenAI: имя поля остаётся у реле.
        for url in ("https://evil-openai.com/v1", "https://openai.com.example/v1",
                    "https://relay.local/openai.com/v1"):
            with self.subTest(url=url):
                sent = _sent(url)
                self.assertEqual(sent.get("max_tokens"), 777, url)
                self.assertNotIn("max_completion_tokens", sent)

    def test_case_and_subdomains(self):
        for url in ("HTTPS://API.OPENAI.COM/v1", "https://openai.com/v1",
                    "https://eu.api.openai.com/v1"):
            with self.subTest(url=url):
                self.assertEqual(_sent(url).get("max_completion_tokens"), 777, url)


class TheRelayStillGetsItsOwnField(unittest.TestCase):
    def test_relay_and_compatible_servers_get_max_tokens(self):
        for url in ("http://127.0.0.1:5011/v1", "http://host.docker.internal:5011/v1",
                    "https://api.z.ai/api/paas/v4", "https://openrouter.ai/api/v1"):
            with self.subTest(url=url):
                sent = _sent(url)
                self.assertEqual(sent.get("max_tokens"), 777, url)
                self.assertNotIn("max_completion_tokens", sent)

    def test_client_without_an_address_is_the_relay(self):
        sent = _sent(None)
        self.assertEqual(sent.get("max_tokens"), 777)

    def test_the_same_model_name_goes_both_ways(self):
        # Имя модели одно — поле разное: решает адресат, не имя.
        self.assertIn("max_completion_tokens", _sent("https://api.openai.com/v1"))
        self.assertIn("max_tokens", _sent("http://127.0.0.1:5011/v1"))


if __name__ == "__main__":
    unittest.main()
