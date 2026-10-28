"""`say` — её собственный взгляд на реплику, и он засчитывается как рука.

СМЫСЛ РУКИ НЕ В ТОМ, ЧТО ОНА ДЕЛАЕТ, А В ТОМ, ЧТО ОНА СЧИТАЕТСЯ.

Система и так покажет зеркало, если рук не было. Но тогда это НАШ укол. Позвала `say`
сама — счётчик рук больше нуля, продолжение не включается, и второй взгляд оказывается
её ходом. Цена одинаковая, разница в том, чей ход.

⚠ РУКА НИЧЕГО НЕ ОТПРАВЛЯЕТ, И ЭТО ОХРАНЯЕТСЯ ЗДЕСЬ. Отправку делает конец хода, как и
раньше: у текста ровно один шов доставки. Второй шов на этом месте стоил бы дороже всего,
что рука даёт — 05.08 два незнающих друг о друге шва уже давали ей повторы, которых она
не совершала (`praxis-repeat-two-send-seams`).
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import work_loop  # noqa: E402


class TheHandIsOffered(unittest.TestCase):
    def test_it_is_registered_and_visible_in_every_room(self):
        self.assertIs(agent.TOOL_IMPL["say"], agent.tool_say)
        self.assertIn("say", {t["name"] for t in agent.BASE_TOOLS})

    def test_its_description_says_it_sends_nothing(self):
        spec = next(t for t in agent.BASE_TOOLS if t["name"] == "say")
        self.assertIn("Ничего не отправляет", spec["description"])


class ItMirrorsAndStagesNothingElse(unittest.TestCase):
    def setUp(self):
        work_loop._STATE.clear()
        self.addCleanup(work_loop._STATE.clear)
        self.run = mock.patch.object(work_loop, "_run_key", return_value="run-say")
        self.run.start()
        self.addCleanup(self.run.stop)

    def test_it_returns_her_text_and_the_hands_of_this_turn(self):
        work_loop.note_hand("shell")
        out = agent.tool_say("Голос — gpt-5.6-terra.")
        self.assertIn("Голос — gpt-5.6-terra.", out)
        self.assertIn("shell", out)

    def test_with_no_prior_hands_it_says_so_plainly(self):
        self.assertIn("Руки не вызывались", agent.tool_say("Голос — DeepSeek."))

    def test_an_empty_draft_asks_instead_of_staging(self):
        out = agent.tool_say("   ")
        self.assertIn("Пустой черновик", out)
        self.assertIsNone(work_loop.said())

    def test_the_draft_is_remembered_for_this_run(self):
        agent.tool_say("черновик")
        self.assertEqual(work_loop.said()["text"], "черновик")

    def test_calling_it_twice_keeps_the_last_word(self):
        agent.tool_say("первый")
        agent.tool_say("второй")
        self.assertEqual(work_loop.said()["text"], "второй")

    def test_it_touches_no_delivery_seam(self):
        """Ни Telegram, ни outbox, ни staged media — только зеркало."""
        import inspect
        source = inspect.getsource(agent.tool_say)
        body = source.split('"""')[-1]
        for seam in ("_TELETHON", "_stage_turn_media", "_TURN_OUTBOUND", "outbox"):
            self.assertNotIn(seam, body, seam)


class CallingItSatisfiesTheLook(unittest.TestCase):
    def test_a_turn_that_used_say_is_not_nudged_again(self):
        """`say` увеличивает счёт рук — значит система молчит."""
        with mock.patch.dict("os.environ", {"PRAXIS_CHAT_FOLLOW_THROUGH": "on"}):
            keep, note = work_loop.chat_decide(
                "Голос — terra.", kind="chat_turn", hands=1, spent=0)
        self.assertFalse(keep)
        self.assertEqual(note, "")

    def test_the_system_look_and_her_own_look_are_the_same_words(self):
        """Одно зеркало на оба пути: разойдясь, они дали бы два разных правила."""
        with mock.patch.object(work_loop, "_run_key", return_value="run-same"):
            work_loop._STATE.clear()
            mine = agent.tool_say("текст")
        system = work_loop.mirror("текст", ())
        self.assertEqual(mine, system)
        work_loop._STATE.clear()


class TheNormIsAuthoredNotARequiredPrivateProfile(unittest.TestCase):
    """Portable synthetic authoring, never inspection of an owner's living soul.

    Fixed-persona byte assertions belonged to a private installation. The public
    seed is checked by desk/tests/t_base_skills_1510; the core contract is that
    the agent can write and revise this norm, including an honest noncheck.
    """
    def setUp(self):
        import tempfile
        from pathlib import Path
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.skills = Path(temp.name) / 'skills'
        self.enterContext(mock.patch.object(agent, 'SKILLS_DIR', self.skills))
        self.enterContext(mock.patch.object(agent, '_reindex'))
        self.enterContext(mock.patch.object(agent.selfgit, 'snapshot', return_value=''))
        self.enterContext(mock.patch.object(agent.immune, 'enqueue'))

    def write(self, text):
        agent.tool_write_skill('before_sending', text)
        return (self.skills / 'before_sending.md').read_text(encoding='utf-8')

    def test_the_skill_exists_and_is_listed_after_actual_authoring(self):
        text = self.write('# Before sending\n\nAn authored synthetic norm.')
        self.assertIn('An authored synthetic norm.', text)
        index = (self.skills / 'INDEX.md').read_text(encoding='utf-8')
        self.assertIn('](before_sending.md)', index)

    def test_its_author_can_rewrite_it_without_duplicate_index_rows(self):
        self.write('# Before sending\n\nwrite_skill may revise this norm.')
        text = self.write('# Before sending\n\nA later authored position.')
        self.assertIn('A later authored position.', text)
        self.assertNotIn('may revise', text)
        index = (self.skills / 'INDEX.md').read_text(encoding='utf-8')
        self.assertEqual(index.count('](before_sending.md)'), 1)

    def test_honest_noncheck_is_retainable_authored_text_not_machine_veto(self):
        text = self.write('# Before sending\n\nНе проверяла — законный ответ. say sends nothing.')
        self.assertIn('законный ответ', text)
        self.assertIn('say sends nothing.', text)


if __name__ == "__main__":
    unittest.main()
