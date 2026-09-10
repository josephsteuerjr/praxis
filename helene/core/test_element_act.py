# -*- coding: utf-8 -*-
"""Стенд руки `act_element`: действие над названным элементом, а не над точкой экрана.

Дыра, ради которой глагол написан: между чтением окна и ударом по координатам был
провал. Пиксель, верный секунду назад, указывает мимо, если окно проехало, список
прокрутился или система стоит на другом масштабе; ввод шёл В ФОКУС, то есть «напечатать
в поле пароля» значило «сначала как-нибудь навести фокус и надеяться»; а повторить
вчерашнее действие было нечем — числовые `id` чтения действуют ровно на один ответ.

Здесь проверяется НЕ то, что глагол работает (для этого нужен живой рабочий стол), а то,
что он не врёт:

  * отбор доезжает до тела целиком и без выдуманных умолчаний;
  * три отказа — «подошли несколько», «не нашлось», «нет такого паттерна» — остаются
    ОТВЕТАМИ со своими словами, а не превращаются в «не получилось»;
  * расписка удачи не объявляет цель достигнутой.

Живого тела здесь нет: `body_client.call` подменён.
"""
from __future__ import annotations

import unittest
from unittest import mock

import body_client


class Payload(unittest.TestCase):
    """Что именно уезжает в тело."""

    def call(self, **kwargs) -> dict:
        seen: dict = {}

        def fake(capability, payload, **rest):
            seen["capability"] = capability
            seen["payload"] = payload
            seen["rest"] = rest
            return {"ok": True, "did": kwargs.get("action", "invoke"),
                    "element": {}, "element_after": {}, "waited_ms": 1, "polls": 1}

        with mock.patch.object(body_client, "call", fake):
            body_client.desktop_element_act(**kwargs)
        return seen

    def test_the_selector_travels_whole_and_nothing_is_invented(self):
        seen = self.call(action="invoke", automation_id="saveBtn", role="button",
                         name_contains="Сохран")
        self.assertEqual(seen["capability"], "desktop.element.act")
        self.assertEqual(seen["payload"]["select"],
                         {"automation_id": "saveBtn", "role": "button",
                          "name_contains": "Сохран"})
        self.assertEqual(seen["payload"]["do"], "invoke")

    def test_empty_selector_fields_are_left_out_rather_than_sent_as_emptiness(self):
        """Пустая строка в отборе — это не «совпади с пустым именем», а «не задано».

        Тело отличает отсутствие ключа от пустого значения, и слать `name: ""` значило
        бы просить элемент БЕЗ имени."""
        seen = self.call(action="focus", role="edit", name="", automation_id="   ")
        self.assertEqual(seen["payload"]["select"], {"role": "edit"})

    def test_text_goes_only_with_set_value(self):
        """`text` при других действиях тело отвергает — не молча роняет."""
        seen = self.call(action="set_value", role="edit", text="привет")
        self.assertEqual(seen["payload"]["text"], "привет")
        seen = self.call(action="invoke", name="ОК")
        self.assertNotIn("text", seen["payload"])

    def test_nth_zero_is_a_choice_and_not_an_absence(self):
        """`nth=0` — это «возьми первый», а не «я ничего не сказала».

        Ровно та мина, на которой ломаются проверки `if nth:`: ноль ложный.
        """
        seen = self.call(action="invoke", role="button", nth=0)
        self.assertEqual(seen["payload"]["select"]["nth"], 0)

    def test_we_wait_longer_than_the_body_does(self):
        """Иначе мы бросим трубку ровно тогда, когда оно вот-вот ответит."""
        seen = self.call(action="invoke", role="button", timeout_ms=30_000)
        self.assertGreater(seen["rest"]["timeout"], 30)


