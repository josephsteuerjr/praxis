"""Руки → указатели (12.09, КЕАТ 17.08): родные со схемой, остальные строкой; describe/call.

Запуск: python praxis_test.py test_tool_pointers -v
"""
from __future__ import annotations

import json
import os
import re
import types
import unittest
from unittest import mock

from test_layer7 import Base, FakeResp  # noqa: E402 — тот же герметичный стенд

import agent  # noqa: E402
import frame_trace  # noqa: E402
import llm  # noqa: E402


def _names(tools):
    return [t.get("name") or t.get("type") for t in tools]


class PointerBase(Base):
    def setUp(self):
        super().setUp()
        self._lever = os.environ.pop("PRAXIS_TOOLS_POINTERS", None)
        self.addCleanup(self._restore_lever)

    def _restore_lever(self):
        if self._lever is None:
            os.environ.pop("PRAXIS_TOOLS_POINTERS", None)
        else:
            os.environ["PRAXIS_TOOLS_POINTERS"] = self._lever


class TestOffered(PointerBase):
    def test_owner_gets_natives_plus_dispatcher_only(self):
        ctx = agent.ChannelContext.from_legacy("777", is_dm=True, owner=True, known=True, scope="owner")
        offered = agent.offered_tools_for(ctx)
        names = _names(offered)
        import work_loop
        if work_loop.reply_hand_enabled():
            self.assertIn("reply", names)
            self.assertEqual(names[-1], "end_turn", "закрывающая рука остаётся последней")
        self.assertIn("recall", names)
        self.assertIn("describe", names)
        self.assertIn("call", names)
        # 13.09: shell и руки, которыми она живёт (fs_*, coding_*, group_context…), — родные
        # по замеру использования; указателем едут редкие.
        self.assertIn("shell", names, "shell — родная по замеру 11–12.09")
        self.assertIn("coding_agent", names)
        self.assertNotIn("set_avatar", names, "редкая рука едет указателем, не схемой")
        self.assertNotIn("manage_appetite", names)
        self.assertNotIn("computer", names, "схема computer — 9 тыс. знаков, едет указателем")
        for n in names:
            self.assertTrue(n in agent.NATIVE_HAND_NAMES or n in ("describe", "call")
                            or "web_search" in str(n), n)
        self.assertLess(len(json.dumps(offered, ensure_ascii=False)), 30000,
                        "родные руки со схемами — ~24 тыс. знаков против 70 тыс. манифеста")
        # 22.09: PRAXIS_TEST прячет от песочницы reply(721)+end_turn(990)+web_search(68),
        # и бюджет дважды «чинили» по зелёному тесту при красном бое. Мерим и боевой состав.
        import os
        import work_loop
        if not work_loop.reply_hand_enabled():
            # catalog_tools_for честно вынимает reply/end_turn при опущенном рычаге —
            # добираем их каталогом с рычагом, временно поднятым, а не KeyError-ом.
            os.environ["PRAXIS_CHAT_REPLY_HAND"] = "on"
            try:
                by_name = {str(t.get("name") or ""): t
                           for t in agent.catalog_tools_for(ctx)}
            finally:
                os.environ.pop("PRAXIS_CHAT_REPLY_HAND", None)
            live = list(offered) + [by_name[n] for n in ("reply", "end_turn")]
            live.append({"type": "web_search"})
            self.assertLess(len(json.dumps(live, ensure_ascii=False)), 30000,
                            "боевой состав (с reply/end_turn/web_search) — тоже под 30000")

    def test_catalog_keeps_the_full_hand_set(self):
        owner = agent.ChannelContext.from_legacy("777", is_dm=True, owner=True, known=True, scope="owner")
        own = agent.ChannelContext.from_legacy(None, is_dm=True, owner=False, known=True, scope="owner")
        owner_names = _names(agent.catalog_tools_for(owner))
        self_names = _names(agent.catalog_tools_for(own))
        self.assertIn("shell", owner_names)
        self.assertIn("admit", owner_names)
        self.assertNotIn("admit", self_names, "admit — только человеческому владельцу")
        self.assertGreater(len(owner_names), 60)

    def test_lever_off_restores_the_full_manifest(self):
        os.environ["PRAXIS_TOOLS_POINTERS"] = "off"
        ctx = agent.ChannelContext.from_legacy("777", is_dm=True, owner=True, known=True, scope="owner")
        self.assertEqual(_names(agent.offered_tools_for(ctx)), _names(agent.catalog_tools_for(ctx)))
        self.assertNotIn("call", _names(agent.offered_tools_for(ctx)))


