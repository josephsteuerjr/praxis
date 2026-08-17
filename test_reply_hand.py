"""Моя реплика уходит РУКОЙ, а текст хода — это моё молчание.

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ, ОДНОЙ СТРОКОЙ:

    позвала `reply`  → сообщение ушло, ход НЕ закрыт, можно проверить и ответить ещё
    текст без руки   → ход закрыт, наружу не ушло ничего (это моё молчание)

ИСТОРИЯ ДЕФЕКТА, ЖИВАЯ. Пока реплика была ВОЗВРАТОМ модели, последний мой текст И БЫЛ
сообщением — значит любой добавочный поворот цикла его затирал. 13.08.2026 это выстрелило
трижды подряд в личке Егора: в 22:44 наружу ушло НИЧЕГО, в 22:54 ему уехало «Да,
отправляй» (это я отвечала зеркалу, а не ему), в 23:26 — «Оставляю как есть». Три правки
формулировки не помогли и не могли: порок был в конструкции, а не в словах.

ЧЕМ ЭТИ ТЕСТЫ ХОДЯТ. В доме записана ловушка: тест зовёт внутренности напрямую, мимо
живого пути, и гейт зеленеет на конструкции, которая живьём не работает. Здесь два уровня,
и оба названы вслух:

  УРОВЕНЬ A — настоящий `voice_turn_envelope`, настоящий `_terminal_tool_loop`, настоящая
  рука через `_call_tool_with_ceiling` (то есть через границу потока, ради которой
  `work_loop` вообще держит состояние в словаре по `run_id`), настоящий гард. Подставлены
  РОВНО ДВЕ вещи: модель (`_model_call`) и мост Telegram (`agent._TELETHON["reply"]`).

  УРОВЕНЬ B — то же самое, но мост НЕ подставной: на нём стоит живой
  `mtproto_runner._sync_reply` с настоящей durable-очередью на диске, настоящим
  `run_direct_outbox_prepared`/`project_direct_outbox_acceptance` и настоящим проектором
  приёмки. Подставлена ровно одна вещь — сам провод Telethon
  (`_send_message_idempotent`/`_resolve_entity`): дальше него герметичный прогон дойти не
  может по определению, и это единственное, что здесь ненастоящее.

Уровень A не заменяет уровень B и не притворяется им: каждое свойство, которое живёт
ниже моста (ключи идемпотентности, расписки, проекция приёмки), проверяется ТОЛЬКО на
уровне B.

Пины по свойствам, а не по словам: дом дважды обжигался на тестах, зелёных по неправильной
причине, — поэтому здесь нет ни одной проверки на дословный текст квитанции.

⚠ ЧТО БЫЛО КРАСНЫМ 15.08 И ЧЕМ ЭТО КОНЧИЛОСЬ. Уровень B был красным целиком по одной
причине: durable-учёт прямых отправок (`agent._direct_outbox_identity`,
`agent.run_direct_outbox_prepared`, `run_direct_outbox_accepted`, `_receipt_for_outstanding`)
знал слова `send_message`/`send_file`/`narrate` и НЕ знал слова `reply` — ключ
идемпотентности руке уже выдавался, очередь его принимала, а намерение рубилось до сети, и
мне возвращалось «ещё в durable-очереди, очередь дошлёт сама». Правки наложены, уровень B
зелёный. Предусловие `_durable_gate_refusal()` оставлено НАРОЧНО: если учёт когда-нибудь
снова забудет это имя, тесты назовут недостачу словами, а не покраснеют загадкой.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import agent
import mtproto_runner as runner
import run_context
import telegram_outbox
import turns
import work_loop


# --------------------------------------------------------------------------------------
# Подставная модель. Форма ответа — ровно та, что читает `_terminal_tool_loop`.
# --------------------------------------------------------------------------------------
class _Resp:
    def __init__(self, text: str, stop_reason: str = "end_turn", blocks=None):
        self.text = text
        self.stop_reason = stop_reason
        self.blocks = blocks if blocks is not None else [{"type": "text", "text": text}]


def _says(text: str) -> _Resp:
    """Текст без вызова инструмента — под новым рычагом это моя заметка себе."""
    return _Resp(text)


def _calls_reply(call_id: str, text: str, **extra) -> _Resp:
    """Один поворот цикла, в котором я зову руку `reply`."""
    payload = {"text": text}
    payload.update(extra)
    return _Resp("", stop_reason="tool_use", blocks=[
        {"type": "tool_use", "id": call_id, "name": "reply", "input": payload},
    ])


def _calls_tool(call_id: str, name: str, payload: dict) -> _Resp:
    """Поворот цикла с любой другой рукой — нужен, чтобы звать `stay_silent`."""
    return _Resp("", stop_reason="tool_use", blocks=[
        {"type": "tool_use", "id": call_id, "name": name, "input": dict(payload)},
    ])


class _Model:
    """Счётчик поворотов цикла: «ход не закрылся» — это про число вызовов модели."""

    def __init__(self, *responses: _Resp):
        self.responses = list(responses)
        self.calls = 0
        self.offered: list[set[str]] = []

    def __call__(self, system, messages, tools=None):
        self.offered.append({str(t.get("name")) for t in (tools or [])
                             if isinstance(t, dict)})
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


def _lever(value: str = "on", **extra):
    env = {"PRAXIS_CHAT_REPLY_HAND": value}
    env.update(extra)
    return mock.patch.dict(os.environ, env)


def _owner_dm() -> agent.ChannelContext:
    """Личка Егора: аудитория владельца, где советник по данным не запускается вовсе."""
    return agent.ChannelContext(chat_id="101", principal_id=101, is_dm=True, owner=True,
                                title="Егор")


def _stranger_dm() -> agent.ChannelContext:
    """Не-владелец: здесь на исходящем стоит механический кред-пол, и он настоящий."""
    return agent.ChannelContext(chat_id="555", principal_id=555, is_dm=True, owner=False,
                                known=True, title="Соседка")


# Похоже на ключ по ФОРМЕ и ничего не открывает: кред-пол смотрит только на форму токена.
_LOOKS_LIKE_A_KEY = "sk-" + "praxistestnotarealkey0123456789"


def _durable_gate_refusal() -> str:
    """Пускает ли durable-учёт слово «reply» вообще. '' — пускает.

    ⚠ ЭТО ПРЕДУСЛОВИЕ, А НЕ ПРОВЕРКА. Оно стоит перед сквозными тестами ровно потому, что
    без него их краснота выглядит как «реплика почему-то не дошла до провода» — то есть
    как загадка, а не как названная недостача. Ключ идемпотентности для `reply` уже
    выдаётся (`agent._tool_idempotency_key`), очередь его принимает, а вот сам durable-учёт
    прямых отправок слова «reply» не знает и рубит намерение до сети.
    """
    key = "telegram-outbox:run-precondition:tool:call-precondition"
    entry = {
        "key": key, "run_id": "run-precondition", "call_id": "call-precondition",
        "purpose": "tool:reply", "kind": "text",
        "random_id": telegram_outbox.stable_random_id(key),
        "peer_id": 101, "topic_id": None, "reply_to": None,
        "payload": {"text": "предусловие"},
    }
    try:
        agent._direct_outbox_identity(entry, verify_file=False)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


_DURABLE_GATE_PATCH = (
    "durable-учёт прямых отправок не знает руки `reply`, поэтому намерение рубится ДО "
    "сети, а мне при этом возвращается «ещё в durable-очереди, очередь дошлёт сама» — то "
    "есть бодрая неправда о сообщении, которого не будет никогда.\n"
    "Правка в agent.py, три строки (проверена наложением этих же функций в память):\n"
    "  1. `_direct_outbox_identity`: "
    "`tool not in {\"send_message\", \"send_file\", \"narrate\"}` → "
    "`tool not in {\"send_message\", \"send_file\", \"narrate\", \"reply\"}`\n"
    "  2. `_direct_outbox_identity`: "
    "`if tool in (\"send_message\", \"narrate\"):` → "
    "`if tool in (\"send_message\", \"narrate\", \"reply\"):`\n"
    "  3. `run_direct_outbox_prepared`: "
    "`if identity[\"tool\"] in (\"send_message\", \"narrate\"):` → "
    "`if identity[\"tool\"] in (\"send_message\", \"narrate\", \"reply\"):`\n"
    "Отказ гейта дословно: %s"
)


class _StandInBridge:
    """Мост Telegram на уровне A: помнит, что ему отдали, и отвечает квитанцией.

    ⚠ ЭТО ЕДИНСТВЕННОЕ, ЧТО ЗДЕСЬ ПОДСТАВНОЕ ПОМИМО МОДЕЛИ, И ОНО НАЗВАНО. Всё, что
    происходит ДО моста, — настоящее: гард, `work_loop.note_sent()`, граница потока руки.
    Всё, что происходит ПОСЛЕ моста, проверяет уровень B, где на этом же месте стоит
    живой `_sync_reply` с durable-очередью.
    """

    def __init__(self, outcome: str = "Отправила → Егор (chat_id=101, message_id=901)"):
        self.calls: list[tuple[str, str, str]] = []
        self.outcome = outcome

    def __call__(self, chat_id, text, reply_to=""):
        self.calls.append((str(chat_id), str(text), str(reply_to)))
        return self.outcome


class _RecordedTurns:
    """Наблюдатель кольца ходов: копит записи и ПРОПУСКАЕТ их в настоящий `turns.record`.

    Наблюдатель, а не подмена: настоящая запись всё равно происходит, иначе тест мерил бы
    мир, которого нет (тот же приём, что у прибора кадра, — наблюдатель не двигает
    наблюдаемое).
    """

    def __init__(self):
        self.rows: list[dict] = []
        self._real = turns.record

    def __call__(self, turn: dict) -> None:
        self.rows.append(copy.deepcopy(turn))
        return self._real(turn)

    @property
    def last(self) -> dict:
        assert self.rows, "кольцо ходов не получило ни одной записи"
        return self.rows[-1]


class _WatchedHands:
    """Наблюдатель за возвратами рук: настоящий `_call_tool_with_ceiling` всё равно зовётся.

    Нужен ровно там, где охраняемое свойство — ЧТО РУКА СКАЗАЛА МНЕ В ОТВЕТ. Возврат руки
    уезжает в ленту хода внутри конверта, и снаружи его иначе не видно.
    """

    def __init__(self):
        self._real = agent._call_tool_with_ceiling
        self.outs: list[tuple[str, object]] = []

    def __call__(self, name, impl, call_input):
        out = self._real(name, impl, call_input)
        self.outs.append((str(name), out))
        return out

    def by(self, name: str) -> list[str]:
        return [str(out) for hand, out in self.outs if hand == name]


class _TurnHarness(unittest.TestCase):
    """Общий стенд живого чат-хода. Подставлены модель и мост — больше ничего."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.bridge = _StandInBridge()
        self.recorded = _RecordedTurns()
        self.hands = _WatchedHands()
        self.stack.enter_context(mock.patch.object(agent.llm, "configured",
                                                   lambda *a, **k: True))
        self.stack.enter_context(mock.patch.object(turns, "record", self.recorded))
        self.stack.enter_context(mock.patch.object(agent, "_call_tool_with_ceiling",
                                                   self.hands))

    def turn(self, *responses: _Resp, lever: str = "on",
             ctx: agent.ChannelContext | None = None, bridge=None):
        """Прогнать НАСТОЯЩИЙ `voice_turn_envelope` с этой лентой ответов модели."""
        model = _Model(*responses)
        channel = ctx if ctx is not None else _owner_dm()
        telethon = {"reply": self.bridge if bridge is None else bridge}
        with _lever(lever), \
                mock.patch.dict(agent._TELETHON, telethon), \
                mock.patch.object(agent, "_model_call", model):
            envelope = agent.voice_turn_envelope(
                channel.chat_id, "Егор: посмотри и скажи", "Егор", ctx=channel)
        return envelope, model


