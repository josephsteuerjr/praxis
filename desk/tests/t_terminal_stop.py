# -*- coding: utf-8 -*-
"""Терминальная остановка хода и сетевые ретраи тем же контекстом (05.10, слово владельца).

Две правки одного дня, одна жалоба: «остановка происходит не полностью, а когда
не отправляется из-за проблем с сети, остановить нельзя вовсе».

  * model_watch: вызов модели уходит в дочерний поток СО СНАПШОТОМ контекста,
    поток хода между порциями ожидания спрашивает состояние прогона —
    стоп-исключение поднимается немедленно; брошенный вызов доделывается вхолостую,
    его поздний сбой глушится (llm.abandon_call), чтобы призрак не запирал
    эндпойнт живому следующему ходу;
  * llm: обрыв сети/таймаут — транспортный повтор ТЕМ ЖЕ каналом и ТЕМ ЖЕ
    контекстом (до сегодня был только фолбэк на другой фреймворк).

Живая сеть не зовётся: клиенты — фейки в llm._TEST_CLIENTS, конфиг — во tmp.
Запуск:  python tests/t_terminal_stop.py
"""
import contextvars
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREE = Path(os.environ.get('HELENE_TREE_SRC', str(ROOT / 'helene' / 'core')))
os.environ['PRAXIS_TEST'] = '1'
# Ретраи без пауз: стенд обязан быть быстрым, проверяет он механику, а не сон.
os.environ['PRAXIS_NET_RETRY_PAUSE_SEC'] = '0'
sys.path[:0] = [str(TREE), str(ROOT / 'praxis')]
import _sandbox  # noqa: E402
assert _sandbox.activate_if_testing()
import llm  # noqa: E402
import model_watch  # noqa: E402


# ────────────────────────── model_watch: стоп не ждёт сети ──────────────────

class ModelWatch(unittest.TestCase):
    def test_result_and_error_pass_through(self):
        self.assertEqual(
            model_watch.call_with_stop(lambda: 7, lambda: None, poll_sec=0.01), 7)

        def boom():
            raise RuntimeError("сеть")

        with self.assertRaises(RuntimeError):
            model_watch.call_with_stop(boom, lambda: None, poll_sec=0.01)

    def test_stop_wins_while_work_hangs_in_network(self):
        """«SDK висит в сети» — стоп обязан прийти за доли секунды, не за таймаут SDK."""
        class Stopped(Exception):
            pass

        started = threading.Event()

        def work():
            started.set()
            time.sleep(30)          # зависший вызов; ответ никто не прочтёт
            return "поздно"

        def on_stop():
            return Stopped("ход остановлен") if started.is_set() else None

        t0 = time.monotonic()
        with self.assertRaises(Stopped):
            model_watch.call_with_stop(work, on_stop, poll_sec=0.02)
        self.assertLess(time.monotonic() - t0, 5,
                        "стоп-исключение обязано прийти за доли секунды")

    def test_blind_status_keeps_waiting(self):
        """Слепота стоп-проверки (None) — не повод рвать живой вызов."""

        def on_stop():
            return None

        self.assertEqual(
            model_watch.call_with_stop(lambda: "ответ", on_stop, poll_sec=0.01), "ответ")

    def test_worker_inherits_the_caller_context(self):
        """Свежая нить в питоне начинает с ПУСТОГО контекста: без снапшота трейс
        вызова терял бы итерацию тул-цикла, и привязку прогона (ревью 05.10)."""
        marker = contextvars.ContextVar("praxis_test_marker")
        seen = {}

        def work():
            seen["value"] = marker.get(None)
            return "ок"

        def set_and_call():
            marker.set("живой кадр")
            return model_watch.call_with_stop(work, lambda: None, poll_sec=0.01)

        self.assertEqual(set_and_call(), "ок")
        self.assertEqual(seen["value"], "живой кадр",
                         "контекст вызова не доехал до нити — атрибуция трейса потеряна")

    def test_abandon_and_settle_hooks_bookend_the_ghost(self):
        """Стоп поднялся — нить помечена призраком; нить доделалась — пометка снята
        (ident переиспользуются, протухшая пометка гллушила бы чужой вызов)."""
        abandoned, settled = [], []
        started = threading.Event()

        def work():
            started.set()
            time.sleep(0.5)
            return "поздно"

        class Stopped(Exception):
            pass

        def on_stop():
            return Stopped("ход остановлен") if started.is_set() else None

        with self.assertRaises(Stopped):
            model_watch.call_with_stop(work, on_stop, poll_sec=0.01,
                                       on_abandon=lambda i: abandoned.append(i),
                                       on_settle=lambda i: settled.append(i))
        self.assertEqual(len(abandoned), 1, "нить не помечена призраком при стопе")
        deadline = time.monotonic() + 5
        while not settled and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(settled, abandoned, "пометка призрака не снята по завершении нити")
        self.assertNotIn(abandoned[0], llm._ABANDONED_THREADS,
                         "протухшая пометка осталась в реестре llm")


