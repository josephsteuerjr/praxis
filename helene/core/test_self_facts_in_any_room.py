"""Она знает о себе в ЛЮБОЙ комнате, а чужое остаётся владельцу.

ЖИВОЙ СЛУЧАЙ 13.08.2026, AbstractDL. Её спросили про архитектуру и модель. Она ответила
«DeepSeek V4 Pro» и выдала общий список агентных рисков как аудит себя. Замер кадра того
хода: `brain_configuration` — НЕТ, `capability_description` — НЕТ, `identity_continuity` —
НЕТ, слова `terra` нет вовсе. Слово `DeepSeek` в кадре БЫЛО — из чужих сообщений ленты.

Она не «поленилась позвать инструмент». Ей не дали ни одного факта о себе, зато дали чужой
разговор про модели, и она ответила единственным именем, которое видела.
"""
from __future__ import annotations

import unittest

import agent


class SheKnowsHerselfEverywhere(unittest.TestCase):
    def test_the_exception_list_is_short_and_about_other_people(self):
        """Правило от обратного: она знает о себе всё, исключения — про ДРУГИХ.

        Белый список («что ей можно») молча растёт на всякий случай; чёрный требует
        назвать, чьё это и почему. Первая редакция была белой и закрыла ей восемь фактов
        о ней самой — счётчики, её перезапуск, её предложения, её последний ход.
        """
        self.assertEqual(
            set(agent.NOT_HERS_LABELS),
            {"unanswered_dm_participants", "undelivered_media",
             "owner_absence_schedule_note", "appetite_owner_request"})

    def test_a_non_owner_room_still_gets_only_facts_about_her(self):
        block = agent.build_state_evidence_block(self_only=True)
        for label in agent.NOT_HERS_LABELS:
            self.assertNotIn('"label":"%s"' % label, block.replace(" ", ""),
                             "чужое или владельческое уехало в чужую комнату: %s" % label)

    def test_the_owner_room_is_unchanged(self):
        """Правка ничего не отнимает: у владельца кадр прежний."""
        full = agent.build_state_evidence_block(self_only=False)
        narrow = agent.build_state_evidence_block(self_only=True)
        self.assertGreaterEqual(len(full), len(narrow))
        for label in ("brain_configuration", "capability_description",
                      "identity_continuity", "self_git_recent_commits"):
            if '"label":"%s"' % label in narrow.replace(" ", ""):
                self.assertIn('"label":"%s"' % label, full.replace(" ", ""),
                              "факт о себе пропал у владельца: %s" % label)

    def test_every_self_fact_that_exists_reaches_a_group(self):
        narrow = agent.build_state_evidence_block(self_only=True).replace(" ", "")
        full = agent.build_state_evidence_block(self_only=False).replace(" ", "")
        for label in ("brain_configuration", "capability_description",
                      "identity_continuity", "self_git_recent_commits",
                      "runner_counters_now", "restart_reason",
                      "selfdev_pending_proposals"):
            tag = '"label":"%s"' % label
            if tag in full:
                self.assertIn(tag, narrow,
                              "факт о себе не доехал в группу: %s" % label)

    def test_the_gate_is_not_the_speaker_anymore(self):
        """Сторож формулировки: знание о себе не имеет права зависеть от собеседника."""
        import pathlib
        source = pathlib.Path(agent.__file__).read_text(encoding="utf-8")
        i = source.index("state_evidence = build_state_evidence_block")
        head = source[max(0, i - 400):i]
        self.assertNotIn("if owner_context:", head.split("\n")[-3:][0] if head else "",
                         "блок снова спрятан за owner_context")
        self.assertIn("self_only=not owner_context",
                      source[i:i + 300], "сужение потеряно — в группу уедет чужое")


if __name__ == "__main__":
    unittest.main()