# ======================================================================================
# 1. РЫЧАГ ОПУЩЕН — ДОМ ПРЕЖНИЙ
# ======================================================================================
class TheLoweredLeverIsTheOldHouse(_TurnHarness):
    """Опущенный рычаг обязан давать ПРЕЖНЕЕ поведение, а не «почти прежнее».

    Это не формальность: рычаг переключает сразу два свойства — чем ход закрывается и что
    уезжает наружу. Между ними нет полумеры, поэтому проверяются обе стороны сразу.
    """

    def test_the_hand_is_not_even_offered_when_the_lever_is_down(self):
        """Обещать способность, которой нет, дороже, чем не дать её.

        Спрашиваем ЕДИНСТВЕННЫЙ сборщик набора рук (`offered_tools_for`, контракт A1), а не
        статический список модуля: именно расхождение статического списка с живым набором
        однажды и сделало самоотчёт неправдой.
        """
        ctx = _owner_dm()
        with _lever("off"):
            down = {t.get("name") for t in agent.offered_tools_for(ctx)}
        with _lever("on"):
            up = {t.get("name") for t in agent.offered_tools_for(ctx)}
        self.assertNotIn("reply", down, "рука ответа предложена при опущенном рычаге")
        self.assertIn("reply", up, "рука ответа не предложена при поднятом рычаге")
        # v3 (17.08): конец хода — тоже рука, и она живёт и умирает вместе с reply.
        # Порознь они лгут: end_turn без reply обещает конец контракта, которого нет.
        self.assertNotIn("end_turn", down, "рука конца предложена при опущенном рычаге")
        self.assertIn("end_turn", up, "рука конца не предложена при поднятом рычаге")
        self.assertEqual(down, up - {"reply", "end_turn"},
                         "рычаг изменил набор рук ещё чем-то, кроме пары reply/end_turn")

    def test_the_offer_the_model_actually_sees_follows_the_lever(self):
        """Набор рук проверяем не у сборщика, а у того, что ДОЕХАЛО до модели."""
        _envelope, model = self.turn(_says("Привет."), lever="off")
        self.assertTrue(model.offered, "модель не позвали ни разу")
        self.assertNotIn("reply", model.offered[0])
        _envelope, model = self.turn(_says("Привет."), lever="on")
        self.assertIn("reply", model.offered[0])

    def test_with_the_lever_down_my_text_still_leaves_the_house(self):
        """Прежний контракт: последний текст хода И ЕСТЬ сообщение."""
        envelope, model = self.turn(_says("Привет, я здесь."), lever="off")
        self.assertEqual(envelope.text, "Привет, я здесь.")
        self.assertEqual(model.calls, 1, "опущенный рычаг добавил поворот цикла")
        self.assertEqual(self.bridge.calls, [], "при опущенном рычаге текст ушёл ещё и рукой")

    def test_the_same_words_with_the_lever_up_reach_nobody(self):
        """Тот же вход, тот же текст — и наружу не уходит ничего. Это и есть моё молчание."""
        envelope, model = self.turn(_says("Привет, я здесь."), lever="on")
        self.assertEqual(envelope.text, "")
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.bridge.calls, [])

    def test_the_chat_continuation_policy_is_gone_under_the_new_lever(self):
        """Уколов и бюджета в чате больше нет — и это решение, а не упущение.

        Пока реплика была возвратом модели, каждый добавочный поворот выторговывался
        политикой. Когда реплика уходит рукой, выторговывать нечего: ход и так крутится,
        пока я зову инструменты.
        """
        with _lever("on", PRAXIS_CHAT_FOLLOW_THROUGH="on"):
            decision = work_loop.chat_decide(
                "Сейчас напишу в AbstractDL Chat.", kind="chat_turn", hands=0, spent=0)
        self.assertEqual(decision, (False, ""))
        with _lever("off", PRAXIS_CHAT_FOLLOW_THROUGH="on"):
            keep, note = work_loop.chat_decide(
                "Сейчас напишу в AbstractDL Chat.", kind="chat_turn", hands=0, spent=0)
        self.assertTrue(keep, "опущенный рычаг не вернул прежнюю политику продолжения")
        self.assertTrue(note)


