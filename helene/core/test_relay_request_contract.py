"""Шлём то, что адресат объявил; чего не шлём — сказано вслух.

ЧТО ЭТО ЛОВИТ. `llm._call_openai` собирает запрос, а принимает его `ChatRequest` в
`relay/src/core/models.rs`. Структура НЕ помечена `deny_unknown_fields`, поэтому serde
выбрасывает лишнее молча: ни ошибки, ни лога, ни красного теста.

Так и вышло. Ветка выбирала имя потолка по имени модели:

    _OPENAI_COMPLETION_TOKENS_RE = re.compile(r"^(o\\d|gpt-5)")

Все три её модели — `gpt-5.6-terra`, `-luna`, `-sol` — матчатся, значит клиент ВСЕГДА слал
`max_completion_tokens`, которого у реле нет. Потолок из `memory/llm.json` (voice 32768,
evaluator 10000) не доезжал никуда — **год**. Обнаружено только сверкой двух исходников
руками.

ЧТО ОХРАНЯЕТСЯ ЗДЕСЬ — три колонки, дословно по формулировке Егора:

    1. что шлём и что принимается  → пересечение обязано быть полным
    2. что шлём впустую            → список закрыт и назван поимённо, с причиной
    3. что он ждёт, а мы не шлём   → тоже назван, чтобы это было решением, а не забвением

⚠ ДВА СВИДЕТЕЛЯ, И ЭТО НАМЕРЕННО. Реле живёт в ДРУГОМ дереве (`/opt/relay/Code`), поэтому
читать только его — значит пропускать пин ровно там, где он нужен: на проде. Поэтому здесь
записан список полей с провенансом (откуда и когда снят), и он бьёт всегда. А когда
исходник реле дотягивается — отдельный тест сверяет запись с ним и краснеет на расхождении.
Запись без сверки протухает молча; сверка без записи не работает на половине машин.
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import llm  # noqa: E402

#: Где может лежать исходник реле. Он в отдельном дереве, и это нормально.
RELAY_SOURCES = (
    Path(os.environ.get("PRAXIS_RELAY_SRC") or "/opt/relay/Code/src/core/models.rs"),
    Path(__file__).resolve().parent / "relay" / "src" / "core" / "models.rs",
)

#: Поля `ChatRequest`, снятые с `relay/src/core/models.rs` 13.08.2026, реле `99cb8cc`.
#: Провенанс обязателен: без него это просто чьё-то мнение о чужом коде.
RELAY_DECLARED = frozenset({
    "model", "messages", "stream", "temperature",
    "max_tokens", "tools", "reasoning_effort", "prompt_cache_key",
})

#: Поля, которые клиент шлёт заведомо мимо реле, и почему они всё-таки шлются.
#: Список ЗАКРЫТЫЙ: новое имя здесь — это осознанное решение, а не случайность.
KNOWINGLY_UNACCEPTED = {
    "stream_options": (
        "нужен настоящему OpenAI, чтобы usage приехал в стриме; реле шлёт usage само "
        "из response.completed, поэтому здесь поле лишнее, но безвредное"
    ),
}

#: Поля, которые реле объявляет, а мы не шлём. Тоже закрытый список — чтобы «не шлём»
#: было записанным решением, а не тем, о чём никто не вспомнил.
DECLARED_BUT_UNUSED = {
    "temperature": "температуру не задаём нигде: её регистр — её дело, а не параметр",
}


def _relay_source() -> Path | None:
    return next((p for p in RELAY_SOURCES if p.is_file()), None)


def _declared_fields() -> set[str]:
    """Что реле объявляет — по записи с провенансом (см. RELAY_DECLARED)."""
    return set(RELAY_DECLARED)


def _sent_fields(model: str = "gpt-5.6-terra", *, everything: bool = False) -> set[str]:
    """Что клиент реально кладёт в запрос — перехватом на самом вызове SDK.

    `everything=True` — вызов, где срабатывают ВСЕ условные поля: с инструментами, с
    запрошенной глубиной размышления и с адресом кэша. Без него проба меряет минимальный
    запрос и «мы этого не шлём» получается неправдой о трёх полях из восьми.
    """
    seen: dict = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            raise _Stop()

    class _Stop(Exception):
        pass

    client = mock.Mock()
    client.chat.completions = _Completions()
    tools = [{"name": "ping", "description": "проба",
              "input_schema": {"type": "object", "properties": {}}}] if everything else None
    with mock.patch.object(llm, "cache_address",
                           return_value="praxis:test:-:-" if everything else ""):
        try:
            llm._call_openai(client, model, system="s",
                             messages=[{"role": "user", "content": "x"}],
                             tools=tools, max_tokens=1000,
                             thinking=4096 if everything else 0)
        except _Stop:
            pass
    fields = {k for k in seen if k != "extra_body"}
    fields |= set((seen.get("extra_body") or {}))
    return fields


class TheClientSpeaksTheRelaysLanguage(unittest.TestCase):
    def test_nothing_is_sent_into_the_void_without_a_named_reason(self):
        stray = _sent_fields(everything=True) - _declared_fields() - set(KNOWINGLY_UNACCEPTED)
        self.assertEqual(stray, set(),
                         "клиент шлёт поле, которого нет в ChatRequest реле, и это нигде "
                         "не названо: serde выбросит его молча")

    def test_the_ceiling_field_is_the_one_the_relay_declares(self):
        """Тот самый год тишины: слали `max_completion_tokens`, приняли бы `max_tokens`."""
        sent = _sent_fields()
        self.assertIn("max_tokens", sent)
        self.assertNotIn("max_completion_tokens", sent)

    def test_every_model_of_hers_goes_the_same_way(self):
        """Имя потолка больше не зависит от имени модели — адресат один и тот же."""
        for model in ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-4o", "llama-3"):
            with self.subTest(model=model):
                self.assertIn("max_tokens", _sent_fields(model))

    def test_conditional_fields_do_reach_the_relay_when_they_fire(self):
        """`tools`, `reasoning_effort`, `prompt_cache_key` шлются по условию — проверяем, что
        когда условие выполнено, они действительно уходят и уходят под правильными именами."""
        full = _sent_fields(everything=True)
        for name in ("tools", "reasoning_effort", "prompt_cache_key"):
            with self.subTest(field=name):
                self.assertIn(name, full)

    def test_what_the_relay_expects_and_we_never_send_is_written_down(self):
        skipped = _declared_fields() - _sent_fields(everything=True)
        self.assertEqual(skipped, set(DECLARED_BUT_UNUSED),
                         "реле объявляет поле, которого мы не шлём, и это нигде не "
                         "записано как решение")

    def test_the_reasons_are_actual_sentences_not_placeholders(self):
        for name, why in {**KNOWINGLY_UNACCEPTED, **DECLARED_BUT_UNUSED}.items():
            with self.subTest(field=name):
                self.assertGreater(len(why), 30, f"{name}: причина должна быть причиной")


class TheRecordStillMatchesTheRelay(unittest.TestCase):
    """Вторая половина двух свидетелей: запись сверяется с живым исходником, если он есть."""

    def test_the_written_down_field_list_is_still_true(self):
        source = _relay_source()
        if source is None:
            self.skipTest("исходника реле не видно — сверять не с чем, выдумывать нельзя")
        text = source.read_text(encoding="utf-8")
        start = text.index("pub struct ChatRequest {")
        body = text[start:text.index("}", start)]
        live = set(re.findall(r"^\s*pub (\w+):", body, re.M))
        self.assertEqual(live, set(RELAY_DECLARED),
                         f"ChatRequest в {source} разъехался с записью здесь — "
                         f"перечитать и обновить провенанс")


class TheCeilingIsStillFictionAndThatIsSaidOutLoud(unittest.TestCase):
    """⚠ Поле теперь доезжает до реле — и там НЕ ЧИТАЕТСЯ (`grep max_tokens` пуст).

    То есть предела длины ответа по-прежнему нет ни на одном участке пути. Это не
    исправлено этой правкой и не должно выглядеть исправленным: включение настоящего
    потолка — отдельное решение, у которого есть цена (десяток вспомогательных проходов
    впервые начнёт обрезаться, см. спеку `_docs/СПЕКА-КОНТРАКТ-РЕЛЕ-для-кодекса.md`).
    """

    def test_the_client_no_longer_guesses_the_addressee_by_model_name(self):
        source = Path(llm.__file__).read_text(encoding="utf-8")
        call = source[source.index("def _call_openai"):source.index("def _note_truncation")]
        self.assertNotIn("_OPENAI_COMPLETION_TOKENS_RE.match", call,
                         "выбор имени поля снова зависит от имени модели")


if __name__ == "__main__":
    unittest.main()