class TestPointerText(PointerBase):
    def test_pointer_lists_every_non_native_hand_once_with_a_purpose(self):
        ctx = agent.ChannelContext.from_legacy("777", is_dm=True, owner=True, known=True, scope="owner")
        catalog = agent.catalog_tools_for(ctx)
        text = agent.hands_pointer_text(catalog)
        self.assertIn("## Мои руки — указатель", text)
        for tool in catalog:
            name = tool.get("name")
            if not name:
                continue
            if name in agent.NATIVE_HAND_NAMES:
                self.assertNotRegex(text, rf"(?m)[;:] {name}\(", f"{name} родная, в списке ей не место")
            else:
                self.assertEqual(len(re.findall(rf"(?m)(?:^|[;:] ){re.escape(name)}\(", text)), 1, name)
        self.assertEqual(text, agent.hands_pointer_text(list(catalog)), "детерминирован")
        self.assertLess(len(text), 12000, "указатель — ~10 тыс. знаков с сигнатурами, не манифест")

    def test_pointer_rows_carry_signatures_so_call_needs_no_describe(self):
        """13.09: сигнатура в строке указателя — обязательные без знака, необязательные с «?»."""
        tool = {"name": "fs_write", "description": "Создать файл.",
                "input_schema": {"type": "object",
                                 "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                                "overwrite": {"type": "boolean"}, "force": {"type": "boolean"}},
                                 "required": ["path", "content"]}}
        self.assertEqual(agent._hand_signature(tool), "fs_write(path, content, overwrite?, force?)")
        self.assertEqual(agent._hand_signature({"name": "my_agenda", "input_schema": {"type": "object", "properties": {}}}),
                         "my_agenda()")
        many = {"name": "x", "input_schema": {"type": "object", "properties": {f"a{i}": {} for i in range(9)},
                                              "required": ["a0"]}}
        self.assertEqual(agent._hand_signature(many), "x(a0, a1?, a2?, a3?, a4?, a5?, …)")
        text = agent.hands_pointer_text([tool, {"name": "reply", "input_schema": {}}])
        self.assertIn("fs_write(path, content, overwrite?, force?) — ", text)
        self.assertIn("СРАЗУ", text, "указатель говорит звать call без describe")
        self.assertNotIn("reply(", text, "родная в списке не повторяется")

    def test_native_set_is_a_lever_with_a_fixed_core(self):
        """PRAXIS_NATIVE_HANDS задаёт состав родных; ядро (reply/end_turn/describe/call…) не снимается."""
        with mock.patch.dict(os.environ, {"PRAXIS_NATIVE_HANDS": "shell, fs_read"}):
            got = agent._native_hands()
        self.assertEqual(got, agent._NATIVE_CORE | {"shell", "fs_read"})
        with mock.patch.dict(os.environ, {"PRAXIS_NATIVE_HANDS": ""}):
            self.assertEqual(agent._native_hands(), agent._NATIVE_CORE, "пустой рычаг = только ядро")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_NATIVE_HANDS", None)
            self.assertEqual(agent._native_hands(), agent._NATIVE_CORE | agent._NATIVE_BY_USE)

    def test_every_catalog_hand_has_a_curated_purpose(self):
        ctx = agent.ChannelContext.from_legacy("777", is_dm=True, owner=True, known=True, scope="owner")
        missing = [t["name"] for t in agent.catalog_tools_for(ctx)
                   if t.get("name") and t["name"] not in agent.HAND_PURPOSE]
        self.assertEqual(missing, [], "каждой руке — выверенная строка назначения (слово Егора 12.09)")

    def test_roster_knows_the_section(self):
        self.assertIn("contract.hands_pointer", frame_trace.SYSTEM_ROSTER)