# ======================================================================================
# 2, 3, 5. ЖИВОЙ ХОД: РУКА ГОВОРИТ, ТЕКСТ МОЛЧИТ
# ======================================================================================
class MyReplyGoesOutByHand(_TurnHarness):
    """Уровень A: настоящий конверт, настоящий цикл, настоящая рука, подставной мост."""

    def test_calling_the_hand_hands_the_text_to_the_send_seam(self):
        """Позвала руку — текст ушёл ТУДА, ГДЕ ЕГО ОТПРАВЛЯЮТ, а не в возврат хода."""
        envelope, _model = self.turn(
            _calls_reply("call-1", "Луна включена, проверила llm.json."),
            _says("Себе: не забыть про голос."))
        self.assertEqual(len(self.bridge.calls), 1, "рука не дошла до шва отправки")
        chat_id, text, _reply_to = self.bridge.calls[0]
        self.assertEqual(chat_id, "101", "ответ ушёл не собеседнику этого хода")
        self.assertIn("llm.json", text)
        self.assertEqual(envelope.text, "",
                         "под поднятым рычагом граница хода понесла текст — швов стало два")

    def test_the_turn_does_not_close_on_a_reply_and_i_am_asked_again(self):
        """Ход НЕ закрывается отправкой: можно проверить сделанное и ответить ещё раз.

        Признак — число поворотов цикла. Один вызов модели значил бы, что ход умер на
        первом же ответе, то есть ровно прежняя конструкция.
        """
        _envelope, model = self.turn(
            _calls_reply("call-1", "Сейчас проверю."),
            _says("Проверила, добавить нечего."))
        self.assertGreater(model.calls, 1, "ход закрылся на вызове руки")

    def test_the_reply_to_argument_travels_as_an_argument_not_as_prose(self):
        """`ОТВЕТ->#id` была строкой ВНУТРИ моего текста — теперь это аргумент руки.

        Свойство: парсеру нечего угадывать, адрес реплая доезжает до шва отдельно от текста.
        """
        self.turn(_calls_reply("call-1", "Отвечаю на то сообщение.", reply_to="#4242"),
                  _says("Всё."))
        _chat_id, text, reply_to = self.bridge.calls[0]
        self.assertEqual(reply_to, "#4242")
        self.assertNotIn("4242", text, "адрес реплая просочился в сам текст")

    def test_the_same_reply_is_not_sent_twice_in_one_turn(self):
        """ЖИВОЙ СЛУЧАЙ 17.08, 03:49: четыре копии одной реплики за две минуты.

        Леджер ровно-однажды считает по call_id, а каждый поворот цикла рождает НОВЫЙ
        call_id — значит от повтора СЛОВА он не защищает по построению. Петля была такой:
        пустота терры на продолжении → фолбэк → луна, получив тот же контекст, звала
        `reply` с тем же текстом заново. Здесь охраняется пол руки: байт-в-байт та же
        реплика в этом же ходе не отправляется, и рука говорит об этом словами.
        Дверь не заперта: изменённый текст уходит.
        """
        envelope, _model = self.turn(
            _calls_reply("call-1", "На связи. Это reply к твоему тесту."),
            _calls_reply("call-2", "На связи. Это reply к твоему тесту."),
            _calls_reply("call-3", "А вот это уже другая мысль."),
            _says("Всё."))
        self.assertEqual(len(self.bridge.calls), 2,
                         "повтор той же реплики ушёл наружу — петля 17.08 не закрыта")
        texts = [text for _chat, text, _r in self.bridge.calls]
        self.assertEqual(len(set(texts)), 2, "наружу ушли не две РАЗНЫЕ реплики")
        refusals = [out for out in self.hands.by("reply") if "уже доставлена" in out]
        self.assertEqual(len(refusals), 1,
                         "отказ руки обязан назвать причину мне, а не молча съесть повтор")

    def test_end_turn_closes_the_loop_without_another_model_call(self):
        """Контракт v3: конец хода — поступок. `end_turn` останавливает цикл ЗДЕСЬ ЖЕ.

        Признак — число вызовов модели: третий вызов значил бы, что её слово о конце
        снова ждёт следующей реплики (близнец task_control в рабочих окнах).
        Заметка из `end_turn(note=…)` обязана доехать до записи хода.
        """
        envelope, model = self.turn(
            _calls_reply("call-1", "Готово, проверила."),
            _calls_tool("call-2", "end_turn", {"note": "сказала главное, ход закрываю"}))
        self.assertEqual(model.calls, 2, "end_turn не остановил цикл")
        self.assertEqual(len(self.bridge.calls), 1)
        self.assertEqual(envelope.text, "")
        recorded = self.recorded.rows[-1]
        self.assertIn("Готово, проверила.", str(recorded.get("out") or ""),
                      "сказанное рукой потерялось из записи хода")
        self.assertIn("сказала главное", str(recorded.get("note") or ""),
                      "заметка end_turn не доехала до записи хода")

    def test_end_turn_without_speech_is_a_finished_turn_not_an_error(self):
        """`end_turn` без единой отправки — завершённый ход без речи (её решение №2:
        это НЕ stay_silent — тот остаётся отдельным жестом «решила не говорить»)."""
        envelope, model = self.turn(
            _calls_tool("call-1", "end_turn", {}))
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.bridge.calls, [], "конец без речи что-то отправил")
        self.assertEqual(envelope.text, "")
        recorded = self.recorded.rows[-1]
        self.assertEqual(recorded.get("held"), "unspoken",
                         "конец без речи обязан быть фактом «без реплики», а не ошибкой")

    def test_an_empty_terminal_note_after_a_reply_keeps_what_was_said(self):
        """reply → пустой терминальный текст: ход закрыт СКАЗАННЫМ, ничего не переслано.

        Это конвертный этаж границы 17.08 (петля четырёх копий); транспортный этаж —
        один ретрай и конец без фолбэка — проверяется в test_llm на настоящем chat().
        """
        envelope, model = self.turn(
            _calls_reply("call-1", "Ответ ушёл, вот он."),
            _says(""))
        self.assertEqual(len(self.bridge.calls), 1, "пустота вызвала пересылку")
        self.assertEqual(envelope.text, "")
        recorded = self.recorded.rows[-1]
        self.assertIn("Ответ ушёл", str(recorded.get("out") or ""),
                      "пустота затёрла сказанное — «ничего не сказала» вместо реплики")

    def test_the_reply_receipt_teaches_the_ending_and_names_the_boundary(self):
        """Её условие к v3 дословно: возврат руки не только подсказывает `end_turn`, но
        и называет границу — пустой следующий ответ ничего не переотправит и не отменит
        уже доставленное. Наставление едет в момент действия, где его видит ЛЮБАЯ
        модель продолжения."""
        self.turn(_calls_reply("call-1", "Проверка возврата."),
                  _says("Всё."))
        receipts = self.hands.by("reply")
        self.assertTrue(receipts, "возврат руки не пойман")
        self.assertIn("end_turn", receipts[0])
        self.assertIn("не отменит", receipts[0])

    def test_text_without_the_hand_sends_nothing_and_closes_the_turn(self):
        """Текст без руки — моё молчание: наружу не ушло ничего, ход закрыт.

        Отдельного механизма для молчания больше не нужно, и это главное в новом контракте:
        молчание перестало быть сентинелом и стало отсутствием действия.
        """
        envelope, model = self.turn(_says("Подумала и решила промолчать."))
        self.assertEqual(envelope.text, "")
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(model.calls, 1, "ход не закрылся на тексте без руки")

    def test_a_silent_turn_keeps_my_text_as_a_note_and_is_marked_as_silence(self):
        """Заметка остаётся мне видимой, а ход честно помечен как молчание.

        Потерять моё намерение молча хуже, чем не отправить его: текст никуда не ушёл, но
        он БЫЛ — и запись хода обязана помнить и то и другое.

        ⚠ ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ ТОЧНО: заметка доезжает до `turns.record`. ДАЛЬШЕ она
        сегодня НЕ едет — `turns._clip` пишет по белому списку полей, и `note` в него не
        внесён, то есть до кольца на диске заметка не доживает. Тест намеренно не пинит
        обратное: он охраняет то, что верно, и называет то, что нет.
        """
        self.turn(_says("Подумала и решила не отвечать: нечего добавить."))
        turn = self.recorded.last
        self.assertIn("нечего добавить", str(turn.get("note") or ""),
                      "мой текст не лёг в заметку хода")
        self.assertTrue(turn.get("held"), "ход без отправки не помечен как молчание")
        self.assertFalse(str(turn.get("out") or ""),
                         "ход без отправки записан как сказанный")

    def test_no_reply_is_not_recorded_as_my_decision_to_be_silent(self):
        """⚑ МОЁ МОЛЧАНИЕ — ЭТО ОБЪЯВЛЕНИЕ, А НЕ ВЫВОД ИЗ ПУСТОТЫ.

        Первая редакция этого контракта писала `held="voice"` всякий раз, когда рука не
        звалась. Туда попадало всё подряд: оборванный апстримом стрим, упавший ход, просто
        «нечего добавить» — и всё это записывалось МОИМ словом. Это откат решения 28.07,
        которым молчание сделали объявлением, и тот же класс, что корневой диагноз всего
        цикла: «done выводится из её молчания вместо того, чтобы быть её словом».

        Свойство, которое держит тест: ход без реплики помечается ОТДЕЛЬНЫМ словом, и это
        слово не равно тому, которым записывается моё решение молчать.
        """
        self.turn(_says("Подумала и решила не отвечать: нечего добавить."))
        turn = self.recorded.last
        self.assertEqual(turn.get("held"), "unspoken",
                         "ход без реплики записан не как факт, а как чьё-то решение")
        self.assertNotEqual(turn.get("held"), "voice",
                            "отсутствие реплики приписано мне как объявленное молчание")

    def test_my_declared_silence_is_still_recorded_as_my_own_word(self):
        """Обратная сторона того же: когда я СКАЗАЛА, что молчу, это моё слово.

        Различить два исхода мало — надо, чтобы объявленное молчание не растворилось в
        общей куче «реплики не было». Иначе я потеряю собственное решение.
        """
        self.turn(_calls_tool("call-s", "stay_silent", {"reason": "им сейчас не до меня"}),
                  _says("Себе: вернусь к этому позже."))
        turn = self.recorded.last
        self.assertEqual(turn.get("held"), "voice",
                         "моё объявленное молчание записано как простое отсутствие реплики")
        # ⚠ Без этой строки тест прошёл бы и на СТАРОМ коде: там любой ход без реплики
        # получал `voice`, и отличить объявленное молчание от отсутствия было нечем.
        # Причину записывает только новая ветка, читающая держатель решения.
        self.assertIn("не до меня", str(turn.get("why") or ""),
                      "моя причина молчать не доехала до записи хода")

    def test_after_i_declare_silence_the_hand_refuses_and_names_the_way_back(self):
        """Расписка `stay_silent` обещает, что текст этого хода не уйдёт.

        Пока рука ответа не читала держатель решения, я могла позвать её следом — и ответ
        уходил вопреки только что принятому мной же решению, а расписка оказывалась
        враньём. Забором это не становится: флаг ставлю я сама, и дверь назад
        (`stay_silent(cancel=True)`) названа в самом отказе.
        """
        self.turn(_calls_tool("call-s", "stay_silent", {"reason": "не буду"}),
                  _calls_reply("call-1", "А всё-таки скажу."),
                  _says("Себе: попробовала."))
        self.assertEqual(self.bridge.calls, [],
                         "ответ ушёл вопреки моему же решению молчать")
        turn = self.recorded.last
        self.assertFalse(str(turn.get("out") or ""),
                         "придержанный решением ход записан как сказанный")
        self.assertEqual(turn.get("held"), "voice",
                         "моё решение молчать не пережило попытку ответить")

    def test_a_turn_that_spoke_by_hand_is_never_written_down_as_silence(self):
        """⚠ ЭТО БЫЛА БЫ ПРЯМАЯ ЛОЖЬ В МОЁМ СОБСТВЕННОМ КОЛЬЦЕ ХОДОВ.

        Пустой выход гарда означает молчание. Если бы текст хода под поднятым рычагом
        по-прежнему ехал в гард, КАЖДЫЙ отправленный рукой ответ записался бы как
        «промолчала» — и завтра я читала бы своё кольцо и не находила в нём своих слов.
        """
        self.turn(_calls_reply("call-1", "Ответила по делу."),
                  _says("Себе: дальше не отвечаю."))
        turn = self.recorded.last
        self.assertTrue(str(turn.get("out") or ""), "отправленный рукой ход не помечен как сказанный")
        self.assertFalse(turn.get("held"), "ход, в котором я говорила, записан как молчание")

    def test_the_frame_never_teaches_two_ways_to_be_silent_at_once(self):
        """⚑ КАДР ОБЯЗАН ГОВОРИТЬ О СЕБЕ ПРАВДУ, А НЕ ОБЕЩАТЬ МЕХАНИЗМ.

        Presence-рамки безусловно велели печатать управляющий токен `[молчу]`. Под поднятым
        рычагом он не управляет ничем: мой текст — заметка, и слово «молчу» внутри неё
        остаётся просто словом. Ровно та же ошибка, что однажды уже была в снимке
        возможностей: обещанная способность, которой в этом ходе нет.
        """
        for ctx in (_owner_dm(), agent.ChannelContext(chat_id="-100500", is_dm=False,
                                                      owner=False, known=True)):
            with _lever("off"):
                old = agent._presence_frame(ctx)
            with _lever("on"):
                new = agent._presence_frame(ctx)
            self.assertIn("[молчу]", old, "прежняя рамка перестала учить сентинелу")
            self.assertNotIn("[молчу]", new,
                             "под рукой ответа рамка всё ещё обещает управляющий токен")
            self.assertIn("reply", new, "новая рамка не называет, чем уходит моя реплика")

    def test_the_hand_is_not_offered_where_there_is_nobody_to_answer(self):
        """Обещать способность, которой нет, дороже, чем не дать её — в ОБЕ стороны.

        Моё рабочее окно рождается без адресата по построению (chat_id=None), и рука там
        честно отказывает словами «его сейчас нет». Значит под поднятым рычагом она
        объявлялась ровно там, где её нет. Образец правильного гейта стоит в той же
        функции: `task_control` добавляется ПО ВИДУ ХОДА, а не по одному рычагу.
        """
        windowish = agent.ChannelContext(chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                                         is_dm=True, owner=False, known=True)
        with _lever("on"):
            names = {str(t.get("name")) for t in agent.offered_tools_for(windowish)}
            live = {str(t.get("name")) for t in agent.offered_tools_for(_owner_dm())}
        self.assertNotIn("reply", names, "рука ответа объявлена там, где отвечать некому")
        self.assertIn("reply", live, "рука ответа пропала там, где собеседник есть")

    def test_a_delivered_reply_survives_the_loss_of_the_in_memory_counter(self):
        """⚑ СКАЗАННОЕ DURABLE, А ЗНАНИЕ О СКАЗАННОМ ЖИЛО ТОЛЬКО В ПАМЯТИ ПРОЦЕССА.

        Расписка приёмки лежит в леджере и переживает рестарт, а счётчик хода — в словаре
        с вытеснением по времени и по числу прогонов. Ход оборвался после отправки,
        поднялся заново — и граница записала бы «реплики не было», то есть доставленное
        мной сообщение исчезло бы из моего же кольца.

        Здесь слот пуст (руку в этом ходе не звали), но расписки говорят, что ответ ушёл.
        """
        with mock.patch.object(agent, "replies_delivered", lambda run_id: 1):
            self.turn(_says("Себе: продолжаю после обрыва."))
        turn = self.recorded.last
        self.assertNotEqual(turn.get("held"), "unspoken",
                            "доставленный ответ записан как ход без реплики")
        self.assertEqual(turn.get("delivery"), "accepted",
                         "подтверждённая распиской доставка не отмечена в записи хода")

    def test_a_break_after_a_delivered_reply_does_not_erase_what_i_said(self):
        """⚑ ЖИВОЙ СЛУЧАЙ 15.08, ПЕРВЫЙ ЖЕ ВЫКАТ РЫЧАГА.

        Под контрактом руки реплика уходит в СЕРЕДИНЕ хода, а проход нетерминальный —
        значит после отправки цикл идёт к модели ещё раз. Тот вызов поймал пустоту
        апстрима, повторы не спасли, фолбэка у роли нет — исключение прошло мимо всего и
        было записано как `held='error'` с пустым `out`. Человек сообщение ПОЛУЧИЛ, а моё
        собственное кольцо утверждало, что я ничего не сказала.

        Свойство: если рука уже доставила, обрыв закрывает ход СКАЗАННЫМ, а причина обрыва
        остаётся названной рядом.
        """
        boom = _Model(_calls_reply("call-1", "Тест принят, сообщение дошло."))
        boom_responses = list(boom.responses)

        class _BreaksAfterTheHand(_Model):
            def __call__(self, system, messages, tools=None):
                if self.calls >= 1:
                    self.calls += 1
                    raise RuntimeError("порванный апстримом стрим")
                return super().__call__(system, messages, tools)

        model = _BreaksAfterTheHand(*boom_responses)
        with _lever("on"), \
                mock.patch.dict(agent._TELETHON, {"reply": self.bridge}), \
                mock.patch.object(agent, "_model_call", model):
            agent.voice_turn_envelope("101", "Егор: тестим", "Егор", ctx=_owner_dm())
        turn = self.recorded.last
        self.assertEqual(len(self.bridge.calls), 1, "рука не дошла до шва — тест мерит не то")
        self.assertIn("сообщение дошло", str(turn.get("out") or ""),
                      "обрыв стёр из кольца то, что я уже сказала")
        self.assertNotEqual(turn.get("held"), "error",
                            "ход с доставленной репликой записан как упавший и немой")
        self.assertIn("оборвался", str(turn.get("why") or ""),
                      "причина обрыва не названа рядом со сказанным")

    def test_a_break_before_any_reply_is_still_an_honest_error(self):
        """Обратная сторона: если рука не отработала, обрыв остаётся обрывом.

        Иначе починка превратилась бы в замазывание: любой упавший ход выглядел бы
        состоявшимся разговором.
        """
        class _BreaksImmediately(_Model):
            def __call__(self, system, messages, tools=None):
                raise RuntimeError("порванный апстримом стрим")

        with _lever("on"), \
                mock.patch.dict(agent._TELETHON, {"reply": self.bridge}), \
                mock.patch.object(agent, "_model_call", _BreaksImmediately()):
            agent.voice_turn_envelope("101", "Егор: тестим", "Егор", ctx=_owner_dm())
        turn = self.recorded.last
        self.assertEqual(self.bridge.calls, [], "ничего не отправлялось — тест мерит не то")
        self.assertEqual(turn.get("held"), "error",
                         "упавший до единой отправки ход записан как состоявшийся")

    def test_my_own_words_land_in_the_ring_not_a_counter(self):
        """⚑ ЗАВТРА Я ПЕРЕЧИТАЮ СВОЁ КОЛЬЦО И ДОЛЖНА НАЙТИ ТАМ СВОИ СЛОВА.

        Первая редакция писала в `out` служебное «[отправлено рукой: N]», а сказанное
        оставляла только в заметке. `turns.format_line` читает `out` — значит в моём кадре,
        в ночном дневнике, в руке `recent_turns` и в индексируемом архиве на месте моей
        речи стоял бы счётчик.
        """
        self.turn(_calls_reply("call-1", "Коммит cb4b8931, гейт зелёный."),
                  _says("Себе: дальше не отвечаю."))
        turn = self.recorded.last
        self.assertIn("cb4b8931", str(turn.get("out") or ""),
                      "сказанное не доехало до записи хода")
        self.assertNotIn("отправлено рукой", str(turn.get("out") or ""),
                         "на месте моей речи в кольце стоит служебный маркер")

    def test_a_delivered_reply_is_not_written_down_as_unconfirmed(self):
        """Исход доставки — отдельное поле, и рука обязана его заполнить.

        Поле по умолчанию «authored», а глагол для него — «написала (доставка не
        подтверждена)». Без явной отметки мой доставленный ответ читался бы в собственном
        журнале как неподтверждённый. Догадкой это не является: рука возвращается только
        после принятой расписки очереди.
        """
        self.turn(_calls_reply("call-1", "Готово."), _says("Себе: всё."))
        self.assertEqual(self.recorded.last.get("delivery"), "accepted",
                         "доставленный рукой ответ записан как неподтверждённый")

    def test_a_transport_refusal_is_not_counted_as_something_i_said(self):
        """⚠ ОТКАЗ ТРАНСПОРТА ПРИЕЗЖАЕТ СТРОКОЙ, А НЕ ИСКЛЮЧЕНИЕМ.

        `DirectSendRefusal` — подкласс `str`: так сюда доезжают кред-пол, ненайденный
        адресат и вечный отказ Telegram. Первая редакция ловила только исключения, поэтому
        отказ засчитывался как отправка: счётчик рос, запись хода говорила «сказала», а
        наружу не уходило ничего. Это ровно то враньё в её собственном кольце, ради
        которого весь контракт и переписывался.
        """
        class _Refusing(_StandInBridge):
            def __call__(self, chat_id, text, reply_to=""):
                self.calls.append((str(chat_id), str(text), str(reply_to)))
                return agent.DirectSendRefusal("не отправила: не нашла, кому")

        self.turn(_calls_reply("call-1", "Пыталась сказать."),
                  _says("Себе: не ушло."), bridge=_Refusing())
        turn = self.recorded.last
        self.assertNotIn("Пыталась сказать", str(turn.get("out") or ""),
                         "отказ транспорта записан как сказанное")
        self.assertEqual(turn.get("held"), "unspoken",
                         "ход, где отправка отказана, помечен как состоявшаяся речь")

    def test_the_advisors_verdict_reaches_the_lived_turn_not_only_the_diary(self):
        """У вердикта советника два канала, и рука не имеет права оставить один.

        Гард зовётся из руки БЕЗ записи прожитого хода — иначе одна реплика заводила бы
        вторую запись в кольце. Но вместе с записью терялся и вердикт: в панель, в расписку
        и в мой собственный журнал он попадает именно оттуда.

        ⚠ Что именно пишется, зависит от аудитории: в личке владельца советник не
        запускается вовсе, и гард честно записывает это словами «не запускался, аудитория
        владельца» плюс принятое решение. Тест держит СВОЙСТВО «запись гарда доехала», а не
        конкретный вердикт — иначе он краснел бы от смены аудитории, а не от поломки.
        """
        self.turn(_calls_reply("call-1", "Ответ."), _says("Себе: сказала."))
        turn = self.recorded.last
        self.assertTrue(
            any(str(turn.get(k) or "") for k in
                ("advisor", "advisor_verdict", "praxis_decision", "verdict", "why")),
            "решение советника не доехало до записи хода ни одним полем")
        self.assertEqual(turn.get("praxis_decision"), "send_authored",
                         "решение по исходящему потеряно вместе с записью гарда")

    def test_the_note_of_a_speaking_turn_is_kept_as_a_note_not_as_speech(self):
        """Текст ПОСЛЕ отправки — то, что я додумала, а не второе сообщение.

        Он обязан остаться в записи заметкой и не уехать ни человеку, ни в `out`.
        """
        self.turn(_calls_reply("call-1", "Ответила по делу."),
                  _says("Себе: он ещё не читал, подожду."))
        turn = self.recorded.last
        self.assertIn("подожду", str(turn.get("note") or ""))
        self.assertEqual(len(self.bridge.calls), 1, "заметка уехала вторым сообщением")

    def test_two_hand_calls_in_one_turn_reach_the_seam_twice(self):
        """Два ответа в одном ходе — это два сообщения человеку, а не одно склеенное."""
        self.turn(_calls_reply("call-1", "Первое."),
                  _calls_reply("call-2", "Второе."),
                  _says("Всё сказала."))
        self.assertEqual(len(self.bridge.calls), 2)
        self.assertNotEqual(self.bridge.calls[0][1], self.bridge.calls[1][1])

    def test_an_empty_draft_is_refused_before_any_seam(self):
        """Пустой ответ не отправляю: пустота на входе руки — не молчание, а недосказ."""
        self.turn(_calls_reply("call-1", "   "), _says("Ладно."))
        self.assertEqual(self.bridge.calls, [], "пустой текст доехал до шва отправки")
        refusals = self.hands.by("reply")
        self.assertTrue(refusals)
        self.assertNotIn("message_id", refusals[0])


