"""«Здесь» — это комната, а не наша псевдоветка.

ЖИВОЙ СЛУЧАЙ 16.08.2026, из-за которого этот файл существует.
Егор написал в AbstractDL корневое сообщение «Пракс, о чем тут шла речь?». Ход получил
адрес `origin_chat_id: "-1001240718803__topic__98994"` — псевдоветку из ОДНОГО сообщения.
Лента комнаты в кадре при этом была полной (53 упоминания Арета, 47 корневых строк, 182
тредовых), но на вопрос «а ветка рядом?» она ответила: «контекста до этого сообщения в
ветке нет». Для ветки это правда. Для комнаты — нет.

ПОЧЕМУ ТАК ВЫШЛО. `telegram_topics.route_for_message` умеет главное с 25.07: при
`is_forum=False` обычная супергруппа это ОДНА комната, и цепочка ответов не становится
ключом хранения. Знание о природе комнаты у дома тоже было — реестр маршрутов держал
вердикт «не форум» по живому ответу Telegram, 4604 наблюдения. Не было ОДНОГО: знание не
доезжало до построения ключа. Раннер копил свидетельства и писал рядом дословно:
«Маршрутизацию НЕ трогаем — только копим свидетельства, чтобы перекладка ключа однажды
делалась по знанию».

Здесь охраняется именно этот стык: три места, где строится ключ, спрашивают реестр, и
спрашивают ОДИНАКОВО — иначе одно и то же сообщение получит разный адрес на разных путях,
и это будет тот же дефект, только тише.
"""
from __future__ import annotations

import types
import unittest
from unittest import mock

import mtproto_runner as runner
import telegram_routes
import telegram_topics


def _msg(mid: int, *, reply_to_top: int | None = None, reply_to: int | None = None):
    """Сообщение в форме, которую читает `route_for_message`."""
    header = None
    if reply_to is not None or reply_to_top is not None:
        header = types.SimpleNamespace(
            reply_to_msg_id=reply_to, reply_to_top_id=reply_to_top,
            forum_topic=bool(reply_to_top))
    return types.SimpleNamespace(id=mid, reply_to=header)


