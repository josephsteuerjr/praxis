# -*- coding: utf-8 -*-
"""Новая комната открывается тихой: умолчание участия — `addressed` (16.09).

ЖИВОЙ СЛУЧАЙ. Комната `-1004375515554` «CayleyPy_Agent»: 1461 сообщение, 31 участник,
четыре бота молотят друг с другом (352 · 314 · 106 · 29 сообщений). Её ходы там уходили
на 65 вызовов модели и 265 шагов, и прервать их было нечем: новое сообщение только бьёт
`_supersede_gen`, а идущий ход сверяется с ним ТОЛЬКО на доставке
(`mtproto_runner`, снимок `armed_gen` до первого await) — то есть цикл домалывает до
конца и выбрасывает готовый ответ как `superseded by a newer trigger before send`.

СЧЁТ, КОТОРЫЙ РЕШИЛ ВОПРОС. На момент правки из шестнадцати профилей комнат
**пятнадцать стояли `addressed`** и ровно ОДНА — `reflective`, самая свежая. То есть
`reflective` нигде не был чьим-то выбором: его штамповал протокол входа
(`rooms.profile_update(chat_id, engagement="reflective")`), а дальше каждую комнату
переводили руками. Умолчание, которое всегда правят вручную, — не умолчание, а работа.

И оно перебивало рычаг владельца: в проде стоял `PRAXIS_ROOM_ENGAGEMENT=addressed`, но
протокол писал участие в профиль ЯВНО, а явное сильнее умолчания.

ЦЕНА ОШИБКИ НЕСИММЕТРИЧНА. Лишний `addressed` — это «не заговорила сама», и человек
отменяет одним словом. Лишний `reflective` в шумной комнате — это ходы, которые нечем
остановить.

ЧЕГО ПРАВКА НЕ ДЕЛАЕТ: не трогает глубину комнаты (лента, сводка, кросс-темы, добор),
не трогает уже выбранное участие и не отнимает `reflective` — он остаётся выбором,
просто перестаёт быть умолчанием.

Запуск: python praxis_test.py test_addressed_by_default_1609 -v
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import rooms  # noqa: E402

LEVER = "PRAXIS_ROOM_ENGAGEMENT"


class TheDefaultIsQuiet(unittest.TestCase):

    def test_default_policy_is_addressed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(LEVER, None)
            self.assertEqual(rooms.default_policy()["engagement"], "addressed")

    def test_lever_still_rules(self):
        with mock.patch.dict(os.environ, {LEVER: "reflective"}):
            self.assertEqual(rooms.default_policy()["engagement"], "reflective")
        with mock.patch.dict(os.environ, {LEVER: "addressed"}):
            self.assertEqual(rooms.default_policy()["engagement"], "addressed")

    def test_a_broken_lever_falls_quiet_not_loud(self):
        """Испорченная строка обязана падать в тишину: иначе опечатка ПОВЫШАЕТ участие."""
        for junk in ("always", "", "REFLEKTIVE", "да"):
            with mock.patch.dict(os.environ, {LEVER: junk}):
                self.assertEqual(rooms.default_policy()["engagement"], "addressed",
                                 "мусор %r поднял участие" % junk)

    def test_reflective_is_still_a_legal_choice(self):
        self.assertIn("reflective", rooms.ENGAGEMENTS)
        self.assertIn("addressed", rooms.ENGAGEMENTS)

    def test_only_engagement_moved(self):
        """Глубина комнаты не поехала вместе с участием."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(LEVER, None)
            policy = rooms.default_policy()
        # 25.09: глубина поднята словом Егора (400 / 40 000) — см. test_room_memory_2509.
        self.assertEqual(policy["context_hot"], 400)
        self.assertEqual(policy["context_summary_chars"], 40_000)
        self.assertEqual(policy["cross_topics"], "map")


class TheNewcomerProtocolNoLongerStampsIt(unittest.TestCase):
    """Протокол входа больше не пишет участие в профиль вовсе."""

    def test_runner_source_does_not_write_reflective_on_join(self):
        import re
        from pathlib import Path
        src = Path(rooms.__file__).with_name("mtproto_runner.py").read_text(
            encoding="utf-8", errors="replace")
        # Строка кода, а не комментарий: комментарии слово `reflective` упоминать вправе.
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        stamped = re.findall(r"profile_update\([^)]*engagement\s*=\s*[\"']reflective[\"']",
                             code)
        self.assertEqual(stamped, [],
                         "протокол снова штампует reflective поверх рычага владельца")


if __name__ == "__main__":
    unittest.main(verbosity=2)