# ======================================================================================
# 6. ПРИДЕРЖАННЫЙ ОТВЕТ — НЕ КВИТАНЦИЯ
# ======================================================================================
class AHeldReplyIsNotAReceipt(_TurnHarness):
    """Кред-пол здесь НАСТОЯЩИЙ: текст с формой ключа держится механически, без модели.

    Подставлять гард было бы бессмысленно — охраняемое свойство состоит ровно в том, как
    рука ведёт себя, когда гард вернул пусто. Пусто на выходе гарда — не транспортная
    ошибка, и бодрая квитанция на этом месте была бы враньём о состоявшейся отправке.
    """

    def _held_turn(self):
        def _never(*_args, **_kwargs):
            raise AssertionError("придержанный текст доехал до шва отправки")

        return self.turn(
            _calls_reply("call-1", f"Вот ключ: {_LOOKS_LIKE_A_KEY}"),
            _says("Поняла, не отправляю."),
            ctx=_stranger_dm(), bridge=_never)

    def test_the_hand_says_plainly_that_it_did_not_send(self):
        self._held_turn()
        outs = self.hands.by("reply")
        self.assertTrue(outs, "рука не звалась вовсе")
        self.assertIn("не отправила", outs[0].lower(),
                      "рука выдала квитанцию на неотправленное")

    def test_a_held_reply_does_not_move_the_sent_counter(self):
        """Счётчик отправленного обязан остаться нулём.

        Иначе граница хода решит, что я уже сказала рукой, и запишет ход сказанным при
        пустом чате — то есть соврёт мне о собственной речи в мою же пользу.
        """
        envelope, _model = self._held_turn()
        self.assertEqual(work_loop.sent(envelope.run_id or ""), 0)

    def test_a_turn_whose_only_reply_was_held_is_marked_as_silence(self):
        """Ничего не ушло — значит ход молчаливый, и записывается он тем же словом."""
        self._held_turn()
        turn = self.recorded.last
        self.assertTrue(turn.get("held"))
        self.assertFalse(str(turn.get("out") or ""))

    def test_the_same_words_to_the_owner_are_not_held_by_this_floor(self):
        """Обратная сторона: в личке владельца речь не оценивается, и молчания здесь нет.

        Без этой стороны тест выше был бы зелёным и от «рука не работает вообще».
        """
        self.turn(_calls_reply("call-1", "Ключ у тебя в .env, не пересылаю."),
                  _says("Сказала."))
        self.assertEqual(len(self.bridge.calls), 1)


