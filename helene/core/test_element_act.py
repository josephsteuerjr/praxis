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
        seen = self.call(action="focus", role="edit", name="", automation_id="")
        self.assertEqual(seen["payload"]["select"], {"role": "edit"})

    def test_whitespace_exact_selectors_are_never_dropped(self):
        for field in ("automation_id", "name"):
            with self.subTest(field=field):
                seen = self.call(action="focus", role="edit", **{field: " \t "})
                self.assertEqual(seen["payload"]["select"],
                                 {"role": "edit", field: " \t "})

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



class Integration(unittest.TestCase):
    def test_schema_and_grants_include_both_verbs(self):
        import agent
        actions = agent.COMPUTER_TOOL['input_schema']['properties']['action']['enum']
        for action in ('read_window', 'act_element'):
            self.assertIn(action, actions)
            self.assertEqual(agent._COMPUTER_ACTION_SCOPES[action], 'computer.apps')

    def test_both_verbs_refuse_before_transport_without_grant(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=False), \
                mock.patch.object(body_client, 'call') as transport:
            for action in ('read_window', 'act_element'):
                self.assertIn('computer.apps', agent.tool_computer(action))
            transport.assert_not_called()

    def test_system_desktop_is_rejected_even_for_sovereign(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=True), \
                mock.patch.object(agent, '_is_sovereign_actor', return_value=True), \
                mock.patch.object(body_client, 'call') as transport:
            for action in ('read_window', 'act_element'):
                self.assertIn('interactive', agent.tool_computer(action, execution='system'))
            transport.assert_not_called()

    def test_read_window_prerequisite_passes_limits_and_filters(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=True), \
                mock.patch.object(body_client, 'desktop_window_read', return_value={}) as read, \
                mock.patch.object(body_client, 'format_window_read', return_value='tree'):
            self.assertEqual(agent.tool_computer('read_window', hwnd='0x42', shape='flat',
                text_contains='привет', max_nodes=50, max_depth=4, timeout_ms=1000,
                visible_only=False), 'tree')
        read.assert_called_once_with(hwnd='0x42', shape='flat', text_contains='привет',
            visible_only=False, max_nodes=50, max_depth=4, timeout_ms=1000,
            execution='interactive')

    def test_hand_transmits_exact_selector_and_empty_set_value(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=True), \
                mock.patch.object(agent, '_computer_actor', return_value='praxis:self'), \
                mock.patch.object(body_client, 'desktop_element_act', return_value={}) as act:
            agent.tool_computer('act_element', element_action='set_value', hwnd='0x42',
                automation_id='Field', role='edit', nth=0, text='', text_contains='old',
                idempotency_key='test-set-value')
        self.assertEqual(act.call_args.args, ('set_value',))
        self.assertEqual(act.call_args.kwargs['nth'], 0)
        self.assertEqual(act.call_args.kwargs['text'], '')
        self.assertEqual(act.call_args.kwargs['value_contains'], 'old')
        self.assertEqual(act.call_args.kwargs['automation_id'], 'Field')

    def test_partial_search_is_not_reported_as_absence(self):
        for reason in ('timeout', 'max_nodes', 'max_depth'):
            said = body_client.format_element_act(dict(ok=False, reason=reason,
                searched_whole_window=False, nodes_scanned=12, waited_ms=23))
            self.assertIn(reason, said)
            self.assertIn('False', said)
            self.assertIn('12', said)
            self.assertNotIn('не нашёлся', said)

    def test_missing_after_is_not_disguised_as_before(self):
        said = body_client.format_element_act(dict(ok=True, did='invoke',
            element={'value': 'before'}, element_after=None))
        self.assertNotIn('before', said)
        self.assertIn('недоступно', said)


class ExactSelector(unittest.TestCase):
    def test_exact_values_are_not_silently_trimmed(self):
        with mock.patch.object(body_client, 'call', return_value={'ok': False}) as call:
            body_client.desktop_element_act('invoke', automation_id=' save ', name=' OK ')
        self.assertEqual(call.call_args.args[1]['select'],
                         {'automation_id': ' save ', 'name': ' OK '})



class RetryIdentity(unittest.TestCase):
    def test_unkeyed_action_never_calls_body(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=True), \
                mock.patch.object(body_client, 'call') as call:
            said = agent.tool_computer('act_element', element_action='toggle', role='checkbox')
        self.assertIn('idempotency_key', said)
        call.assert_not_called()

    def test_retry_and_changed_intent_address_same_journal_entry(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=True), \
                mock.patch.object(agent, '_computer_actor', return_value='praxis:self'), \
                mock.patch.object(body_client, 'call', return_value={'ok': False}) as call:
            for text in ('a', 'a', 'different'):
                agent.tool_computer('act_element', element_action='set_value', role='edit',
                                    text=text, idempotency_key='stable-test')
        ids = [(c.kwargs['request_id'], c.kwargs['operation_id']) for c in call.call_args_list]
        self.assertEqual(ids, [ids[0]] * 3)
        self.assertTrue(all(ids[0]))
        self.assertNotEqual(call.call_args_list[0].args[1], call.call_args_list[2].args[1])
        # Same IDs/different payload => journal Admission::Conflict, not a new action.

    def test_keys_are_principal_scoped_and_not_marked_auto_replay_safe(self):
        import agent
        with mock.patch.object(agent, '_computer_allowed', return_value=True), \
                mock.patch.object(agent, '_computer_actor', side_effect=['telegram:1', 'telegram:2']), \
                mock.patch.object(body_client, 'call', return_value={'ok': False}) as call:
            for _ in range(2):
                agent.tool_computer('act_element', element_action='toggle', role='checkbox',
                                    idempotency_key='same-client-key')
        self.assertNotEqual(call.call_args_list[0].kwargs['request_id'],
                            call.call_args_list[1].kwargs['request_id'])
        self.assertEqual(agent._tool_idempotency_key(None, 'call', 'computer',
            {'action': 'act_element', 'idempotency_key': 'same-client-key'}), '')

    def test_read_only_classification(self):
        import agent
        self.assertFalse(agent._tool_has_side_effect('computer', {'action': 'read_window'}))
        self.assertTrue(agent._tool_has_side_effect('computer', {'action': 'act_element'}))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class AdmissionConflict(unittest.TestCase):
    def test_success_retry_then_changed_intent_never_polls_stale_success(self):
        # Model the bridge HTTP contract, including its immutable terminal cache.
        # The real Rust handler/spool regression verifies the admission implementation.
        bound = None
        polls = []

        def bridge(method, path, payload=None, **kwargs):
            nonlocal bound
            if method == "POST":
                intent = (payload["operation_id"], payload["execution"],
                          payload["capability"], payload["args"])
                if bound is not None and bound != intent:
                    return {"ok": False, "code": "id_conflict", "error": "changed intent"}
                bound = intent
                return {"ok": True, "request_id": payload["request_id"]}
            polls.append(path)
            return {"ok": True, "response": {"type": "result", "ok": True,
                    "result": {"value": "A"}}}

        with mock.patch.object(body_client, "_request", side_effect=bridge):
            def invoke(text):
                return body_client.call("desktop.element.act",
                    {"do": "set_value", "select": {"automation_id": "field"}, "text": text},
                    request_id="stable-r", operation_id="stable-o")
            self.assertEqual(invoke("A")["value"], "A")
            self.assertEqual(invoke("A")["value"], "A")
            changed = invoke("B")
        self.assertFalse(changed["ok"])
        self.assertEqual(changed["code"], "id_conflict")
        self.assertNotIn("value", changed)
        self.assertEqual(len(polls), 2)
