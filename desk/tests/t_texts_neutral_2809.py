# -*- coding: utf-8 -*-
"""Тексты, которые издание кладёт агенту, — без рода, без Праксис, контейнеров и сервера.

Слово Егора 28.09: «не должно быть рода в принципе в описании чего-либо, как и упоминания
контейнеров и Праксис… где есть род — писать по-английски». Стенд держит стартовый комплект
(конституция, голос, запись дня ноль, письмо, навыки) и тексты харнесса, которые уходят
модели: известные формы с родом, найденные 28.09 во входе модели Джарвиса, и слова чужого
дома. Новая такая строка — красный стенд с именем файла и строкой.

В 1.5.9 владелец отдельно потребовал включить все предоставленные навыки без изменения
текста; попытка исключить их была отозвана. Эти заводские файлы проверяются по точным
принятым байтам, остальные шаблоны и новые навыки — по прежнему языковому контракту.
Проверка не относится к собственным soul-файлам работающего агента.

Запуск:  python tests/t_texts_neutral_2809.py
"""
from __future__ import annotations

import re
import hashlib
import json
from collections import Counter
import sys
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
RES = DESK / "resources"
sys.path.insert(0, str(DESK / "localharness"))

KIT = [RES / "SOUL.md", RES / "self-day-zero.md", RES / "soul" / "VOICE.md",
       RES / "soul" / "self.md", *sorted((RES / "soul" / "skills").glob("*.md"))]
SUPPLIED = json.loads((Path(__file__).parent / 'fixtures/owner-supplied-skills-1.5.9.json').read_text(encoding='utf8'))
# 16.10: Yegor approved one narrowly sanitized factory revision. The historical
# 1.5.9 hash stays in the fixture; only these exact NEW bytes may replace that
# template. An old copy containing the private room ID is not acceptable again.
SANITIZED_TEMPLATE_HASHES = {
    'abstractdl-mtproto-moderation.md': '0b54434ff6ea97b2bbb87d65ed44a79ad34a08986f18529f2e47aa5f5f35536f',
}


def owner_supplied(path):
    expected = SUPPLIED['files'].get(path.name) if path.parent == RES/'soul/skills' else None
    if expected and path.name in SANITIZED_TEMPLATE_HASHES:
        expected = SANITIZED_TEMPLATE_HASHES[path.name]
    return bool(expected) and hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected

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
    accepted = Counter()
    if path == RES / 'soul' / 'skills' / 'INDEX.md':
        # The 1.5.9 INDEX is a byte-accepted owner document, but unlike the
        # 78 skills it is also factory navigation and grows with new skills.
        # Scan the changed/added lines, not accepted owner lines already there.
        # A multiset prevents one historical line from excusing arbitrarily
        # many new copies of the same forbidden text.
        import boot
        expected = SUPPLIED['files']['INDEX.md']
        for prior in boot._kit_history().get('soul/skills/INDEX.md', []):
            if hashlib.sha256(prior.encode('utf-8').replace(b'\r\n', b'\n')).hexdigest() == expected:
                accepted = Counter(prior.split('\n'))
                break
    for no, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if accepted[line]:
            accepted[line] -= 1
            continue
        yield no, line


class KitIsNeutral(unittest.TestCase):
    def test_owner_supplied_factory_skills_keep_the_accepted_bytes(self):
        self.assertEqual(SUPPLIED['schema'], 'helene.owner-supplied-skills.v1')
        self.assertTrue(SUPPLIED['files'])
        for name in SUPPLIED['files']:
            with self.subTest(skill=name):
                if name == 'INDEX.md':
                    # The INDEX was owner-supplied in 1.5.9, but is also the factory
                    # navigation page: later additions must appear there without
                    # rewriting the historical owner-accepted text in the fixture.
                    # The immutable predecessor stays in the stock-upgrade history;
                    # refresh_kit only migrates byte-identical stock homes.
                    import boot
                    old_hash = SUPPLIED['files'][name]
                    predecessors = boot._kit_history().get('soul/skills/INDEX.md', [])
                    approved = [t for t in predecessors
                                if hashlib.sha256(t.encode('utf-8').replace(b'\r\n', b'\n')).hexdigest() == old_hash]
                    self.assertEqual(len(approved), 1, 'Owner-supplied INDEX predecessor lost')
                    # Four new rows and the corrected count in the introduction
                    # are the only intended changes to this accepted factory
                    # INDEX. Preserve every other owner-authored line (including
                    # the older 78 purpose links); the fixture itself stays old.
                    old_lines = Counter(approved[0].split('\n'))
                    old_intro = [line for line in old_lines if line.startswith('Эти двадцать написаны до меня')]
                    self.assertEqual(len(old_intro), 1)
                    old_lines[old_intro[0]] -= 1
                    current_lines = Counter((RES / 'soul' / 'skills' / name).read_text(encoding='utf-8').split('\n'))
                    self.assertEqual(old_lines - current_lines, Counter(), 'Accepted INDEX lines disappeared')
                else:
                    if name in SANITIZED_TEMPLATE_HASHES:
                        self.assertNotEqual(SUPPLIED['files'][name], SANITIZED_TEMPLATE_HASHES[name],
                                            'The owner-approved revision must not bless the former private value')
                    self.assertTrue(owner_supplied(RES/'soul/skills'/name),
                                    'Owner-accepted factory skill changed: ' + name)

    def test_no_foreign_home_in_the_kit(self):
        bad = [f"{p.relative_to(RES)}:{no}: {line.strip()}" for p in KIT if not owner_supplied(p) for no, line in _lines(p)
               if FOREIGN.search(line)]
        self.assertEqual(bad, [], "слова чужого дома в стартовом комплекте:\n" + "\n".join(bad))

    def test_no_known_gendered_forms_in_the_kit(self):
        bad = []
        for p in KIT:
            if owner_supplied(p): continue
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