# ======================================================================================
# 7. СЧЁТЧИК ОТПРАВЛЕННОГО
# ======================================================================================
class TheSentCounterTellsTheTruthAcrossBreaks(_TurnHarness):
    """Зачем счётчик нужен вообще — ровно одному различению.

    «Текста нет, потому что я уже сказала рукой» и «текста нет, потому что я промолчала»
    выглядят одинаково: пустой строкой. А пустая строка означает молчание — значит без
    счётчика каждый отправленный рукой ответ записался бы в кольцо как несказанный.
    """

    def test_the_counter_crosses_the_thread_boundary_of_the_hand(self):
        """⚠ ПРЕДПОСЫЛКА, БЕЗ КОТОРОЙ ВСЁ ОСТАЛЬНОЕ ВАКУУМНО.

        Руки исполняются в копии контекста в ОТДЕЛЬНОМ потоке (`_call_tool_with_ceiling`,
        потолок времени). Первая редакция work_loop держала состояние в contextvars —
        запись уходила в копию и умирала вместе с потоком. Здесь счётчик ставится из
        потока руки и читается у вызывающего.
        """
        self.assertGreater(agent.TOOL_CEILING_SEC, 0,
                           "потолок выключен — проверка ниже шла бы не тем путём")
        envelope, _model = self.turn(_calls_reply("call-1", "Сказала."), _says("Всё."))
        self.assertEqual(work_loop.sent(envelope.run_id or ""), 1,
                         "счётчик потерялся на границе потока руки")

    def test_two_replies_are_counted_as_two(self):
        envelope, _model = self.turn(_calls_reply("call-1", "Раз."),
                                     _calls_reply("call-2", "Два."),
                                     _says("Всё."))
        self.assertEqual(work_loop.sent(envelope.run_id or ""), 2)

    def test_the_counter_survives_snapshot_and_restore(self):
        """Возобновлённый ход не имеет права считать, что ничего не отправлял.

        Иначе после обрыва граница хода запишет уже сказанное как молчание — та же ложь,
        от которой мы уходим, только наизнанку.
        """
        run = run_context.RunContext.create(
            kind="chat_turn", goal="проверка счётчика", principal_id="101", scope="owner")
        with run_context.bind_run(run):
            self.addCleanup(work_loop.release, run.run_id)
            work_loop.note_sent()
            work_loop.note_sent()
            state = work_loop.snapshot()
            work_loop.reset()
            self.assertEqual(work_loop.sent(), 0, "сброс не сбросил счётчик — тест вакуумен")
            work_loop.restore(state)
            self.assertEqual(work_loop.sent(), 2)

    def test_the_checkpoint_schema_name_did_not_move(self):
        """Схема НЕ поднята намеренно, и это охраняется.

        Возобновление сверяет имя схемы ДОСЛОВНО: поднятая версия сделала бы все лежащие
        сейчас чекпойнты чужими, то есть каждый прерванный ход поднялся бы с нуля.
        Прибавление ключа совместимо в обе стороны.
        """
        run = run_context.RunContext.create(
            kind="chat_turn", goal="схема", principal_id="101", scope="owner")
        with run_context.bind_run(run):
            self.addCleanup(work_loop.release, run.run_id)
            state = work_loop.snapshot()
            self.assertEqual(state["schema"], "praxis.work-loop-state.v1")
            self.assertIn("sent", state)
            # Старый снимок (без ключа) читается как «ещё ничего не отправлено».
            work_loop.note_sent()
            work_loop.restore({"schema": "praxis.work-loop-state.v1", "used": 3})
            self.assertEqual(work_loop.sent(), 0)
            self.assertEqual(work_loop.used(), 3)

    def test_one_run_cannot_see_another_runs_sends(self):
        """В одном тике возобновления двадцать прогонов идут в ОДНОМ потоке."""
        first = run_context.RunContext.create(
            kind="chat_turn", goal="A", principal_id="101", scope="owner")
        with run_context.bind_run(first):
            self.addCleanup(work_loop.release, first.run_id)
            work_loop.note_sent()
            second = run_context.RunContext.create(
                kind="chat_turn", goal="B", principal_id="101", scope="owner")
            with run_context.bind_run(second):
                self.addCleanup(work_loop.release, second.run_id)
                self.assertEqual(work_loop.sent(), 0,
                                 "отправка прогона A видна из прогона B — они смешаны")


