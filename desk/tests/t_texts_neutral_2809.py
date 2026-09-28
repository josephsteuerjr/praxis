# -*- coding: utf-8 -*-
"""Тексты, которые издание кладёт агенту, — без рода, без Праксис, контейнеров и сервера.

Слово Егора 28.09: «не должно быть рода в принципе в описании чего-либо, как и упоминания
контейнеров и Праксис… где есть род — писать по-английски». Стенд держит стартовый комплект
(конституция, голос, запись дня ноль, письмо, навыки) и тексты харнесса, которые уходят
модели: известные формы с родом, найденные 28.09 во входе модели Джарвиса, и слова чужого
дома. Новая такая строка — красный стенд с именем файла и строкой.

Запуск:  python tests/t_texts_neutral_2809.py
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
RES = DESK / "resources"
sys.path.insert(0, str(DESK / "localharness"))

KIT = [RES / "SOUL.md", RES / "self-day-zero.md", RES / "soul" / "VOICE.md",
       RES / "soul" / "self.md", *sorted((RES / "soul" / "skills").glob("*.md"))]

#: Чужой дом. `/app` — путь, «докер/контейнер/сервер» — чужая машина, Praxis — чужое имя.
FOREIGN = re.compile(r"(?i)praxis|праксис|контейнер|container|docker|докер|(?<![\w.])/app\b|"
                     r"сервер(?!ном издании)|\bserver\b")

#: Формы с родом, которые 28.09 стояли в комплекте о себе или о владельце. Список конкретный,
#: не эвристика: эвристика по окончаниям ловит «канал» и «журнал».
GENDERED = re.compile(
    r"(?i)(?<![\w-])(?:заперт|заперта|проснулся|проснулась|заметил|заметила|захотел|"
    r"захотела|поступил|поступила|переписал|переписала|завёл|завела|я видел|я видела|видел журнал|"
    r"не закрыл|не закрыла|выбрал не тот|выбрала не ту|осмотрелся|ты уверен|ты уверена|"
    r"какой-то другой|я ошибся|я ошиблась|я сам|я сама|сам себя|сама себя|самому|"
    r"я нашёл|я нашла|я увидел|я увидела|не проверял|не проверяла|научился|научилась|"
    r"сделал|сделала|проверил|проверила|я узнал|я узнала|я должен|я должна|уверен|уверена|"
    r"рад работать|рада работать|он сам|она сама|его «да»|её «да»)(?![\w-])")

#: Где «сделал/проверил…» — не про агента, а цитата чужой формы в правиле (пусто: не нужно).
ALLOW: dict[str, tuple[str, ...]] = {}


def _lines(path: Path):
    for no, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        yield no, line


class KitIsNeutral(unittest.TestCase):
    def test_no_foreign_home_in_the_kit(self):
        bad = [f"{p.relative_to(RES)}:{no}: {line.strip()}" for p in KIT for no, line in _lines(p)
               if FOREIGN.search(line)]
        self.assertEqual(bad, [], "слова чужого дома в стартовом комплекте:\n" + "\n".join(bad))

    def test_no_known_gendered_forms_in_the_kit(self):
        bad = []
        for p in KIT:
            for no, line in _lines(p):
                m = GENDERED.search(line)
                if m and not any(ok in line for ok in ALLOW.get(p.name, ())):
                    bad.append(f"{p.relative_to(RES)}:{no}: «{m.group(0)}» — {line.strip()}")
        self.assertEqual(bad, [], "формы с родом в стартовом комплекте:\n" + "\n".join(bad))

    def test_the_kit_does_not_explain_gender_away(self):
        soul = (RES / "SOUL.md").read_text(encoding="utf-8")
        self.assertNotIn("женском роде", soul)
        self.assertNotIn("Наследство", soul)


class HarnessWordsAreNeutral(unittest.TestCase):
    def test_broker_and_system_words(self):
        import broker
        import body
        for text in (broker.tool_schema(mac=False)["description"], body.SYSTEM_ROUTE_NOTE):
            self.assertIsNone(FOREIGN.search(text), text)
            self.assertNotIn("«да»", text)

    def test_session0_disclaimer_lives_on_the_toggle_not_on_the_hands(self):
        import modes
        self.assertEqual(modes.COMPUTER_WARNING, "")
        self.assertIn("энтузиаст", modes.SESSION0_WARNING)
        self.assertIn("без вопроса", modes.SESSION0_WARNING)

    def test_server_hands_are_listed_for_removal(self):
        import edition_tools
        for name in ("host_ctl", "server_status", "server_logs", "manage_service",
                     "propose_host_change", "list_host_changes", "restart_mailbot"):
            self.assertIn(name, edition_tools.SERVER_ONLY)


class EditionToolsInstall(unittest.TestCase):
    def test_removes_from_impl_and_every_list(self):
        import types
        import edition_tools
        mod = types.SimpleNamespace(
            TOOL_IMPL={"host_ctl": object(), "shell": object()},
            OWNER_TOOLS=[{"name": "host_ctl"}, {"name": "shell"}, {"name": "server_status"}],
            BASE_TOOLS=[{"name": "reply"}],
        )
        removed = edition_tools.install(mod)
        self.assertEqual(removed, ["host_ctl", "server_status"])
        self.assertEqual([t["name"] for t in mod.OWNER_TOOLS], ["shell"])
        self.assertNotIn("host_ctl", mod.TOOL_IMPL)
        self.assertEqual(edition_tools.install(mod), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
