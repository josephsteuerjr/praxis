# -*- coding: utf-8 -*-
"""Ключ кэша — АДРЕС разговора, а не хэш его содержимого.

Замер 10.08 на 2 823 записанных кадрах: общий префикс двух соседних системных промптов —
12 886 знаков вперемешку и 13 478…15 767 внутри одной аудитории. Реле берёт
`prompt_cache_key` из запроса и лишь при его отсутствии хэширует весь `messages[0]`,
из-за чего 79 из 84 промптов за сутки получали уникальный ключ.

⚠⚠ 15.08. ЗДЕСЬ ЛЕЖАЛА КОПИЯ ЛИТЕРАЛА, И ИМЕННО ОНА ДЕЛАЛА ТЕСТ СЛЕПЫМ.
Адрес аудитории опознаётся английскими подстроками ПРОЗЫ кадра («private owner channel»,
«public room», «your own run»). План работ прямо предполагает переписать эти места от её
лица по-русски. Тест же держал свои четыре строки-образца и проверял их сам с собой:
в день переписывания кадра он остался бы ЗЕЛЁНЫМ, `cache_address` начал бы возвращать
пустоту, реле вернулось бы к хэшу всего system — и кэш префикса умер бы без единого
симптома. Прибор смотрел бы в другой слой, а его молчание прочли бы как «всё в порядке».

Теперь тест СТРОИТ ЖИВОЙ КАДР (`agent.build_system_parts`) для всех четырёх аудиторий и
требует от каждой не просто непустого адреса, а ПРАВИЛЬНОГО тега. Своей копии прозы у
него больше нет: единственный источник литералов — сам agent.py. Переписали кадр, не
тронув `llm._CACHE_MARKS` — красный в тот же прогон, с указанием, что чинить.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import types
import unittest
from unittest import mock

# Заглушки до импорта agent — тот же приём, что в test_scope.py: load_dotenv(override=True)
# в agent НЕ должен тащить боевой .env в окружение прогона.
_fa = types.ModuleType("anthropic")
_fa.Anthropic = lambda **kw: None
sys.modules.setdefault("anthropic", _fa)
_fd = types.ModuleType("dotenv")
_fd.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", _fd)

import agent  # noqa: E402
import llm  # noqa: E402

ROOM_ID = "-1001240718803"
OTHER_ROOM_ID = "-1004301095307"

#: Четыре аудитории её кадра и тег, которым каждая обязана адресоваться.
#: Ветки — из `agent._build_prompt_parts`: собственный прогон, личка Егора, публичная
#: комната, незнакомый собеседник.
AUDIENCES = {
    "run": lambda: agent.ChannelContext(
        chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
        is_dm=True, owner=False, known=True, _scope_override="owner"),
    "owner": lambda: agent.ChannelContext(
        chat_id="100", principal_id="100", is_dm=True, owner=True, known=True),
    "room": lambda: agent.ChannelContext(
        chat_id=ROOM_ID, room_id=ROOM_ID, is_dm=False, owner=True, known=True),
    "guest": lambda: agent.ChannelContext(
        chat_id="777", principal_id="777", is_dm=True, owner=False, known=False),
}

_FRAMES: dict[str, str] = {}


def frame(audience: str, **over) -> str:
    """Живой системный кадр этой аудитории (кэш на модуль: сборка не бесплатна)."""
    if over:  # ChannelContext заморожен — правка только через replace
        ctx = dataclasses.replace(AUDIENCES[audience](), **over)
        return agent.build_system_parts(speaker="Егор", ctx=ctx)[1]
    if audience not in _FRAMES:
        _FRAMES[audience] = agent.build_system_parts(
            speaker="Егор", ctx=AUDIENCES[audience]())[1]
    return _FRAMES[audience]


def tag_of(address: str) -> str:
    """Тег аудитории из адреса `praxis:<модель>:<тег>:<комната>`."""
    parts = address.split(":")
    return parts[2] if len(parts) >= 4 else ""


def room_of(address: str) -> str:
    parts = address.split(":")
    return parts[3] if len(parts) >= 4 else ""


class TheAddressIsReadFromTheLivingFrame(unittest.TestCase):
    """Источник литералов — agent.py. Копии здесь нет и быть не должно."""

    def test_every_audience_of_the_living_frame_gets_its_own_tag(self):
        for audience, expected in (("run", "run"), ("owner", "owner"),
                                   ("room", "room"), ("guest", "guest")):
            with self.subTest(audience=audience):
                text = frame(audience)
                address = llm.cache_address("gpt-5.6-sol", text)
                self.assertTrue(
                    address,
                    f"живой кадр аудитории «{audience}» ({len(text)} знаков) не дал адреса "
                    f"вовсе: prompt_cache_key не уйдёт, реле вернётся к хэшу всего system, "
                    f"кэш префикса умрёт молча. Скорее всего кадр в agent.py переписан, а "
                    f"llm._CACHE_MARKS остались прежними — их надо править ВМЕСТЕ")
                self.assertEqual(
                    tag_of(address), expected,
                    f"аудитория «{audience}» адресуется тегом {tag_of(address)!r} вместо "
                    f"{expected!r}: маркер в llm._CACHE_MARKS разошёлся с кадром в agent.py")

    def test_no_marker_is_dead_wood(self):
        """Каждый маркер из таблицы обязан находиться хотя бы в одном живом кадре.

        Мёртвый маркер — это не безобидная строка: он выглядит работающим правилом, а
        аудитория, которую он якобы адресует, молча уезжает в общий котёл.
        """
        alive = {tag_of(llm.cache_address("m", frame(a))) for a in AUDIENCES}
        declared = {tag for _, tag in llm._CACHE_MARKS}
        self.assertEqual(declared - alive, set(),
                         "маркер объявлен в llm._CACHE_MARKS, но ни один живой кадр его "
                         "больше не содержит — правило мёртвое")
        self.assertEqual(alive - declared, set(),
                         "кадр адресуется тегом, которого нет в llm._CACHE_MARKS")

    def test_four_audiences_do_not_collide(self):
        keys = {llm.cache_address("m", frame(a)) for a in AUDIENCES}
        self.assertEqual(len(keys), len(AUDIENCES), "четыре разных адреса, а не один")

    def test_the_room_is_part_of_the_address(self):
        here = llm.cache_address("gpt-5.6-sol", frame("room"))
        there = llm.cache_address("gpt-5.6-sol", frame("room", chat_id=OTHER_ROOM_ID,
                                                      room_id=OTHER_ROOM_ID))
        self.assertEqual(room_of(here), ROOM_ID)
        self.assertNotEqual(here, there, "две разные комнаты получили один адрес")

    def test_a_moving_tail_does_not_move_the_address(self):
        text = frame("owner")
        a = llm.cache_address("gpt-5.6-sol", text + "\nuptime_minutes: 11")
        b = llm.cache_address("gpt-5.6-sol", text + "\nuptime_minutes: 12")
        self.assertTrue(a)
        self.assertEqual(a, b, "подвижный хвост не имеет права менять адрес")

    def test_model_is_part_of_the_address(self):
        text = frame("owner")
        self.assertNotEqual(llm.cache_address("sol", text), llm.cache_address("luna", text))

    def test_address_carries_no_content(self):
        """Адрес не имеет права нести её текст: в ключ уезжает только модель, роль и id."""
        address = llm.cache_address("gpt-5.6-sol",
                                    frame("owner") + "\nсекретная строка про Егора")
        self.assertNotIn("секрет", address)
        self.assertNotIn("Yegor", address)
        self.assertTrue(address.startswith("praxis:gpt-5.6-sol:"))


class TheStructuredKeyChangesNoAddress(unittest.TestCase):
    """Розетка под структурный ключ: включат — адрес обязан остаться тем же.

    `cache_address` умеет читать объявленный кадром `audience_key=<тег>` и предпочитает
    его прозе. Вилки пока нет: agent.py такого поля не печатает, и сегодня работает ветка
    прозы. Смысл этого класса — доказать ПРОГОНОМ, что в день включения адрес не сменится
    ни на одной аудитории: иначе включение обнулило бы кэш реле разом, и «мы улучшили
    ключ» выглядело бы как «мы уронили попадание».
    """

    # ⚠ ФОРМУЛА ЗДЕСЬ ПОВТОРЕНА НАРОЧНО, И ЭТО НЕ ДУБЛЬ РАДИ ДУБЛЯ.
    # Прежняя редакция брала тег ИЗ УЖЕ ПОЛУЧЕННОГО АДРЕСА — то есть из того же самого
    # разбора прозы, который и проверяла. Такой тест тавтологичен: он не мог покраснеть
    # ни при какой ошибке в формуле, которую собираются печатать в кадре, а именно она и
    # есть то новое, что включают. Теперь тег считается независимо, по той же формуле, что
    # предлагается для `agent.py` — и в день, когда формула и проза разойдутся, тест это
    # скажет.
    @staticmethod
    def _audience_key(audience: str) -> str:
        return {"run": "run", "owner": "owner", "room": "room", "guest": "guest"}.get(
            audience, "")

    def test_turning_the_socket_on_keeps_every_address_byte_for_byte(self):
        for audience in AUDIENCES:
            with self.subTest(audience=audience):
                text = frame(audience)
                before = llm.cache_address("gpt-5.6-sol", text)
                tag = self._audience_key(audience)
                self.assertTrue(tag, "аудитория без объявленного ключа — формула отстала")
                after = llm.cache_address("gpt-5.6-sol",
                                          text + f"\naudience_key={tag}\n")
                self.assertEqual(before, after,
                                 "структурный ключ дал НЕ ТОТ адрес, что проза — включение "
                                 "поля обнулило бы кэш реле на этой аудитории")

    def test_the_socket_works_without_any_prose_at_all(self):
        """Кадр без единого английского маркера, но с объявленным ключом, всё ещё адресуем."""
        russian = ("Я в личном канале с Егором. Комната: room_id=-100500; людей: 2.\n"
                   "audience_key=owner\n")
        address = llm.cache_address("gpt-5.6-sol", russian)
        self.assertEqual(tag_of(address), "owner")
        self.assertEqual(room_of(address), "-100500")

    def test_a_stranger_value_is_not_trusted(self):
        """Неизвестный тег не становится адресом: словарь один на прозу и на ключ."""
        text = frame("owner") + "\naudience_key=выдумка\n"
        self.assertEqual(tag_of(llm.cache_address("m", text)), "owner")


class TheInstrumentDoesNotStaySilent(unittest.TestCase):
    """Кадр без адреса обязан быть слышен на проде, а не только в этом файле."""

    def setUp(self):
        self._misses = dict(llm._ADDRESS_MISSES)
        llm._ADDRESS_MISSES["n"] = 0
        self.addCleanup(lambda: llm._ADDRESS_MISSES.update(self._misses))

    def test_a_big_frame_without_an_address_shouts(self):
        huge = "проза без единого маркера и без комнаты. " * 200
        self.assertGreater(len(huge), llm._CACHE_FRAME_MIN)
        with self.assertLogs("praxis-llm", level="WARNING") as caught:
            self.assertEqual(llm.cache_address("m", huge), "")
        self.assertIn("prompt_cache_key", "\n".join(caught.output))
        self.assertEqual(llm._ADDRESS_MISSES["n"], 1)

    def test_a_short_technical_call_is_not_an_alarm(self):
        llm.cache_address("m", "ping")
        self.assertEqual(llm._ADDRESS_MISSES["n"], 0,
                         "техвызов без кадра — не симптом; крик на нём обесценит крик")


class AddressIsStableAndSpecific(unittest.TestCase):
    def test_no_address_means_no_key_at_all(self):
        self.assertEqual(llm.cache_address("m", "просто текст без адреса"), "")
        self.assertEqual(llm.cache_address("m", ""), "")

    def test_lever_off(self):
        with mock.patch.dict(os.environ, {"PRAXIS_CACHE_KEY": "off"}):
            self.assertEqual(llm.cache_address("m", frame("owner")), "")


class ItReachesTheRequest(unittest.TestCase):
    class _Cli:
        def __init__(self):
            self.seen = {}
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            self.seen = kw
            raise RuntimeError("стоп: полезен только собранный запрос")

    def _payload(self, system, thinking=0):
        cli = self._Cli()
        try:
            llm._call_openai(cli, "gpt-5.6-sol", system=system, messages=[], tools=[],
                             max_tokens=16, thinking=thinking)
        except RuntimeError:
            pass
        return cli.seen

    def test_key_lands_in_extra_body(self):
        text = frame("owner")
        kw = self._payload(text)
        self.assertEqual(kw.get("extra_body", {}).get("prompt_cache_key"),
                         llm.cache_address("gpt-5.6-sol", text))

    def test_no_address_no_field(self):
        kw = self._payload("текст без адреса")
        self.assertNotIn("prompt_cache_key", kw.get("extra_body", {}))

    def test_effort_and_key_coexist(self):
        """Прежняя редакция клала extra_body целиком и затирала бы соседа."""
        extra = self._payload(frame("owner"), thinking=4096).get("extra_body", {})
        self.assertIn("prompt_cache_key", extra)
        self.assertIn("reasoning_effort", extra)


if __name__ == "__main__":
    unittest.main()
