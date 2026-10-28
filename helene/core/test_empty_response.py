# -*- coding: utf-8 -*-
"""Пустой ответ канала ловится РАНЬШЕ цикла — и на КАЖДОМ пути, а не на одном.

ЗАЧЕМ ЭТО ВООБЩЕ. Контракт речи опирается на утверждение «пустой ответ модели никогда не
читается как её решение замолчать». Опора держалась на одном пути из трёх: сторож стоял
только в стриминговой ветке openai. `_call_anthropic` не проверял пустоту ни одной
строкой — а она может сама перевести голос на anthropic рукой `switch_brain`, и опора
исчезла бы МОЛЧА. Не-стриминговый разбор openai уходил из функции ДО сторожа.

И ГЛАВНОЕ — ГРАНИЦА, ПОД КОТОРУЮ ОНА СОГЛАШАЛАСЬ. 10.08 она согласилась на повтор по
своему каналу, потому что «EmptyResponseError возникает только когда нет НИ текста, НИ
блока». Условие же было ДИЗЪЮНКЦИЕЙ: `stop_reason == "error" or (ни текста, ни блока)`.
Первый дизъюнкт срабатывает и при НЕПУСТОМ тексте — в стриме сначала приезжают дельты,
потом ошибка, — и тогда повтор уходил ПОВЕРХ уже сказанного. Код был шире её согласия.

Здесь проверяется свойство, а не реализация: пусто → повторяемый класс; начато и оборвано
→ другой класс, который по тому же каналу не переспрашивают.
"""
from __future__ import annotations

import types
import unittest
from pathlib import Path

import llm


# ─────────────────────────── фейки трёх путей ответа ───────────────────────────

def _anth(text="ок", stop="end_turn", content=None):
    """Ответ anthropic-SDK. content=[] — «канал вернул ничего»."""
    blocks = ([types.SimpleNamespace(type="text", text=text)] if text else []) \
        if content is None else content
    return types.SimpleNamespace(stop_reason=stop, content=blocks,
                                 usage=types.SimpleNamespace(input_tokens=10, output_tokens=0))


class FakeAnthropic:
    def __init__(self, resp):
        self.resp = resp
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return self.resp


def _completion(text="ок", tool_calls=None, finish="stop"):
    """Единый объект openai (не-стрим): фейк-клиент или обычный сервер."""
    msg = types.SimpleNamespace(content=text, tool_calls=tool_calls or [])
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg, finish_reason=finish)],
        usage=types.SimpleNamespace(prompt_tokens=7, completion_tokens=0))