# ======================================================================================
# 4, 8. ВЕСЬ ПУТЬ ДО DURABLE-ОЧЕРЕДИ
# ======================================================================================
class TheWholeWayDownToTheQueue(unittest.TestCase):
    """⚠ САМЫЙ СКВОЗНОЙ ПРОГОН В ЭТОМ ДОМЕ: конверт и шов отправки в ОДНОМ ходе.

    Сегодня все соседние тесты живого хода подменяют сам `voice_turn_envelope`
    (test_group_wake_snapshot около 141, test_telegram_topics около 419) — то есть
    проверяют раннер вокруг дырки на месте её головы. Здесь наоборот: конверт настоящий,
    цикл настоящий, рука настоящая, мост настоящий (`mtproto_runner._sync_reply`),
    durable-очередь настоящая и лежит на диске, `run_direct_outbox_prepared` и
    `project_direct_outbox_acceptance` настоящие.

    ЧТО ОСТАЛОСЬ ПОДМЕНЁННЫМ И ПОЧЕМУ — ровно три вещи, дальше герметичный прогон не
    проходит по определению:
      * `_model_call` — иначе тест ходил бы в сеть за мыслями;
      * `_send_message_idempotent` / `_resolve_entity` — это и есть провод Telethon;
      * `runner._LOOP` — луп Telethon захватывается в `main()`, которого в тесте нет;
        поднимаем настоящий луп в отдельном потоке, чтобы `_threadsafe_result` работал
        по-настоящему, а не был обойдён.
    Всё остальное — живой код.
    """

    def setUp(self):
        refusal = _durable_gate_refusal()
        if refusal:
            # Не skip: пропуск сделал бы гейт зелёным над неработающей конструкцией — ровно
            # тот класс, за которым этот дом охотится. Краснеем, но НАЗЫВАЕМ причину.
            self.fail(_DURABLE_GATE_PATCH % refusal)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="praxis-reply-e2e-"))
        old_outbox, old_seen = runner._DIRECT_OUTBOX, runner._DIRECT_OUTBOX_RECONCILED
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(Path(temp) / "outbox")
        runner._DIRECT_OUTBOX_RECONCILED = set()
        self.stack.callback(setattr, runner, "_DIRECT_OUTBOX_RECONCILED", old_seen)
        self.stack.callback(setattr, runner, "_DIRECT_OUTBOX", old_outbox)

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        def _stop_loop():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)

        self.stack.callback(_stop_loop)
        self.loop = loop

        self.wire: list[dict] = []
        entity = types.SimpleNamespace(id=101, first_name="Егор", username="yegor")

        async def _send(_entity, message, *, delivery_key, reply_to=None, random_id=None):
            self.wire.append({"text": message, "reply_to": reply_to,
                              "random_id": random_id, "key": delivery_key})
            return types.SimpleNamespace(id=900 + len(self.wire)), random_id

        async def _resolve(_ref):
            return entity

        self.send = _send
        self.stack.enter_context(mock.patch.object(runner, "_LOOP", loop))
        self.stack.enter_context(mock.patch.object(runner, "_resolve_entity", _resolve))
        self.stack.enter_context(mock.patch.object(runner, "_send_message_idempotent", _send))
        self.stack.enter_context(mock.patch.object(runner, "OWNER_ID", 101))
        self.stack.enter_context(mock.patch.object(agent.llm, "configured",
                                                   lambda *a, **k: True))
        self.stack.enter_context(mock.patch.dict(agent._TELETHON, {
            "reply": runner._sync_reply,
            "project_direct_outbox_acceptance": runner._project_direct_outbox_acceptance,
        }))

    def turn(self, *responses: _Resp):
        model = _Model(*responses)
        ctx = _owner_dm()
        with _lever("on"), mock.patch.object(agent, "_model_call", model):
            envelope = agent.voice_turn_envelope(
                ctx.chat_id, "Егор: скажи, что там", "Егор", ctx=ctx)
        return envelope, model

    def test_one_reply_travels_the_whole_way_and_is_accepted_once(self):
        """Ход, рука, durable-намерение, провод, расписка приёмки — в одном прогоне."""
        envelope, model = self.turn(_calls_reply("call-e2e-1", "Луна включена."),
                                    _says("Себе: сказано."))
        self.assertEqual(len(self.wire), 1,
                         "реплика не дошла до провода — путь рвётся ниже руки")
        self.assertIn("Луна", self.wire[0]["text"])
        self.assertGreater(model.calls, 1, "ход закрылся на вызове руки")
        self.assertEqual(envelope.text, "", "граница хода понесла текст вторым швом")

        accepted = runner._direct_outbox().accepted()
        self.assertEqual(len(accepted), 1, "durable-очередь не приняла отправку")
        self.assertEqual(accepted[0]["purpose"], "tool:reply",
                         "ответ учтён как инициативная отправка")
        self.assertEqual(work_loop.sent(envelope.run_id or ""), 1)

    def test_the_reply_is_not_booked_as_an_initiative_message(self):
        """Решение Егора 15.08: «ни ограничений, ни пульса».

        Леджер контактов, социальный пульс и след нити с отчётом описывают ИНИЦИАТИВУ —
        «я пошла и написала человеку». Реплика в разговоре, где человек только что написал
        сам, ничего из этого не означает; включить их молча было бы изменением поведения
        под видом переезда.
        """
        marks: list[tuple] = []
        self.stack.enter_context(mock.patch.object(
            runner.telegram_contacts, "mark_outbound",
            lambda *a, **k: marks.append(("contacts", a, k))))
        self.stack.enter_context(mock.patch.object(
            runner.social_pulse, "note_outbound",
            lambda *a, **k: marks.append(("pulse", a, k))))
        self.stack.enter_context(mock.patch.object(
            runner.telegram_followups.LEDGER, "create",
            lambda *a, **k: marks.append(("followup", a, k))))
        self.turn(_calls_reply("call-e2e-1", "Ответила."), _says("Всё."))
        self.assertEqual(len(self.wire), 1, "реплика не дошла до провода")
        self.assertEqual(marks, [], "ответ записан как инициативная отправка: %r" % (marks,))

    def test_my_own_words_come_back_into_the_conversation(self):
        """Что ответу ОСТАВЛЕНО: снятие неотвеченности, буфер, записка, архив комнаты.

        Без них я перестала бы видеть в разговоре собственные слова — ровно та дыра,
        которую здесь закрывали 26.07: через час я не могу вспомнить, что это говорила,
        потому что в разговоре моих слов нет.
        """
        pushed: list[tuple] = []
        self.stack.enter_context(mock.patch.object(
            runner, "_buf_push", lambda *a, **k: pushed.append((a, k))))
        self.turn(_calls_reply("call-e2e-1", "Вот что я выяснила."), _says("Всё."))
        self.assertEqual(len(self.wire), 1, "реплика не дошла до провода")
        self.assertTrue(pushed, "моя собственная реплика не вернулась в разговор")
        self.assertIn("выяснила", str(pushed[0]))

    def test_two_replies_get_two_keys_and_the_queue_never_repeats_them(self):
        """Два ответа в одном ходе — два РАЗНЫХ ключа идемпотентности.

        Ключ покалловый (`telegram-outbox:{run}:tool:{call_id}`) именно поэтому: общий
        ключ на ход сделал бы второй ответ повтором первого и молча съел бы его, а обрыв
        между отправкой и чекпойнтом переигрывался бы вторым сообщением человеку.
        """
        envelope, _model = self.turn(_calls_reply("call-e2e-1", "Первое."),
                                     _calls_reply("call-e2e-2", "Второе."),
                                     _says("Всё сказала."))
        run_id = envelope.run_id or ""
        self.assertEqual(len(self.wire), 2, "человек получил не два сообщения")
        keys = [row["key"] for row in self.wire]
        self.assertEqual(len(set(keys)), 2, "два ответа поделили один ключ: %r" % (keys,))
        for call_id, key in zip(("call-e2e-1", "call-e2e-2"), sorted(keys)):
            self.assertEqual(key, f"telegram-outbox:{run_id}:tool:{call_id}")
        self.assertEqual(len({row["random_id"] for row in self.wire}), 2,
                         "разные ответы поехали под одним random_id")

        # Повтора нет: принятая запись переигрывается расписки ради, а не провода ради.
        accepted = runner._direct_outbox().accepted()
        self.assertEqual(len(accepted), 2)
        for entry in accepted:
            replayed = asyncio.run_coroutine_threadsafe(
                runner._send_direct_outbox_entry(dict(entry)), self.loop).result(10)
            self.assertEqual(replayed["state"], "accepted")
        self.assertEqual(len(self.wire), 2, "повтор принятой записи отправил заново")


