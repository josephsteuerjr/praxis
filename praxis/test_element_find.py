# -*- coding: utf-8 -*-
"""Контракт read-only руки `find_elements` между агентом, клиентом и Windows-телом."""
from __future__ import annotations

import unittest
from unittest import mock

import body_client


class Payload(unittest.TestCase):
    def call(self, **kwargs):
        seen = {}

        def fake(capability, payload, **rest):
            seen.update(capability=capability, payload=payload, rest=rest)
            return {"ok": True, "matched": 1, "shown": 1, "elements": []}

        with mock.patch.object(body_client, "call", fake):
            body_client.desktop_element_find(**kwargs)
        return seen

    def test_selector_limits_and_transport_timeout_travel(self):
        seen = self.call(hwnd="0x42", automation_id=" save ", role="button",
                         name=" OK ", name_contains=" Сохр ", value_contains=" value ",
                         limit=7, timeout_ms=1200, max_nodes=90, max_depth=6)
        self.assertEqual(seen["capability"], "desktop.element.find")
        self.assertEqual(seen["payload"], {
            "hwnd": "0x42",
            "select": {"automation_id": " save ", "role": "button", "name": " OK ",
                       "name_contains": " Сохр ", "value_contains": " value "},
            "limit": 7, "timeout_ms": 1200, "max_nodes": 90, "max_depth": 6,
        })
        self.assertGreaterEqual(seen["rest"]["timeout"], 21.2)

    def test_blank_selector_fields_are_omitted_but_exact_identity_is_not_trimmed(self):
        seen = self.call(automation_id="  ", name=" exact ")
        self.assertEqual(seen["payload"]["select"], {"name": " exact "})
        self.assertEqual(seen["payload"]["timeout_ms"], 0)


class Formatting(unittest.TestCase):
    def test_success_and_partial_not_found_keep_their_evidence(self):
        said = body_client.format_element_find({
            "ok": True, "matched": 3, "shown": 1, "truncated": True,
            "searched_whole_window": False, "elements": [{"role": "button"}],
            "nodes_scanned": 400, "waited_ms": 20, "polls": 1,
        })
        self.assertIn("нашлось 3", said)
        self.assertIn("показаны первые 1", said)
        missed = body_client.format_element_find({
            "ok": False, "reason": "search_incomplete", "matched": 0,
            "searched_whole_window": False, "nodes_scanned": 400,
            "waited_ms": 2000, "polls": 1, "hint": "raise limits",
        })
        self.assertIn("search_incomplete", missed)
        self.assertIn("raise limits", missed)

    def test_non_dict_transport_failure_is_named(self):
        self.assertIn("не ответил", body_client.format_element_find(None))
        self.assertIn("не ответил", body_client.format_element_find("сломалось"))


class AgentBridge(unittest.TestCase):
    def test_find_requires_apps_grant_before_transport(self):
        import agent
        with mock.patch.object(agent, "_computer_allowed", return_value=False), \
                mock.patch.object(body_client, "desktop_element_find") as transport:
            said = agent.tool_computer("find_elements", role="button")
        self.assertIn("computer.apps", said)
        transport.assert_not_called()

    def test_system_desktop_is_rejected_even_for_sovereign(self):
        import agent
        with mock.patch.object(agent, "_computer_allowed", return_value=True), \
                mock.patch.object(agent, "_is_sovereign_actor", return_value=True), \
                mock.patch.object(body_client, "desktop_element_find") as transport:
            said = agent.tool_computer("find_elements", role="button", execution="system")
        self.assertIn("interactive", said)
        transport.assert_not_called()

    def test_arguments_reach_the_find_client(self):
        import agent
        with mock.patch.object(agent, "_computer_allowed", return_value=True), \
                mock.patch.object(body_client, "desktop_element_find", return_value={}) as find, \
                mock.patch.object(body_client, "format_element_find", return_value="found"):
            said = agent.tool_computer(
                "find_elements", hwnd="0x42", automation_id="id", role="button",
                name="name", name_contains="needle", text_contains="value",
                element_limit=8, timeout_ms=900, max_nodes=80, max_depth=7,
            )
        self.assertEqual(said, "found")
        find.assert_called_once_with(
            hwnd="0x42", automation_id="id", role="button", name="name",
            name_contains="needle", value_contains="value", limit=8,
            timeout_ms=900, max_nodes=80, max_depth=7, execution="interactive",
        )

    def test_schema_scope_and_enum_stay_in_sync(self):
        import agent
        self.assertEqual(agent._COMPUTER_ACTION_SCOPES["find_elements"], "computer.apps")
        actions = agent.COMPUTER_TOOL["input_schema"]["properties"]["action"]["enum"]
        self.assertIn("find_elements", actions)
        properties = agent.COMPUTER_TOOL["input_schema"]["properties"]
        self.assertIn("element_limit", properties)


if __name__ == "__main__":
    unittest.main()
