"""Переключить подписку — её ход, а не инструкция человеку.

ПОЧЕМУ РУКИ НЕ БЫЛО ДО 13.08.2026. Реле держит активный слот В ПАМЯТИ и само переносит
его в файл состояния. Значит правка файлов подписок на живом реле бесполезна: её затрёт
следующая запись. Исполнителя не существовало — существовала только инструкция человеку
сделать это руками, и она лежала у неё в workspace как `ПОДПИСКИ-13.08.md`.

Теперь у реле есть `GET /v1/account` и `POST /v1/account/switch`, а у неё — `switch_brain`
с `accounts` и `use_account`. Ключей она по-прежнему не видит: реле берёт их из своего дома.

⚠ ЧТО ОХРАНЯЮТ ЭТИ ТЕСТЫ. Адрес реле берётся оттуда же, откуда она и так думает
(`memory/llm.json`), — второго источника правды нет. Отказ реле пересказывается ЕГО
словами: подменять их своими значило бы снова отвечать за прибор вместо прибора.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import brain  # noqa: E402


def _relay(reply, *, calls=None):
    def fake(path, payload=None):
        if calls is not None:
            calls.append((path, payload))
        if isinstance(reply, Exception):
            raise reply
        return reply
    return mock.patch.object(brain, "_relay_call", side_effect=fake)


class TheAddressIsTheOneSheAlreadyThinksThrough(unittest.TestCase):
    def test_the_relay_base_comes_from_her_brain_config(self):
        with mock.patch("llm._config", return_value={
                "frameworks": {"openai": {"base_url": "http://host.docker.internal:5012"}}}):
            self.assertEqual(brain._relay_base(), "http://host.docker.internal:5012")

    def test_a_trailing_v1_is_not_doubled(self):
        with mock.patch("llm._config", return_value={
                "frameworks": {"openai": {"base_url": "http://relay:5011/v1"}}}):
            self.assertEqual(brain._relay_base(), "http://relay:5011")

    def test_no_address_is_said_out_loud_rather_than_guessed(self):
        with mock.patch("llm._config", return_value={"frameworks": {}}):
            with self.assertRaises(RuntimeError):
                brain._relay_call("/v1/account")


class SheCanSeeWhichSubscriptionIsLive(unittest.TestCase):
    ANSWER = {
        "active_slot": "primary",
        "configured_slots": 2,
        "slots": [
            {"slot": "primary", "active": True, "cooldown_seconds_left": 0},
            {"slot": "secondary", "active": False, "cooldown_seconds_left": 900},
        ],
    }

    def test_both_slots_and_the_live_one_are_named(self):
        with _relay(self.ANSWER):
            words = brain.accounts()
        self.assertIn("primary ← активна", words)
        self.assertIn("secondary", words)

    def test_a_parked_slot_says_how_long_it_rests(self):
        with _relay(self.ANSWER):
            self.assertIn("отдыхает ещё 15м", brain.accounts())

    def test_an_unreachable_relay_is_a_fact_about_the_channel(self):
        with _relay(ConnectionError("нет связи")):
            words = brain.accounts()
        self.assertIn("факт о канале", words)


class SwitchingIsHerMoveAndTheRefusalIsTheRelaysWords(unittest.TestCase):
    def test_a_successful_switch_names_both_ends(self):
        calls: list = []
        answer = {"previous_slot": "primary", "active_slot": "secondary",
                  "slots": [{"slot": "secondary", "active": True,
                             "cooldown_seconds_left": 0}]}
        with (
            _relay(answer, calls=calls),
            mock.patch.object(brain, "_journal"),
            mock.patch.object(brain, "_spine"),
        ):
            words = brain.use_account("secondary", why="кончился лимит на первой")
        self.assertEqual(calls[0], ("/v1/account/switch", {"slot": "secondary"}))
        self.assertIn("«primary» → «secondary»", words)
        self.assertIn("без перезапуска", words)

    def test_the_reason_reaches_her_journal_and_her_spine(self):
        answer = {"previous_slot": "primary", "active_slot": "secondary", "slots": []}
        with (
            _relay(answer),
            mock.patch.object(brain, "_journal") as journal,
            mock.patch.object(brain, "_spine") as spine,
        ):
            brain.use_account("secondary", why="считаю замер, беру свежую квоту")
        self.assertIn("primary → secondary", journal.call_args[0][0])
        self.assertEqual(spine.call_args[0][1]["kind"], "account_switch")

    def test_an_unconfigured_slot_is_refused_in_the_relays_own_words(self):
        answer = {
            "error": {"message": 'account slot "tertiary" is not configured '
                                 '(configured: primary, secondary)',
                      "code": "switch_refused"},
            "active_slot": "primary",
            "slots": [{"slot": "primary", "active": True, "cooldown_seconds_left": 0}],
        }
        with _relay(answer):
            words = brain.use_account("tertiary")
        self.assertIn("не configured".replace("не ", "not "), words)
        self.assertIn("Осталась «primary»", words)

    def test_switching_to_the_live_slot_says_nothing_changed(self):
        answer = {"previous_slot": "primary", "active_slot": "primary", "slots": []}
        with (
            _relay(answer),
            mock.patch.object(brain, "_journal"),
            mock.patch.object(brain, "_spine"),
        ):
            self.assertIn("Уже была «primary»", brain.use_account("primary"))

    def test_an_empty_slot_name_asks_instead_of_calling_the_relay(self):
        calls: list = []
        with _relay({}, calls=calls):
            words = brain.use_account("  ")
        self.assertEqual(calls, [])
        self.assertIn("primary", words)

    def test_a_dead_relay_leaves_the_subscription_where_it_was(self):
        with _relay(TimeoutError("нет ответа")):
            words = brain.use_account("secondary")
        self.assertIn("не менялась", words)


class TheHandIsOfferedAndGuarded(unittest.TestCase):
    def test_the_schema_declares_both_new_actions(self):
        import agent
        spec = next(t for t in agent.BASE_TOOLS if t["name"] == "switch_brain")
        actions = spec["input_schema"]["properties"]["action"]["enum"]
        self.assertIn("accounts", actions)
        self.assertIn("use_account", actions)

    def test_switching_needs_a_recognised_principal_exactly_like_the_model_switch(self):
        import agent
        with (
            mock.patch.object(agent, "_is_sovereign_actor", return_value=False),
            mock.patch.object(agent, "_active_scope", return_value="group"),
            mock.patch.object(agent, "_active_principal", return_value=""),
            mock.patch.object(agent.rails, "deny"),
            mock.patch.object(brain, "use_account",
                              side_effect=AssertionError("дошло до реле")),
        ):
            words = agent.tool_switch_brain("use_account", model="secondary")
        self.assertIn("как принципал", words)

    def test_reading_which_subscription_is_live_needs_no_principal(self):
        """Знание о себе — не действие. Тот же разворот, что 13.08 в кадре."""
        import agent
        with (
            mock.patch.object(agent, "_is_sovereign_actor", return_value=False),
            mock.patch.object(agent, "_active_scope", return_value="group"),
            mock.patch.object(brain, "accounts", return_value="Подписки: primary ← активна."),
        ):
            self.assertIn("primary", agent.tool_switch_brain("accounts"))


if __name__ == "__main__":
    unittest.main()