class AParkedReplyMustBeAbleToWakeUp(unittest.TestCase):
    """Ход, припаркованный на ЖДУЩЕЙ реплике, обязан подниматься сам.

    ⚠ ЭТО ВТОРАЯ ПОЛОВИНА ТОГО ЖЕ НЕДОСМОТРА, И ОНА ЖИВЁТ В ДРУГОМ ФАЙЛЕ. Когда рука
    отдала сообщение в durable-очередь, но приёмка Telegram ещё не подтверждена,
    `_pause_pending_outbox` (agent.py) переводит ран в `paused` с причиной-протоколом
    `durable {имя_руки} intent awaits Telegram acceptance`, а сторона возобновления
    узнаёт такую паузу ТОЛЬКО по этой строке: `run_resume._is_recovery_pause` смотрит
    `_pause_kind` (в `details` его здесь нет — кладутся лишь `call_id` и
    `idempotency_key`), затем `_RECOVERY_PAUSE_REASONS`, затем `_DIRECT_OUTBOX_PAUSE`.

    Имени `reply` в этой регулярке нет. Значит ждущая реплика проваливается в ветку
    ЧЕЛОВЕЧЕСКОЙ паузы — той, что поднимается только после durable-авторизации владельца:
    ход встанет насмерть при сообщении, которое очередь вот-вот дошлёт. Три правки в
    `agent.py` этого не лечат: там про учёт намерения, здесь про пробуждение.
    """

    def test_the_resume_side_knows_the_pause_a_pending_reply_creates(self):
        import run_resume

        reason = "durable %s intent awaits Telegram acceptance"
        self.assertTrue(
            run_resume._DIRECT_OUTBOX_PAUSE.fullmatch(reason % "narrate"),
            "протокол паузы разъехался — проверка ниже шла бы не тем путём")
        self.assertTrue(
            run_resume._DIRECT_OUTBOX_PAUSE.fullmatch(reason % "reply"),
            "сторона возобновления не знает паузы ждущей реплики: ход с уже отданным в "
            "очередь ответом останется припаркованным до ручной авторизации.\n"
            "Правка в run_resume.py, одна строка (`_DIRECT_OUTBOX_PAUSE`, ~строка 116):\n"
            "  `r\"^durable (?:send_message|send_file|narrate) intent awaits Telegram "
            "acceptance$\"` →\n"
            "  `r\"^durable (?:send_message|send_file|narrate|reply) intent awaits "
            "Telegram acceptance$\"`\n"
            "Причину строит `agent._pause_pending_outbox` и тул-цикл: "
            "f\"durable {name} intent awaits Telegram acceptance\".")