class TheRoomVerdictReachesTheKey(unittest.TestCase):
    """Вердикт реестра обязан доезжать до ключа, а не оставаться тенью."""

    def test_a_plain_supergroup_keeps_one_key_for_the_whole_room(self):
        """Обычная супергруппа — одна комната. Цепочка ответов ключом не становится.

        Это и есть починка живого случая: сообщение в цепочке больше не уводит её в
        псевдоветку из одного сообщения.
        """
        with mock.patch.object(runner.telegram_routes, "status_at",
                               lambda *a, **k: (telegram_routes.FALSE, 1)):
            forum = runner._known_forum("-1001240718803", _msg(98996, reply_to_top=98994))
        self.assertIs(forum, False, "реестр сказал «не форум», а раннер это потерял")
        route = telegram_topics.route_for_message(
            "-1001240718803", _msg(98996, reply_to_top=98994),
            is_private=False, is_forum=forum)
        self.assertEqual(route.conversation_id, "-1001240718803",
                         "комната снова разрезана на псевдоветку")
        self.assertIsNone(route.topic_id)

    def test_a_real_forum_keeps_its_topics_apart(self):
        """Настоящий форум не трогаем: его темы разделил Telegram, а не мы."""
        with mock.patch.object(runner.telegram_routes, "status_at",
                               lambda *a, **k: (telegram_routes.TRUE, 1)):
            forum = runner._known_forum("-100777", _msg(500, reply_to_top=400))
        self.assertIs(forum, True)
        route = telegram_topics.route_for_message(
            "-100777", _msg(500, reply_to_top=400), is_private=False, is_forum=forum)
        self.assertEqual(route.conversation_id, "-100777__topic__400",
                         "настоящая тема форума перестала быть отдельным местом")

    def test_an_unproven_room_behaves_exactly_as_before(self):
        """«Не знаем» обязано быть прежним поведением байт-в-байт.

        Иначе правка меняет адрес там, где мы ничего не выяснили, — то есть чинит одно
        и ломает другое молча.
        """
        with mock.patch.object(runner.telegram_routes, "status_at",
                               lambda *a, **k: (telegram_routes.UNKNOWN, 0)):
            forum = runner._known_forum("-100999", _msg(7, reply_to_top=5))
        self.assertIsNone(forum, "неизвестное превратилось в утверждение")
        by_knowledge = telegram_topics.route_for_message(
            "-100999", _msg(7, reply_to_top=5), is_private=False, is_forum=forum)
        as_before = telegram_topics.route_for_message(
            "-100999", _msg(7, reply_to_top=5), is_private=False)
        self.assertEqual(by_knowledge.conversation_id, as_before.conversation_id)

    def test_a_broken_registry_does_not_change_the_address(self):
        """Прибор упал — это факт о приборе. Адрес хода от этого меняться не должен."""
        def _boom(*a, **k):
            raise RuntimeError("реестр недоступен")
        with mock.patch.object(runner.telegram_routes, "status_at", _boom):
            self.assertIsNone(runner._known_forum("-100999", _msg(7)))

    def test_the_epoch_is_asked_about_this_message_not_about_today(self):
        """У комнаты бывают эпохи: сообщение из прошлого роутится режимом СВОЕГО времени.

        Иначе вчерашний форум, ставший сегодня обычной группой, задним числом сольёт свои
        настоящие темы — те, что разделил Telegram, а не мы.
        """
        seen: list = []

        def _spy(peer_id, message_id=None):
            seen.append((str(peer_id), message_id))
            return telegram_routes.FALSE, 1

        with mock.patch.object(runner.telegram_routes, "status_at", _spy):
            runner._known_forum("-100999", _msg(4242, reply_to_top=4200))
        self.assertEqual(seen, [("-100999", 4242)],
                         "реестр спрошен не про это сообщение — эпохи перестали работать")


class TheThreeKeyBuildersAgree(unittest.TestCase):
    """Три пути строят ключ — и обязаны строить его одинаково.

    Иначе одно сообщение получит разный адрес в живом ходе, в добивке истории и при
    правке — и это будет тот же дефект «два читателя одного факта», только тише.
    """

    def test_every_call_site_passes_the_registry_verdict(self):
        import inspect
        src = inspect.getsource(runner)
        calls = src.count("telegram_topics.route_for_message(")
        # ⚠ Считаем ВЫЗОВЫ, а не форму строки: первая редакция этого теста пинила шаблон
        # `is_forum=_known_forum(`, и он развалился в тот же день, когда живой путь стал
        # передавать третий аргумент. Тест обязан держать свойство, а не вёрстку.
        asked = src.count("_known_forum(") - src.count("def _known_forum(")
        self.assertEqual(calls, 3, "число мест построения ключа изменилось — сверь заново")
        self.assertEqual(asked, calls,
                         "не все места спрашивают знание: адреса разъедутся молча")

    def test_the_live_paths_read_the_room_object_not_only_the_registry(self):
        """Прямой ответ Telegram сильнее накопленного вердикта — и он бесплатен.

        Живых путей два (входящее и правка); третий — добивка истории, и ей объект
        передавать НЕЛЬЗЯ: старое сообщение роутится режимом своего времени, а не
        сегодняшним флагом.
        """
        import inspect
        src = inspect.getsource(runner)
        self.assertEqual(src.count('_known_forum(event.chat_id, msg, getattr(event, "chat", None))'), 2,
                         "живые пути перестали читать объект комнаты — знание опять "
                         "приходит вчерашним днём")
        self.assertIn("_known_forum(peer, msg)", src,
                      "добивка истории начала спрашивать сегодняшний флаг вместо эпох")


if __name__ == "__main__":
    unittest.main()