class Refusals(unittest.TestCase):
    """Три отказа, которые обязаны остаться ответами со своими словами."""

    def test_several_matches_are_answered_with_the_candidates(self):
        said = body_client.format_element_act({
            "ok": False, "reason": "ambiguous", "matched": 3,
            "candidates": [
                {"role": "button", "name": "ОК", "automation_id": "ok1"},
                {"role": "button", "name": "ОК", "automation_id": "ok2"},
                {"role": "button", "name": "ОК"},
            ],
        })
        self.assertIn("подошли 3", said)
        self.assertIn("ok1", said)
        self.assertIn("ok2", said)
        # Ответ обязан сказать, ЧТО делать дальше, а не только что не так.
        self.assertIn("nth", said)
        # И пронумеровать кандидатов тем же числом, которое просят назвать.
        self.assertIn("nth=0", said)
        self.assertIn("nth=2", said)

    def test_not_found_says_how_long_it_looked_and_where_to_look_next(self):
        said = body_client.format_element_act({
            "ok": False, "reason": "not_found", "matched": 0, "nodes_scanned": 412,
            "waited_ms": 3000, "polls": 25,
            "hint": "read the window (desktop.window.read) to see what is actually there",
        })
        self.assertIn("не нашёлся", said)
        self.assertIn("3000", said)
        self.assertIn("412", said)
        self.assertIn("desktop.window.read", said)

    def test_a_missing_pattern_is_not_dressed_up_as_a_generic_failure(self):
        """Тело называет паттерны, которые у элемента ЕСТЬ, — и это обязано доехать."""
        said = body_client.format_element_act({
            "ok": False,
            "error": "this element does not support InvokePattern; patterns it does "
                     "support: toggle, select",
        })
        self.assertIn("toggle", said)
        self.assertIn("select", said)


class Receipt(unittest.TestCase):

    def test_the_receipt_shows_what_the_element_became_and_claims_nothing_more(self):
        said = body_client.format_element_act({
            "ok": True, "did": "set_value", "mutating": True,
            "element": {"role": "edit", "value": ""},
            "element_after": {"role": "edit", "automation_id": "login",
                              "value": "егор", "state": {"focused": True}},
            "waited_ms": 84, "polls": 1,
        })
        self.assertIn("set_value", said)
        self.assertIn("егор", said)
        self.assertIn("focused", said)
        self.assertIn("84", said)
        # Слова «получилось» здесь нет и быть не должно: вызов паттерна и достигнутая
        # цель — разные вещи, и вывод делает она.
        for lie in ("получилось", "успешно", "готово"):
            self.assertNotIn(lie, said.lower())

    def test_a_body_that_did_not_answer_is_said_out_loud(self):
        self.assertIn("не ответил", body_client.format_element_act(None))
        self.assertIn("не ответил", body_client.format_element_act("сломалось"))


class Hand(unittest.TestCase):
    """Рука в ядре: право, отказ без действия и передача отбора."""

    def test_the_action_carries_the_apps_scope(self):
        import agent
        self.assertEqual(agent._COMPUTER_ACTION_SCOPES.get("act_element"), "computer.apps")

    def test_the_grant_is_checked_before_the_arguments(self):
        """Право — раньше разбора просьбы, и это правильный порядок.

        Отказ по правам не должен зависеть от того, верно ли заполнены поля: иначе
        неверный аргумент рассказывал бы то, чего звонящему знать не положено.
        """
        import agent
        with mock.patch.object(agent, "_computer_allowed", lambda scope: False):
            said = agent.tool_computer("act_element", role="button")
        self.assertIn("computer.apps", said)
        self.assertNotIn("element_action", said)

    def test_without_an_action_the_hand_says_what_it_knows_instead_of_guessing(self):
        import agent
        with mock.patch.object(agent, "_computer_allowed", lambda scope: True):
            said = agent.tool_computer("act_element", role="button")
        self.assertIn("element_action", said)
        for verb in ("invoke", "set_value", "scroll_into_view", "focus"):
            self.assertIn(verb, said)

    def test_the_unknown_action_message_lists_this_verb_too(self):
        """Этот список — карта возможностей для модели: чего в нём нет, того для неё нет."""
        import agent
        with mock.patch.object(agent, "_computer_allowed", lambda scope: True):
            said = agent.tool_computer("такого-действия-нет")
        self.assertIn("act_element", said)


if __name__ == "__main__":
    unittest.main(verbosity=2)