# ────────────────────────── llm: ретраи тем же контекстом ────────────────────

class APIConnectionError(Exception):
    pass


class RateLimitError(Exception):
    pass


def _openai_resp(text="ок"):
    msg = types.SimpleNamespace(content=text, tool_calls=[])
    choice = types.SimpleNamespace(message=msg, finish_reason="stop")
    usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=2)
    return types.SimpleNamespace(choices=[choice], usage=usage)


class FlakyOpenAI:
    """Клиент, чья первая попытка падает обрывом сети."""

    def __init__(self, error):
        self.error = error
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            raise self.error
        return _openai_resp("жива")


class AlwaysNetError:
    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        raise APIConnectionError("сети нет")


class FakeAnthropic:
    def __init__(self, text="запасная жива"):
        self.calls = []
        self.messages = self
        content = [types.SimpleNamespace(type="text", text=text)]
        self._resp = types.SimpleNamespace(
            stop_reason="end_turn", content=content,
            usage=types.SimpleNamespace(input_tokens=5, output_tokens=2), model="glm-test")

    def create(self, **kw):
        self.calls.append(kw)
        return self._resp


class NetRetries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_netretry_"))
        self._orig = [(llm, k, getattr(llm, k)) for k in ("CONFIG_PATH", "JOURNAL_DIR")]
        llm.CONFIG_PATH = self.tmp / "llm.json"
        llm.JOURNAL_DIR = self.tmp / "journal"
        llm._CACHE.update(mtime=None, cfg=None)
        self._state0 = {r: dict(s) for r, s in llm._STATE.items()}
        for s in llm._STATE.values():
            s.update(on_fallback=False, last_error="")
        llm.clear_test_clients()
        llm._ABANDONED_THREADS.clear()

    def tearDown(self):
        for mod, k, v in self._orig:
            setattr(mod, k, v)
        for r, s in self._state0.items():
            llm._STATE[r].update(s)
        llm.clear_test_clients()
        llm._ABANDONED_THREADS.clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _voice_cfg(self, **over):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"] = {"api_key": "k-openai", "base_url": "http://127.0.0.1:9"}
        cfg["frameworks"]["anthropic"] = {"api_key": "k-zai", "base_url": "http://127.0.0.1:9"}
        cfg["roles"]["voice"].update({"framework": "openai", "model": "gpt-test"})
        cfg["roles"]["voice"].update(over)
        llm.save_config(cfg)

    def test_network_error_retried_with_the_same_context(self):
        fake = FlakyOpenAI(APIConnectionError("обрыв"))
        llm._TEST_CLIENTS["openai"] = fake
        self._voice_cfg()          # запасной ноги нет — помог только ретрай
        resp = llm.chat("voice", system="будь", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(resp.text, "жива")
        self.assertEqual(len(fake.calls), 2, "первая попытка упала — ровно один повтор")
        self.assertEqual(fake.calls[0]["messages"], fake.calls[1]["messages"],
                         "ретрай обязан ехать ТЕМ ЖЕ контекстом")

    def test_exhausted_retries_raise_the_network_error(self):
        fake = AlwaysNetError()
        llm._TEST_CLIENTS["openai"] = fake
        self._voice_cfg()
        with self.assertRaises(APIConnectionError):
            llm.chat("voice", system="будь", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(len(fake.calls), 1 + llm.NET_RETRIES,
                         "все транспортные попытки сделаны тем же каналом")

    def test_non_network_error_is_not_retried_same_channel(self):
        fake = FlakyOpenAI(RateLimitError("429"))
        llm._TEST_CLIENTS["openai"] = fake
        self._voice_cfg()
        with self.assertRaises(RateLimitError):
            llm.chat("voice", system="будь", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(len(fake.calls), 1,
                         "лимит — не обрыв сети: своего канала он не повторяет (прежняя граница)")

    def test_fallback_leg_still_works_after_net_retries(self):
        """Исчерпались ретраи — прежний фолбэк на другой фреймворк без правок."""
        fake = AlwaysNetError()
        reserve = FakeAnthropic()
        llm._TEST_CLIENTS["openai"] = fake
        llm._TEST_CLIENTS["anthropic"] = reserve
        self._voice_cfg(fallback_model="glm-test")
        resp = llm.chat("voice", system="будь", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(resp.text, "запасная жива")
        self.assertEqual(len(fake.calls), 1 + llm.NET_RETRIES)
        self.assertEqual(len(reserve.calls), 1, "запасная нога позвана ровно раз")

    def test_abandoned_ghost_failure_poisons_nothing(self):
        """Поздний сбой брошенного вызова — фон, не событие канала (ревью 05.10):
        ни last_error, ни здоровья «мозга», ни ретраев своего канала."""
        fake = AlwaysNetError()
        llm._TEST_CLIENTS["openai"] = fake
        self._voice_cfg()
        llm.abandon_call(threading.get_ident())
        with self.assertRaises(APIConnectionError):
            llm.chat("voice", system="будь", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(llm._STATE["voice"]["last_error"], "",
                         "призрак испортил last_error живой роли")
        self.assertEqual(len(fake.calls), 1,
                         "призрак гоняет транспортные ретраи — жжёт сеть, которую никто не заказывал")
        llm.settle_call(threading.get_ident())
        # Сняли пометку — сбой снова честное событие канала.
        with self.assertRaises(APIConnectionError):
            llm.chat("voice", system="будь", messages=[{"role": "user", "content": "привет"}])
        self.assertNotEqual(llm._STATE["voice"]["last_error"], "",
                            "живой сбой обязан попасть в состояние роли")


# ──────────────── проводка в agent: вызовы хода идут через сторож ───────────

class Wiring(unittest.TestCase):
    def test_voice_call_goes_through_the_watch(self):
        src = (TREE / "agent.py").read_text(encoding="utf-8")
        self.assertIn('response = _chat_cancellable(\n            "voice",',
                      src, "прямой llm.chat голоса вернулся — стоп снова ждёт сети")
        self.assertIn("model_watch.call_with_stop", src)
        self.assertIn("llm.abandon_call, on_settle=llm.settle_call",
                      src, "призрак перестал помечаться — его сбой отравит канал")
        # Стоп-проверка обязана читать МАНИФЕСТ, а не status(): тот пересобирает
        # весь леджер под локом прогона и спорит с самим путём остановки.
        # (status() в других местах хода легитимен — там он однократен и ему
        # нужен terminalizable; речь только о поллинге сторожа.)
        self.assertIn("manifest = _runs().manifest(current.run_id)", src)
        head = src.index("def _chat_cancellable")
        body = src[head:src.index("\ndef ", head)]
        self.assertNotIn("_runs().status", body,
                         "поллинг сторожа вернулся к тяжёлому status() — py-spy-улика прода")
        # Остановка — не сбой модели: model_failed врать не должен.
        self.assertIn("not isinstance(exc, RunStopped)", src,
                      "стоп владельца снова пишется в WAL как сбой модели")
        # Внутриходовые вызовы вне голосовой ноги — тоже под сторожем.
        self.assertIn('_chat_cancellable("evaluator", max_tokens=_GUARD_VERDICT_MAX_TOKENS',
                      src, "судья приватности снова пережидает стоп в своей сети")
        self.assertIn('_chat_cancellable("voice", system=_SCOUT_FRAME',
                      src, "разведчик снова пережидает стоп в своей сети")


if __name__ == "__main__":
    unittest.main()
