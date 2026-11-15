# -*- coding: utf-8 -*-
"""Neutral current stock, with immutable evidence of the owner-accepted 1.5.9 kit.

The owner subsequently authorized four-language rewriting and server installations.
Product names and qualified server/container references are therefore not forbidden
words in reviewed teaching material. Historical accepted bytes must still survive;
the complete multilingual manifest binds every current replacement. Personal homes
are never rewritten by this test or by stock seeding.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

DESK=Path(__file__).resolve().parents[1];RES=DESK/'resources'
sys.path.insert(0,str(DESK/'localharness'))
KIT=[RES/'SOUL.md',RES/'self-day-zero.md',RES/'soul/VOICE.md',
     RES/'soul/self.md',*sorted((RES/'soul/skills').rglob('*.md'))]
SUPPLIED=json.loads((Path(__file__).parent/'fixtures/owner-supplied-skills-1.5.9.json').read_text(encoding='utf8'))
# Preserve the explicitly approved sanitized predecessor, never restore its old private value.
SANITIZED_TEMPLATE_HASHES={
    'abstractdl-mtproto-moderation.md':'0b54434ff6ea97b2bbb87d65ed44a79ad34a08986f18529f2e47aa5f5f35536f',
}

FOREIGN = re.compile(r"(?i)praxis|праксис|контейнер|container|docker|докер|(?<![\w.])/app\b|"
                     r"сервер(?!ном издании)|\bserver\b")


GENDERED = re.compile(
    r"(?i)(?<![\w-])(?:заперт|заперта|проснулся|проснулась|заметил|заметила|захотел|"
    r"захотела|поступил|поступила|переписал|переписала|завёл|завела|я видел|я видела|видел журнал|"
    r"не закрыл|не закрыла|выбрал не тот|выбрала не ту|осмотрелся|ты уверен|ты уверена|"
    r"какой-то другой|я ошибся|я ошиблась|я сам|я сама|сам себя|сама себя|самому|"
    r"я нашёл|я нашла|я увидел|я увидела|не проверял|не проверяла|научился|научилась|"
    r"сделал|сделала|проверил|проверила|я узнал|я узнала|я должен|я должна|уверен|уверена|"
    r"рад работать|рада работать|он сам|она сама|его «да»|её «да»)(?![\w-])")



class KitIsNeutral(unittest.TestCase):
    def test_owner_accepted_bytes_survive_current_or_shipped_history(self):
        import boot
        self.assertEqual(SUPPLIED['schema'],'helene.owner-supplied-skills.v1')
        self.assertTrue(SUPPLIED['files'])
        history=boot._kit_history()
        digest=lambda text:hashlib.sha256(text.encode('utf8').replace(b'\r\n',b'\n')).hexdigest()
        for name,accepted in SUPPLIED['files'].items():
            with self.subTest(skill=name):
                expected=SANITIZED_TEMPLATE_HASHES.get(name,accepted)
                current=(RES/'soul/skills'/name).read_text(encoding='utf8')
                candidates=[current,*history.get('soul/skills/'+name,[])]
                self.assertIn(expected,{digest(text) for text in candidates},
                              'Owner-accepted predecessor lost: '+name)
                if name in SANITIZED_TEMPLATE_HASHES:
                    self.assertNotEqual(expected,accepted)
                    self.assertNotIn(accepted,{digest(text) for text in candidates})

    def test_context_references_belong_to_complete_reviewed_multilingual_stock(self):
        spec=importlib.util.spec_from_file_location('neutral_stock_audit',DESK/'installer/stock_languages.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=module.audit(RES)
        self.assertTrue(result['complete'])
        self.assertEqual(result['reviewed_files'],result['canonical_files'])
        # Server support is real; a fixed foreign home or identity is not.
        claim=re.compile(r'(?i)(?:мой дом\s*[—:]\s*/app|я\s*[—:]\s*(?:праксис|praxis)\b)')
        for path in KIT:
            self.assertIsNone(claim.search(path.read_text(encoding='utf8')),str(path.relative_to(RES)))

    def test_no_known_gendered_forms_in_the_current_kit(self):
        # These are a named author's historical reading and an adjective about
        # a text, not gender assigned to the new agent or owner. Exact clauses;
        # the complete stock hash gate above still rejects any changed bytes.
        third_person={
            'soul/skills/living-prose/sources.md':'Автор комплекта прочёл доступный HTML с приложениями 12 ноября; данные, код и графики отдельно не проверял.',
            'soul/skills/living-prose/workshop.md':'оценивать по самому тексту и отклику читателя.',
        }
        bad=[]
        for path in KIT:
            relative=path.relative_to(RES).as_posix()
            for no,line in enumerate(path.read_text(encoding='utf8').splitlines(),1):
                clause=third_person.get(relative)
                scanned=line.replace(clause,'') if clause else line
                if match:=GENDERED.search(scanned):bad.append(f'{relative}:{no}: {match.group(0)}')
        self.assertEqual(bad,[])

    def test_the_kit_does_not_explain_gender_away(self):
        soul=(RES/'SOUL.md').read_text(encoding='utf8')
        self.assertNotIn('женском роде',soul);self.assertNotIn('Наследство',soul)


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
