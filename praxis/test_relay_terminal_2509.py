"""Терминал реле читается ДО ответа: лимит подписки — типизированная ошибка, а не реплика.

25.09.2026, баг тестировщика (Сергей): при исчерпанном окне подписки реле отдавало
`finish_reason="error"` и английский текст «Both OpenAI subscriptions are currently
unavailable…» прямо в content стрима. Движок читал это как оборванный стрим, повторял
по тому же каналу, «фолбэчил» во второе имя модели того же реле и в итоге показывал
владельцу чужую диагностику. Под `RELAY_TYPED_TERMINAL=field` реле кладёт рядом с
чанком структурный `relay_terminal`; здесь проверяется, что движок его читает первым.
"""
import os
import shutil
import tempfile
import time
import types
import unittest
from pathlib import Path

import llm

QUOTA = "subscription_window_exhausted"


def _chunk(text=None, finish=None, terminal=None):
    delta = types.SimpleNamespace(content=text, tool_calls=None)
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(choices=[choice], usage=None, relay_terminal=terminal)


class FakeRelay:
    """openai-совместимый клиент, отдающий стримы по очереди (список чанков на вызов)."""

    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        if not self.streams:
            raise AssertionError("в реле сходили чаще, чем ожидал стенд")
        return iter(self.streams.pop(0))