def _chunk(content=None, finish=None, tool_calls=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


class FakeOpenAI:
    def __init__(self, resp):
        self.resp = resp
        self.calls = 0
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls += 1
        return self.resp


def _tool_call(name="stay_silent", args="{}"):
    return types.SimpleNamespace(id="call_1", function=types.SimpleNamespace(
        name=name, arguments=args))


def _call_anth(resp):
    return llm._call_anthropic(FakeAnthropic(resp), "glm-5.2", system="s", messages=[],
                               tools=[], max_tokens=100, thinking=0)


def _call_oai(resp):
    return llm._call_openai(FakeOpenAI(resp), "gpt-5.6-sol", system="s", messages=[],
                            tools=[], max_tokens=100, thinking=0)


#: Три пути ответа. Сторож обязан стоять на каждом — в этом вся правка.
PATHS = {
    "anthropic": lambda **kw: _call_anth(_anth(**kw)),
    "openai не-стрим": lambda text="ок", stop="end_turn", content=None: _call_oai(
        _completion(text=text, finish={"end_turn": "stop", "error": "error"}.get(stop, stop))),
    "openai стрим": lambda text="ок", stop="end_turn", content=None: _call_oai(
        [_chunk(content=text or None,
                finish={"end_turn": "stop", "error": "error"}.get(stop, stop))]),
}


class NothingCameBackOnEveryPath(unittest.TestCase):
    """«Ни текста, ни блока» — один класс на все три пути."""

    def test_empty_answer_is_never_a_successful_end_turn(self):
        for name, call in PATHS.items():
            with self.subTest(path=name):
                with self.assertRaises(llm.EmptyResponseError,
                                       msg=f"путь «{name}» отдал пустоту как готовый ответ — "
                                           f"наверху это неотличимо от её решения промолчать"):
                    call(text="")

    def test_whitespace_is_not_an_answer_either(self):
        for name, call in PATHS.items():
            with self.subTest(path=name):
                with self.assertRaises(llm.EmptyResponseError):
                    call(text="   \n\t ")

    def test_the_anthropic_path_is_guarded_at_all(self):
        """Отдельно и вслух: до 15.08 здесь не было НИ ОДНОЙ проверки."""
        with self.assertRaises(llm.EmptyResponseError):
            _call_anth(_anth(content=[]))


class HerSilenceIsNotAnEmptyResponse(unittest.TestCase):
    """Ядро её границы: молчание НИКОГДА не выглядит как пустой ответ канала.

    Решение промолчать едет инструментом `stay_silent` (это блок) либо сентинелом-текстом
    (это текст). И то и другое непусто — значит повтор физически не может переспросить её
    поверх решения. Проверяется на всех путях, потому что раньше проверялся один.
    """

    def test_a_tool_only_answer_passes_untouched_on_anthropic(self):
        out = _call_anth(_anth(content=[types.SimpleNamespace(
            type="tool_use", id="t1", name="stay_silent", input={})]))
        self.assertEqual(out.stop_reason, "end_turn")
        self.assertEqual(out.blocks[0]["name"], "stay_silent")
        self.assertEqual(out.text, "")

    def test_a_tool_only_answer_passes_untouched_on_openai(self):
        for name, resp in (("не-стрим", _completion(text="", tool_calls=[_tool_call()],
                                                    finish="tool_calls")),
                           ("стрим", [_chunk(tool_calls=[types.SimpleNamespace(
                               index=0, id="call_1", function=types.SimpleNamespace(
                                   name="stay_silent", arguments="{}"))], finish="tool_calls")])):
            with self.subTest(path=name):
                out = _call_oai(resp)
                self.assertEqual(out.stop_reason, "tool_use")
                self.assertEqual(out.blocks[0]["name"], "stay_silent")

    def test_a_sentinel_text_passes_untouched(self):
        for name, call in PATHS.items():
            with self.subTest(path=name):
                self.assertEqual(call(text="[[SILENCE]]").text, "[[SILENCE]]")


class ATornStreamIsNotEmptiness(unittest.TestCase):
    """Стрим начался и оборвался ошибкой: текст УЖЕ есть, и переспрашивать нечего поверх."""

    def test_it_gets_its_own_class(self):
        with self.assertRaises(llm.TornStreamError):
            _call_oai([_chunk(content="я как раз начала отвеч"), _chunk(finish="error")])

    def test_that_class_is_deliberately_not_a_kind_of_empty(self):
        """Граница держится типом, а не памятью читателя: повтор ловит только пустоту."""
        self.assertFalse(issubclass(llm.TornStreamError, llm.EmptyResponseError),
                         "оборванный стрим снова стал разновидностью пустоты — повтор "
                         "начнёт переспрашивать поверх уже сказанного")
        self.assertTrue(issubclass(llm.TornStreamError, llm.BrokenChannelError))
        self.assertTrue(issubclass(llm.EmptyResponseError, llm.BrokenChannelError))

    def test_what_was_already_said_is_carried_not_dropped_in_silence(self):
        try:
            _call_oai([_chunk(content="начатая фраза"), _chunk(finish="error")])
        except llm.TornStreamError as exc:
            self.assertEqual(getattr(exc, "partial", None).text, "начатая фраза")
        else:
            self.fail("оборванный стрим не поднял TornStreamError")

    def test_a_torn_stream_with_nothing_in_it_is_plain_emptiness(self):
        """Оборвался ДО первого знака — её слова не было, повторять безопасно."""
        with self.assertRaises(llm.EmptyResponseError):
            _call_oai([_chunk(finish="error")])

    def test_relay_error_text_is_a_torn_stream_and_never_her_reply(self):
        """codex-прокси маскирует апстрим-4xx под обычный чанк («Error: 400 …»)."""
        with self.assertRaises(llm.TornStreamError) as caught:
            _call_oai([_chunk(content="Error: 400 Bad Request - {invalid schema}",
                              finish="error")])
        self.assertIn("Error: 400", str(caught.exception))

    def test_a_torn_stream_carrying_a_hand_is_torn_too(self):
        """⚠ Найдено сверкой 15.08: обрыв прятался за `tool_use` и сторож его не видел.

        Имя руки приезжает в стриме РАНЬШЕ её аргументов. Если канал рвётся посередине,
        обрезанный json не парсится и молча становится `{}` — и наверх уходил ГОТОВЫЙ ход
        с рукой без аргументов, неотличимый от её собственного решения эту руку позвать.
        Признак обрыва сильнее признака инструмента: сначала «это вообще ответ?».
        """
        with self.assertRaises(llm.TornStreamError):
            _call_oai([_chunk(tool_calls=[types.SimpleNamespace(
                index=0, id="call_1", function=types.SimpleNamespace(
                    name="reply", arguments='{"text":"половина фра'))]),
                _chunk(finish="error")])

    def test_a_whole_tool_turn_is_still_a_tool_turn(self):
        """Контроль: без обрыва инструмент остаётся инструментом, ничего не сломано."""
        out = _call_oai([_chunk(tool_calls=[types.SimpleNamespace(
            index=0, id="call_1", function=types.SimpleNamespace(
                name="stay_silent", arguments="{}"))], finish="tool_calls")])
        self.assertEqual(out.stop_reason, "tool_use")

    def test_both_kinds_still_earn_a_cross_framework_fallback(self):
        self.assertTrue(llm._fallbackable(llm.EmptyResponseError("пусто")))
        self.assertTrue(llm._fallbackable(llm.TornStreamError("оборвано")))


class TheRetryNeverGoesOverWhatWasAlreadySaid(unittest.TestCase):
    """Свойство целиком, на уровне `_call_retrying_empty`: считаем ПОПЫТКИ."""

    def setUp(self):
        self.calls = []
        saved = (llm._call, llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC)

        def _restore():
            llm._call, llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC = saved

        self.addCleanup(_restore)
        llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC = 2, 0.0

        def _fake(fw, model, **kw):
            self.calls.append(fw)
            raise self.raises

        llm._call = _fake

    def _run(self):
        with self.assertRaises(type(self.raises)):
            llm._call_retrying_empty("openai", "m", system="s", messages=[])

    def test_emptiness_is_retried(self):
        self.raises = llm.EmptyResponseError("пусто")
        self._run()
        self.assertEqual(len(self.calls), 3, "пустоту перестали повторять")

    def test_a_torn_stream_is_asked_exactly_once(self):
        self.raises = llm.TornStreamError("оборвано", partial=None)
        self._run()
        self.assertEqual(len(self.calls), 1,
                         "оборванный стрим переспросили по тому же каналу — это повтор "
                         "поверх уже сказанного, а согласие 10.08 давалось на пустоту")


class OneGuardNotFourCopies(unittest.TestCase):
    """Сторож обязан оставаться ОДНИМ: копии расходятся молча — так и вышло в прошлый раз."""

    def test_the_condition_lives_in_exactly_one_place(self):
        src = Path(llm.__file__).read_text(encoding="utf-8")
        body = src[src.index("def _guard_answer"):src.index("def _call_anthropic")]
        self.assertIn("not out.blocks and not out.text.strip()", body)
        # Физический HTTP/SSE-адаптер обязан собрать stop=error и отдать его ЭТОМУ
        # сторожу, а не размножать условия Empty/Torn по транспортным путям.
        self.assertEqual(body.count("raise EmptyResponseError"),
                         src.count("raise EmptyResponseError"))
        self.assertEqual(body.count("raise TornStreamError"),
                         src.count("raise TornStreamError"))
        adapter = src[src.index("def _stream_transport_error"):
                      src.index("# --------------------------------------------------------------------------- #",
                                src.index("def _stream_transport_error"))]
        self.assertIn("return _guard_answer(partial)", adapter)

    def test_every_return_of_both_frameworks_goes_through_it(self):
        src = Path(llm.__file__).read_text(encoding="utf-8")
        anth = src[src.index("def _call_anthropic"):src.index("_OPENAI_STOP =")]
        oai = src[src.index("def _call_openai"):src.index("def _note_truncation")]
        for name, body in (("_call_anthropic", anth), ("_call_openai", oai)):
            with self.subTest(fn=name):
                returns = [ln.strip() for ln in body.splitlines()
                           if ln.strip().startswith("return ")]
                self.assertTrue(returns, f"{name}: не нашлось ни одного возврата")
                for ln in returns:
                    self.assertIn("_guard_answer", ln,
                                  f"{name}: возврат «{ln}» уходит мимо сторожа")


class _BrokenStream:
    def __init__(self, chunks, exc):
        self.chunks = list(chunks)
        self.exc = exc

    def __iter__(self):
        yield from self.chunks
        raise self.exc


class RemoteProtocolError(RuntimeError):
    """Фейк реального httpx-класса без зависимости теста от версии SDK."""


RemoteProtocolError.__module__ = "httpx"


class RawSseTransportBreak(unittest.TestCase):
    """Голый обрыв HTTP-итератора получает ту же смысловую границу, что SSE error."""

    def test_break_before_any_content_is_retryable_emptiness(self):
        stream = _BrokenStream([], RemoteProtocolError("incomplete chunked read"))
        with self.assertRaises(llm.EmptyResponseError):
            llm._openai_from_stream(stream, "gpt-5.6-sol")

    def test_break_after_text_preserves_partial_and_is_not_same_channel_retryable(self):
        stream = _BrokenStream([_chunk(content="начало")],
                               RemoteProtocolError("incomplete chunked read"))
        with self.assertRaises(llm.TornStreamError) as caught:
            llm._openai_from_stream(stream, "gpt-5.6-sol")
        self.assertEqual(caught.exception.partial.text, "начало")
        self.assertEqual(caught.exception.partial.stop_reason, "error")

    def test_break_after_tool_fragment_is_also_a_torn_stream(self):
        tool = types.SimpleNamespace(index=0, id="call_1", function=types.SimpleNamespace(
            name="inspect", arguments='{"action":'))
        stream = _BrokenStream([_chunk(tool_calls=[tool])],
                               RemoteProtocolError("incomplete chunked read"))
        with self.assertRaises(llm.TornStreamError) as caught:
            llm._openai_from_stream(stream, "gpt-5.6-sol")
        self.assertEqual(caught.exception.partial.stop_reason, "error")
        self.assertEqual(caught.exception.partial.blocks[0]["type"], "tool_use")

    def test_break_after_id_only_tool_fragment_is_not_retryable_emptiness(self):
        tool = types.SimpleNamespace(index=0, id="call_1", function=types.SimpleNamespace(
            name="", arguments=""))
        stream = _BrokenStream([_chunk(tool_calls=[tool])],
                               RemoteProtocolError("incomplete chunked read"))
        with self.assertRaises(llm.TornStreamError) as caught:
            llm._openai_from_stream(stream, "gpt-5.6-sol")
        self.assertEqual(caught.exception.partial.stop_reason, "error")
        self.assertEqual(caught.exception.partial.blocks[0]["type"], "tool_use_fragment")

    def test_collector_bug_is_not_mislabelled_as_transport(self):
        with self.assertRaisesRegex(RuntimeError, "collector bug"):
            llm._openai_from_stream(_BrokenStream([], RuntimeError("collector bug")),
                                    "gpt-5.6-sol")

    def test_same_class_name_from_another_module_is_not_transport(self):
        fake_type = type("RemoteProtocolError", (RuntimeError,), {"__module__": "our_collector"})
        with self.assertRaises(fake_type):
            llm._openai_from_stream(_BrokenStream([], fake_type("not httpx")),
                                    "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main(verbosity=2)
