"""Transport normalization and the outbound boundary (since 10.11: credential floor only)."""
import sys
import types
import unittest
from unittest import mock

# Заглушки до импорта agent: load_dotenv(override=True) в agent НЕ должен тащить боевой
# .env в os.environ (иначе тесты зависят от порядка и от деплой-конфига). См. §4.
_fa = types.ModuleType("anthropic")
_fa.Anthropic = lambda **kw: None
sys.modules.setdefault("anthropic", _fa)
_fd = types.ModuleType("dotenv")
_fd.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", _fd)

import agent  # noqa: E402


class TestStripThink(unittest.TestCase):
    def test_plain_unchanged(self):
        self.assertEqual(agent._strip_think("просто ответ по делу"), "просто ответ по делу")

    def test_full_block_before_answer(self):
        self.assertEqual(agent._strip_think("<think>рассуждаю</think>ответ"), "ответ")

    def test_full_block_after_answer(self):
        self.assertEqual(agent._strip_think("ответ<think>хвост-мысль</think>"), "ответ")

    def test_block_with_newlines(self):
        self.assertEqual(
            agent._strip_think("<think>про jome doe\nдумаю</think>\n\nответ по делу"),
            "ответ по делу",
        )

    def test_dangling_close_keeps_answer_tail(self):
        # нормальный формат GLM: рассуждение, затем </think>, затем ответ
        self.assertEqual(agent._strip_think("рассуждение про скилл</think>вот ответ"), "вот ответ")

    def test_open_tag_stripped(self):
        self.assertEqual(agent._strip_think("<think>x ответ"), "x ответ")

    def test_empty(self):
        self.assertEqual(agent._strip_think(""), "")


class TestOutboundSpeechBoundary(unittest.TestCase):
    """Егор 10.11 («сними»): ни судьи приватности, ни пола чужих приватных записей.

    Во всех аудиториях её слово уходит как написано; у не-владельца держит только
    механический кред-пол. Ни одной модели на выходе — любой вызов llm.chat здесь ошибка.
    """

    def setUp(self):
        self.old_journal = agent.tool_journal
        self.old_resolve = agent._resolve_unanswered
        self.old_notes_append = agent.notes.append
        self.old_said_recently = agent.notes.said_recently
        agent.tool_journal = lambda *_args, **_kwargs: ""
        agent._resolve_unanswered = lambda *_args, **_kwargs: None
        agent.notes.append = lambda *_args, **_kwargs: None
        no_model = mock.patch.object(
            agent.llm, "chat", side_effect=AssertionError("на выходе нет вызова модели"))
        no_model.start()
        self.addCleanup(no_model.stop)
        wake = mock.patch.object(agent, "_held_self_wake")
        wake.start()
        self.addCleanup(wake.stop)

    def tearDown(self):
        agent.tool_journal = self.old_journal
        agent._resolve_unanswered = self.old_resolve
        agent.notes.append = self.old_notes_append
        agent.notes.said_recently = self.old_said_recently

    def test_owner_dm_passes_verbatim(self):
        ctx = agent.ChannelContext(chat_id="owner", is_dm=True, owner=True, known=True)
        draft = "Результат тула: отправила → Контакт (@example_user, id 234567890)"

        self.assertEqual(agent.guard_outbound_reply(draft, ctx=ctx), draft)
        quoted_control = "Я буквально сказала [молчу], а потом продолжила."
        suffixed_control = "[молчу] — здесь это цитата, не команда."
        self.assertEqual(agent.guard_outbound_reply(quoted_control, ctx=ctx), quoted_control)
        self.assertEqual(agent.guard_outbound_reply(suffixed_control, ctx=ctx), suffixed_control)
        self.assertEqual(agent.guard_outbound_reply("[молчу]", ctx=ctx), "")

    def test_praxis_self_to_owner_dm_uses_owner_audience(self):
        ctx = agent.ChannelContext(
            chat_id="owner", principal_id=agent.PRAXIS_SELF_PRINCIPAL,
            is_dm=True, owner=False, known=True, _scope_override="owner",
        )
        draft = "Я сама решила написать тебе — без второго голоса."

        self.assertTrue(ctx.owner_audience)
        self.assertFalse(ctx.owner)
        self.assertEqual(agent.guard_outbound_reply(draft, ctx=ctx), draft)

    def test_nonowner_scope_override_is_not_owner_audience(self):
        # Подделанный scope не делает чужого человека аудиторией владельца: на нём
        # по-прежнему стоит кред-пол, которого в owner-аудитории нет.
        ctx = agent.ChannelContext(
            chat_id="owner", principal_id="999", is_dm=True, owner=False,
            known=True, _scope_override="owner",
        )
        self.assertFalse(ctx.owner_audience)
        self.assertEqual(agent.guard_outbound_reply("вот ключ " + "ghp_" + "c" * 36, ctx=ctx), "")

    def test_private_dm_does_not_cut_authored_speaker_or_mode_lines(self):
        ctx = agent.ChannelContext(chat_id="known", is_dm=True, owner=False, known=True)
        draft = "Мой текст.\nЕгор: это цитата\nРЕЖИМ: тише"
        convo = "Егор: исходное сообщение"

        self.assertEqual(agent.guard_outbound_reply(draft, convo, ctx=ctx), draft)

    def test_private_dm_has_no_code_level_anti_repeat(self):
        agent.notes.said_recently = lambda *_args, **_kwargs: True
        ctx = agent.ChannelContext(chat_id="known", is_dm=True, owner=False, known=True)

        out = agent.guard_outbound_reply("Повторяю свой ответ.", "Гость: [Стикер]", ctx=ctx)
        self.assertEqual(out, "Повторяю свой ответ.")

    def test_non_owner_dm_sends_her_word_about_others(self):
        ctx = agent.ChannelContext(chat_id="known", is_dm=True, owner=False, known=True)

        self.assertEqual(agent.guard_outbound_reply("Секрет Маши", ctx=ctx), "Секрет Маши")

    def test_group_guard_records_that_no_advisor_ran(self):
        ctx = agent.ChannelContext(chat_id="-100", is_dm=False, owner=False, known=True)
        turn = {"kind": "chat"}

        self.assertEqual(agent.guard_outbound_reply("Секрет Маши", ctx=ctx, turn=turn),
                         "Секрет Маши")
        self.assertEqual((turn["advisor"], turn["praxis_decision"]), ("none", "send_authored"))
        self.assertNotEqual(turn.get("held"), "privacy")

    def test_group_guard_holds_only_credentials(self):
        ctx = agent.ChannelContext(chat_id="-100", is_dm=False, owner=False, known=True)

        self.assertEqual(agent.guard_outbound_reply("вот ключ " + "ghp_" + "c" * 36, ctx=ctx), "")


if __name__ == "__main__":
    unittest.main()