class FakeAnthropic:
    def __init__(self, text="ок"):
        self.calls = []
        self.text = text
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=self.text)],
            stop_reason="end_turn", model=kw.get("model"),
            usage=types.SimpleNamespace(input_tokens=3, output_tokens=1))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_relay_terminal_"))
        self._orig = [(llm, k, getattr(llm, k))
                      for k in ("CONFIG_PATH", "JOURNAL_DIR", "QUOTA_STATE_PATH")]
        llm.CONFIG_PATH = self.tmp / "llm.json"
        llm.JOURNAL_DIR = self.tmp / "journal"
        llm.QUOTA_STATE_PATH = self.tmp / "quota.json"
        self._cache0 = dict(llm._CACHE)
        llm._CACHE.update(mtime=None, cfg=None)
        self._state0 = {r: dict(s) for r, s in llm._STATE.items()}
        for s in llm._STATE.values():
            s.update(on_fallback=False, last_error="")
        self._hold0 = dict(llm._ENDPOINT_HOLD)
        llm._ENDPOINT_HOLD.clear()
        llm.clear_test_clients()
        self._env = {k: os.environ.get(k) for k in
                     ("GLM_API_KEY", "GLM_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                      llm.VISION_PREPASS_LEVER)}
        for k in self._env:
            os.environ.pop(k, None)

    def tearDown(self):
        for mod, k, v in self._orig:
            setattr(mod, k, v)
        llm._CACHE.clear()
        llm._CACHE.update(self._cache0)
        for r, s in self._state0.items():
            llm._STATE[r].update(s)
        llm._ENDPOINT_HOLD.clear()
        llm._ENDPOINT_HOLD.update(self._hold0)
        llm.clear_test_clients()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, *, fallback_framework="", fallback_model=""):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"] = {"base_url": "http://127.0.0.1:5011", "api_key": "sk-frame-x"}
        cfg["frameworks"]["anthropic"] = {"base_url": "https://api.z.ai/api/anthropic",
                                          "api_key": "zai-key"}
        cfg["roles"]["voice"].update({"framework": "openai", "model": "gpt-5.6-sol",
                                      "fallback_framework": fallback_framework,
                                      "fallback_model": fallback_model})
        llm.save_config(cfg)
        return llm._config()

    def _chat(self):
        return llm.chat("voice", messages=[{"role": "user", "content": "привет"}])


class TerminalIsTyped(Base):
    def test_quota_terminal_is_an_error_not_a_reply_and_is_not_retried(self):
        self._cfg()
        until = time.time() + 3600
        relay = FakeRelay([_chunk(text="Both OpenAI subscriptions are currently unavailable.",
                                  finish="error",
                                  terminal={"code": QUOTA, "message": "Both …", "slot": "main",
                                            "resets_at": until})])
        llm.use_test_client(relay, "openai")
        with self.assertRaises(llm.QuotaExhaustedError) as caught:
            self._chat()
        self.assertEqual(len(relay.calls), 1, "лимит не рассосётся за секунду: повтора нет")
        self.assertAlmostEqual(caught.exception.resets_at, until, delta=1)
        self.assertNotIsInstance(caught.exception, llm.BrokenChannelError)
        hold = llm.quota_state()["holds"]
        self.assertEqual(len(hold), 1)
        self.assertTrue(hold[0]["words"].startswith("подписка исчерпана до "), hold[0]["words"])
        self.assertEqual(llm.snapshot()["voice"]["held_words"], hold[0]["words"])
        self.assertTrue(llm.QUOTA_STATE_PATH.exists(), "окно читает удержание из файла")

    def test_needs_login_and_unavailable_have_their_own_classes(self):
        self._cfg()
        relay = FakeRelay([_chunk(finish="error", terminal={"code": "subscription_needs_login",
                                                             "message": "sign in"})],
                          [_chunk(finish="error", terminal={"code": "subscriptions_unavailable",
                                                             "message": "mixed"})])
        llm.use_test_client(relay, "openai")
        with self.assertRaises(llm.RelayNeedsLoginError):
            self._chat()
        llm._ENDPOINT_HOLD.clear()
        with self.assertRaises(llm.RelayUnavailableError):
            self._chat()

    def test_upstream_codes_keep_the_old_broken_channel_path(self):
        self._cfg()
        streams = [[_chunk(finish="error", terminal={"code": "upstream_error", "message": "502"})]
                   for _ in range(llm.EMPTY_RETRIES + 1)]
        relay = FakeRelay(*streams)
        llm.use_test_client(relay, "openai")
        with self.assertRaises(llm.EmptyResponseError):
            self._chat()
        self.assertEqual(len(relay.calls), llm.EMPTY_RETRIES + 1,
                         "пустой обрыв апстрима по-прежнему повторяется своим каналом")
        self.assertEqual(llm.quota_state()["holds"], [])


class FallbackGoesElsewhere(Base):
    def test_no_fallback_into_the_same_relay(self):
        self._cfg(fallback_framework="openai", fallback_model="gpt-5.6-luna")
        relay = FakeRelay([_chunk(finish="error",
                                  terminal={"code": QUOTA, "message": "…", "resets_at": time.time() + 600})])
        llm.use_test_client(relay, "openai")
        with self.assertRaises(llm.QuotaExhaustedError):
            self._chat()
        self.assertEqual(len(relay.calls), 1, "второе имя модели того же реле — тот же счётчик")

    def test_fallback_to_another_endpoint_and_the_hold_skips_the_relay_next_time(self):
        self._cfg(fallback_framework="anthropic", fallback_model="glm-5.3")
        relay = FakeRelay([_chunk(finish="error",
                                  terminal={"code": QUOTA, "message": "…", "resets_at": time.time() + 600})])
        anth = FakeAnthropic("запасной ответил")
        llm.use_test_client(relay, "openai")
        llm.use_test_client(anth, "anthropic")
        first = self._chat()
        self.assertEqual(first.framework, "anthropic")
        self.assertEqual(first.text, "запасной ответил")
        self.assertEqual(len(relay.calls), 1)
        second = self._chat()
        self.assertEqual(second.framework, "anthropic")
        self.assertEqual(len(relay.calls), 1, "пока окно закрыто, в реле не ходим")
        self.assertEqual(len(anth.calls), 2)
        self.assertTrue(llm._STATE["voice"]["on_fallback"])

    def test_expired_hold_lets_the_relay_be_tried_again(self):
        self._cfg(fallback_framework="anthropic", fallback_model="glm-5.3")
        relay = FakeRelay([_chunk(text="снова живое", finish="stop")])
        llm.use_test_client(relay, "openai")
        llm.use_test_client(FakeAnthropic(), "anthropic")
        llm._ENDPOINT_HOLD[llm._endpoint_key("openai")] = {
            "framework": "openai", "endpoint": llm._endpoint_key("openai"), "code": QUOTA,
            "message": "…", "slot": "", "since": time.time() - 100, "until": time.time() - 1,
            "words": "подписка исчерпана до 00:00"}
        resp = self._chat()
        self.assertEqual(resp.framework, "openai")
        self.assertEqual(resp.text, "снова живое")
        self.assertEqual(llm.quota_state()["holds"], [], "истёкшее удержание снято")

    def test_default_hold_is_the_fifteen_minutes_the_changelog_promises(self):
        self.assertEqual(llm.QUOTA_HOLD_DEFAULT_SEC, 900.0)
        self.assertEqual(llm.LOGIN_HOLD_SEC, 60.0)

    def test_unknown_reset_holds_for_the_default_window_and_says_so(self):
        self._cfg()
        relay = FakeRelay([_chunk(finish="error", terminal={"code": QUOTA, "message": "…"})])
        llm.use_test_client(relay, "openai")
        before = time.time()
        with self.assertRaises(llm.QuotaExhaustedError):
            self._chat()
        hold = llm.quota_state()["holds"][0]
        self.assertGreaterEqual(hold["until"], before + llm.QUOTA_HOLD_DEFAULT_SEC - 1)
        self.assertIn("время восстановления неизвестно", hold["words"])


class ReviewA1(Base):
    def _relay_quota(self, **term):
        term.setdefault("code", QUOTA)
        term.setdefault("message", "Both OpenAI subscriptions are currently unavailable")
        return FakeRelay([_chunk(finish="error", terminal=term)])

    def test_terminal_after_spoken_ends_the_turn_instead_of_fallback(self):
        self._cfg(fallback_framework="anthropic", fallback_model="glm-5.3")
        relay = self._relay_quota(resets_at=time.time() + 600)
        anth = FakeAnthropic("запасной переисполнил бы решение")
        llm.use_test_client(relay, "openai")
        llm.use_test_client(anth, "anthropic")
        resp = llm.chat("voice", messages=[{"role": "user", "content": "что дальше?"}],
                        end_after_spoken=True)
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual(resp.text, "")
        self.assertEqual(anth.calls, [], "после сказанного запасного не зовут (A1 F1)")
        self.assertTrue(llm.quota_state()["holds"], "удержание всё равно поставлено")
        # и синтетический путь при действующем удержании — тоже конец хода, не запасной
        resp2 = llm.chat("voice", messages=[{"role": "user", "content": "ещё?"}],
                         end_after_spoken=True)
        self.assertEqual(resp2.stop_reason, "end_turn")
        self.assertEqual(anth.calls, [])
        self.assertEqual(len(relay.calls), 1)

    def test_terminal_after_spoken_without_fallback_does_not_escape(self):
        self._cfg()
        llm.use_test_client(self._relay_quota(resets_at=time.time() + 600), "openai")
        resp = llm.chat("voice", messages=[{"role": "user", "content": "что дальше?"}],
                        end_after_spoken=True)
        self.assertEqual(resp.stop_reason, "end_turn")

    def test_unavailable_words_are_russian_and_by_code(self):
        self._cfg()
        llm.use_test_client(FakeRelay([_chunk(finish="error", terminal={
            "code": "subscriptions_unavailable",
            "message": "Both OpenAI subscriptions are currently unavailable (main: 401)"})]), "openai")
        with self.assertRaises(llm.RelayUnavailableError):
            self._chat()
        hold = llm.quota_state()["holds"][0]
        self.assertEqual(hold["words"], "подписки отказали по разным причинам (нужен вход / лимит)")
        self.assertIn("401", hold["message"], "английский текст остаётся в message")

    def test_needs_login_is_held_briefly(self):
        self._cfg()
        llm.use_test_client(FakeRelay([_chunk(finish="error", terminal={
            "code": "subscription_needs_login", "message": "login"})]), "openai")
        before = time.time()
        with self.assertRaises(llm.RelayNeedsLoginError):
            self._chat()
        hold = llm.quota_state()["holds"][0]
        self.assertLessEqual(hold["until"], before + llm.LOGIN_HOLD_SEC + 2)
        self.assertGreater(hold["until"], before + 10)

    def test_no_fallback_hold_is_quiet_and_probes_rarely(self):
        self._cfg()
        relay = FakeRelay([_chunk(finish="error", terminal={"code": QUOTA, "message": "…",
                                                             "resets_at": time.time() + 3600})])
        llm.use_test_client(relay, "openai")
        journal_before = _journal_lines(self)
        failed_before = len([r for r in _trace_rows(self) if r.get("ok") is False])
        for _ in range(5):
            with self.assertRaises(llm.QuotaExhaustedError):
                self._chat()
        self.assertEqual(len(relay.calls), 1, "при удержании без запасного в реле не долбим (A1 F4)")
        self.assertEqual(_journal_lines(self) - journal_before, 1, "в дневник — одна строка, не пять")
        # синтетические отказы не ложатся в след как сбои модели (A1 F6): один настоящий поход
        failed_after = len([r for r in _trace_rows(self) if r.get("ok") is False])
        self.assertEqual(failed_after - failed_before, 1)
        # прошло HOLD_PROBE_EVERY_SEC — один пробный поход
        llm._ENDPOINT_HOLD[llm._endpoint_key("openai")]["probed_at"] = time.time() - llm.HOLD_PROBE_EVERY_SEC - 1
        relay.streams.append([_chunk(finish="error", terminal={"code": QUOTA, "message": "…",
                                                                "resets_at": time.time() + 3600})])
        with self.assertRaises(llm.QuotaExhaustedError):
            self._chat()
        self.assertEqual(len(relay.calls), 2)

    def test_hold_words_use_her_clock(self):
        import praxis_time
        until = time.time() + 3600
        expected = praxis_time.local_from(until).strftime("%H:%M")
        self.assertEqual(llm._hold_words(until), "до " + expected)

    def test_resets_in_seconds_without_resets_at_is_honoured(self):
        self._cfg()
        llm.use_test_client(FakeRelay([_chunk(finish="error", terminal={
            "code": QUOTA, "message": "…", "resets_in_seconds": 3600})]), "openai")
        before = time.time()
        with self.assertRaises(llm.QuotaExhaustedError):
            self._chat()
        hold = llm.quota_state()["holds"][0]
        self.assertGreaterEqual(hold["until"], before + 3600 - 2)
        self.assertNotIn("неизвестно", hold["words"])

    def test_terminal_in_model_extra_is_read(self):
        self._cfg()
        delta = types.SimpleNamespace(content=None, tool_calls=None)
        choice = types.SimpleNamespace(delta=delta, finish_reason="error")
        chunk = types.SimpleNamespace(choices=[choice], usage=None,
                                      model_extra={"relay_terminal": {"code": QUOTA, "message": "…",
                                                                      "resets_at": time.time() + 60}})
        llm.use_test_client(FakeRelay([chunk]), "openai")
        with self.assertRaises(llm.QuotaExhaustedError):
            self._chat()


def _journal_lines(case) -> int:
    total = 0
    if llm.JOURNAL_DIR.exists():
        for path in llm.JOURNAL_DIR.glob("*.md"):
            total += sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                         if "подписка" in line)
    return total


def _trace_rows(case) -> list:
    import json
    path = llm.USAGE_PATH.parent / "llm_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