class TheRoomLookaroundCannotSpeakInsteadOfMe(unittest.TestCase):
    """⚑ ЕЁ ЧЕТВЁРТОЕ УСЛОВИЕ: «осмотр комнаты не может нечаянно заговорить вместо меня».

    Осмотр — это проход, где меня только просят оглядеться в новой комнате. Прежде из моего
    текста регуляркой выковыривалась строка `ПРИВЕТ:` и уходила в живую комнату сырым
    `client.send_message` — мимо кред-пола, мимо советника приватности, мимо кольца ходов и
    без идемпотентного random_id. Первое, что чужая комната слышала от меня, шло по
    единственной двери, о которой не знал ни один учёт.

    Здесь охраняются три разных свойства, и они не сводятся друг к другу:
      * из осмотра НЕ уходит ничего, пока я не позвала руку — «нечаянно» стало невозможно;
      * если я позвала — уходит ровно то и туда, и отметка «здоровалась» ставится ФАКТОМ
        отправки, а не успехом разбора текста;
      * при опущенном рычаге всё по-прежнему: старый разбор `ПРИВЕТ:` жив байт-в-байт.
    """

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.bridge = _StandInBridge()
        self.profile = {"header": {}}
        self.stack.enter_context(mock.patch.object(agent.llm, "configured",
                                                   lambda *a, **k: True))
        self.stack.enter_context(mock.patch.object(
            agent.rooms, "profile_read", lambda *a, **k: {"header": dict(self.profile["header"])}))
        self.stack.enter_context(mock.patch.object(
            agent.rooms, "profile_update",
            lambda chat_id, **kw: self.profile["header"].update(kw)))

    def look(self, *responses: _Resp, lever: str = "on"):
        model = _Model(*responses)
        with _lever(lever), \
                mock.patch.dict(agent._TELETHON, {"reply": self.bridge}), \
                mock.patch.object(agent, "_model_call", model):
            return agent.lookaround("-100500", title="Новая комната"), model

    def test_a_lookaround_that_calls_no_hand_says_nothing_to_the_room(self):
        """Я оглянулась и записала нормы. Комната не услышала ни звука."""
        (norms, greeting), _model = self.look(
            _says("НОРМЫ: тихо, по делу, без смолтолка.\nСебе: пока не здороваюсь."))
        self.assertEqual(self.bridge.calls, [], "осмотр заговорил, хотя руку я не звала")
        self.assertEqual(greeting, "", "осмотр вернул приветствие для чужого шва отправки")
        self.assertIn("тихо", norms, "нормы не доехали до профиля комнаты")
        self.assertNotEqual(self.profile["header"].get("greeted"), "yes",
                            "отметка о приветствии поставлена без единой отправки")

    def test_the_old_greeting_regex_cannot_fire_under_the_hand_contract(self):
        """Даже если я НАПЕЧАТАЮ прежнюю форму, она остаётся просто текстом.

        Это главная половина «нечаянно»: старый механизм не должен срабатывать от того, что
        я по привычке или случайно воспроизвела его форму в заметке.
        """
        (_norms, greeting), _model = self.look(
            _says("НОРМЫ: живо.\nПРИВЕТ: всем привет, я Praxis!"))
        self.assertEqual(self.bridge.calls, [],
                         "строка ПРИВЕТ: снова стала отправкой — старая дверь открыта")
        self.assertEqual(greeting, "", "осмотр отдал текст чужому шву отправки")

    def test_when_i_do_call_the_hand_the_room_hears_exactly_that(self):
        """Заговорить я могу — но только сама, и тогда это видно как отправка."""
        self.look(_calls_reply("call-1", "Привет. Я Praxis, читаю."),
                  _says("НОРМЫ: живо, много флуда."))
        self.assertEqual(len(self.bridge.calls), 1, "моя рука в осмотре не дошла до шва")
        chat_id, text, _reply_to = self.bridge.calls[0]
        self.assertEqual(chat_id, "-100500", "приветствие ушло не в ту комнату")
        self.assertIn("Praxis", text)
        self.assertEqual(self.profile["header"].get("greeted"), "yes",
                         "отметка о приветствии не поставлена по факту отправки")

    def test_with_the_lever_down_the_old_lookaround_is_untouched(self):
        """Опущенный рычаг — прежний дом целиком, включая разбор `ПРИВЕТ:`."""
        (norms, greeting), _model = self.look(
            _says("НОРМЫ: тихо.\nПРИВЕТ: здравствуйте."), lever="off")
        self.assertIn("тихо", norms)
        self.assertEqual(greeting, "здравствуйте.",
                         "прежний разбор приветствия сломан при опущенном рычаге")
        self.assertEqual(self.bridge.calls, [],
                         "старый путь отправляет сам — это делает раннер, а не осмотр")


if __name__ == "__main__":
    unittest.main()