class TestDispatch(PointerBase):
    def _ctx(self):
        return agent.ChannelContext.from_legacy("777", is_dm=True, owner=True, known=True, scope="owner")

    def test_call_is_rewritten_into_the_inner_hand(self):
        with agent._bind_tool_catalog(self._ctx()):
            block = {"type": "tool_use", "id": "t1", "name": "call",
                     "input": {"name": "fs_read", "args_json": json.dumps({"path": "soul/SOUL.md"})}}
            out, dispatched, note = agent._unwrap_dispatch(block)
        self.assertIsNone(note)
        self.assertTrue(dispatched)
        self.assertEqual(out["name"], "fs_read")
        self.assertEqual(out["input"], {"path": "soul/SOUL.md"})
        self.assertEqual(out["id"], "t1", "результат вяжется к её же вызову")

    def test_unknown_hand_is_refused_with_suggestions_not_an_exception(self):
        with agent._bind_tool_catalog(self._ctx()):
            block = {"type": "tool_use", "id": "t1", "name": "call",
                     "input": {"name": "fs_reed", "args_json": "{}"}}
            out, dispatched, note = agent._unwrap_dispatch(block)
        self.assertFalse(dispatched)
        self.assertIn("нет", note)
        self.assertIn("fs_read", note, "похожие имена подсказываются")
        self.assertEqual(out["name"], "call")

    def test_bad_json_is_refused_and_points_to_describe(self):
        with agent._bind_tool_catalog(self._ctx()):
            block = {"type": "tool_use", "id": "t1", "name": "call",
                     "input": {"name": "fs_read", "args_json": "{path: x}"}}
            _out, dispatched, note = agent._unwrap_dispatch(block)
        self.assertFalse(dispatched)
        self.assertIn("describe(fs_read)", note)

    def test_authority_is_the_turn_catalog_not_tool_impl(self):
        own = agent.ChannelContext.from_legacy(None, is_dm=True, owner=False, known=True, scope="owner")
        with agent._bind_tool_catalog(own):
            block = {"type": "tool_use", "id": "t1", "name": "call",
                     "input": {"name": "admit", "args_json": "{}"}}
            _out, dispatched, note = agent._unwrap_dispatch(block)
        self.assertFalse(dispatched, "admit не в каталоге её собственного хода — call не расширяет власть")
        self.assertIn("нет", note)

    def test_lever_off_is_a_passthrough(self):
        os.environ["PRAXIS_TOOLS_POINTERS"] = "off"
        block = {"type": "tool_use", "id": "t1", "name": "call", "input": {"name": "fs_read"}}
        out, dispatched, note = agent._unwrap_dispatch(block)
        self.assertIs(out, block)
        self.assertFalse(dispatched)
        self.assertIsNone(note)

    def test_describe_returns_the_schema_as_a_tool_result(self):
        with agent._bind_tool_catalog(self._ctx()):
            text = agent.tool_describe("fs_read, nosuch_hand")
        self.assertIn('"input_schema"', text)
        self.assertIn('"name": "fs_read"', text)
        self.assertIn("nosuch_hand", text)
        self.assertIn("нет", text)

    def test_dispatcher_schemas_survive_openai_strict_translation(self):
        for tool in (agent.CALL_TOOL, agent.DESCRIBE_TOOL):
            strict = llm._openai_strict_schema(tool["input_schema"])
            self.assertFalse(strict.get("additionalProperties", True))
            self.assertEqual(set(strict["required"]), set(strict["properties"]))


class TestLoopEndToEnd(PointerBase):
    def test_call_executes_the_inner_hand_through_the_same_funnel(self):
        received = {}

        def fake_tool(required_field, optional_field="default"):
            received["required_field"] = required_field
            received["optional_field"] = optional_field
            return "ok-from-fake"

        self._orig.append((agent, "TOOL_IMPL", dict(agent.TOOL_IMPL)))
        agent.TOOL_IMPL["fake_tool"] = fake_tool
        self.offer_test_tool("fake_tool",
                             properties={"required_field": {"type": "string"},
                                         "optional_field": {"type": "string"}},
                             required=("required_field",))

        class _Client:
            def __init__(self):
                self._step = 0
                self.messages = self
                self.calls = []

            def create(_s, **kw):
                _s.calls.append(kw)
                if _s._step == 0:
                    _s._step = 1
                    blk = types.SimpleNamespace(
                        type="tool_use", id="t1", name="call",
                        input={"name": "fake_tool",
                               "args_json": json.dumps({"required_field": "x"})})
                    return types.SimpleNamespace(
                        stop_reason="tool_use", content=[blk],
                        usage=types.SimpleNamespace(input_tokens=5, output_tokens=1))
                return FakeResp("готово")

        client = _Client()
        llm.use_test_client(client)
        with mock.patch.object(agent.serverd_client, "state_line",
                               return_value=None), \
             mock.patch.object(agent.serverd_client, "available",
                               return_value=False), \
             mock.patch.object(agent.body_client, "available",
                               return_value=False):
            agent.voice_turn(None, "Егор: тест", speaker="Егор", is_owner=True)
        self.assertEqual(received.get("required_field"), "x")
        self.assertEqual(received.get("optional_field"), "default")
        offered = _names(client.calls[0].get("tools", []))
        self.assertIn("call", offered)
        self.assertNotIn("fake_tool", offered, "fake_tool едет указателем, не схемой")
        system = client.calls[0].get("system")
        system = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
        self.assertIn("Мои руки — указатель", system)
        self.assertIn("fake_tool(required_field, optional_field?) — ", system)
        second = client.calls[1]["messages"]
        results = [b for m in second if isinstance(m.get("content"), list)
                   for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertTrue(results, "результат внутренней руки вернулся модели")
        self.assertEqual(results[-1]["tool_use_id"], "t1")
        self.assertIn("ok-from-fake", json.dumps(results[-1], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
