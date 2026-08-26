from __future__ import annotations

import types
import unittest
from unittest import mock

import agent


class TerminalToolLoopTests(unittest.TestCase):
    def test_provider_web_search_survives_function_schema_validation(self):
        response = types.SimpleNamespace(
            stop_reason="end_turn", text="searched", blocks=[],
        )
        tools = [
            {"name": "probe", "input_schema": {"type": "object"}},
            dict(agent.OPENAI_WEB_SEARCH_TOOL),
        ]
        with mock.patch.object(agent.llm, "chat", return_value=response) as chat:
            out = agent._terminal_tool_loop(
                system="test", messages=[], tools=tools, max_iters=None,
            )
        self.assertEqual(out, "searched")
        self.assertEqual(chat.call_args.kwargs["tools"], tools)

    def test_hourly_self_toolset_accepts_native_search_descriptor(self):
        local_tools = (
            list(agent.BASE_TOOLS)
            + list(agent.SHARED_CONTEXT_TOOLS)
            + list(agent.PRAXIS_SELF_TOOLS)
        )
        tools = local_tools + [dict(agent.OPENAI_WEB_SEARCH_TOOL)]
        names = agent._offered_function_names(tools)
        self.assertEqual(names, {tool["name"] for tool in local_tools})
        self.assertIn("computer", names)
        self.assertIn("inbox_list", names)
        self.assertIn("inbox_read", names)
        self.assertNotIn("web_search", names)

    def test_provider_web_search_cannot_alias_a_local_function(self):
        tools = [
            dict(agent.OPENAI_WEB_SEARCH_TOOL),
            {"name": "web_search", "input_schema": {"type": "object"}},
        ]
        with self.assertRaisesRegex(
            agent.DurableExecutionError, r"tool\[1\] is malformed or duplicated"
        ):
            agent._terminal_tool_loop(system="test", messages=[], tools=tools)

    def test_real_loop_can_exceed_legacy_six_hundred_calls(self):
        calls = 0
        seen_messages = []

        def fake_chat(*_args, **kwargs):
            nonlocal calls
            seen_messages.append(kwargs.get("messages"))
            if calls < 605:
                calls += 1
                return types.SimpleNamespace(
                    stop_reason="tool_use", text="", blocks=[{
                        "type": "tool_use", "id": f"t{calls}",
                        "name": "probe", "input": {"value": "same"},
                    }],
                )
            return types.SimpleNamespace(stop_reason="end_turn", text="done", blocks=[])

        with mock.patch.object(agent.llm, "chat", side_effect=fake_chat), \
                mock.patch.dict(agent.TOOL_IMPL, {"probe": lambda value: "same-result"}):
            out = agent._terminal_tool_loop(
                system="test", messages=[{"role": "user", "content": "go"}],
                tools=[{"name": "probe"}], max_iters=None,
            )

        self.assertEqual(out, "done")
        self.assertEqual(calls, 605)
        flattened = str(seen_messages[-1])
        self.assertIn("repeated 600 times", flattened)
        self.assertIn("does not stop the run", flattened)

    def test_explicit_auxiliary_limit_still_gets_one_final_answer(self):
        responses = [
            types.SimpleNamespace(stop_reason="tool_use", text="", blocks=[{
                "type": "tool_use", "id": "t1", "name": "probe", "input": {},
            }]),
            types.SimpleNamespace(stop_reason="end_turn", text="bounded-final", blocks=[]),
        ]
        with mock.patch.object(agent.llm, "chat", side_effect=responses) as chat, \
                mock.patch.dict(agent.TOOL_IMPL, {"probe": lambda: "ok"}):
            out = agent._terminal_tool_loop(
                system="test", messages=[], tools=[{"name": "probe"}], max_iters=1,
            )
        self.assertEqual(out, "bounded-final")
        self.assertEqual(chat.call_count, 2)

    def test_visual_tool_observation_is_attached_to_next_model_step(self):
        observed = []
        responses = [
            types.SimpleNamespace(stop_reason="tool_use", text="", blocks=[{
                "type": "tool_use", "id": "eyes-1", "name": "eyes", "input": {},
            }]),
            types.SimpleNamespace(stop_reason="end_turn", text="I can see it", blocks=[]),
        ]

        def fake_chat(*_args, **kwargs):
            observed.append(kwargs["messages"])
            return responses.pop(0)

        pixels = agent.ToolObservation(
            "verified pixels", images=({
                "type": "image", "path": "C:/tmp/observation.png",
                "mime": "image/png", "detail": "auto",
            },),
        )
        with mock.patch.object(agent.llm, "chat", side_effect=fake_chat), \
                mock.patch.dict(agent.TOOL_IMPL, {"eyes": lambda: pixels}):
            out = agent._terminal_tool_loop(
                system="test", messages=[], tools=[{"name": "eyes"}],
            )

        self.assertEqual(out, "I can see it")
        blocks = observed[-1][-1]["content"]
        self.assertEqual(blocks[0]["type"], "tool_result")
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["mime"], "image/png")

    def test_stale_observe_screenshots_are_stubbed_after_two_live(self):
        # Раньше каждый кол нёс ВСЕ прежние скрины (линейный рост запроса);
        # живыми должны оставаться два последних observe-скрина, и только они.
        def image(name, origin="computer-observe"):
            block = {"type": "image", "path": f"C:/runs/a/artifacts/{name}",
                     "mime": "image/png", "detail": "auto"}
            if origin:
                block["origin"] = origin
            return block

        telegram_photo = image("from_egor.png", origin=None)
        messages = [
            {"role": "user", "content": [telegram_photo, {"type": "text", "text": "смотри"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1",
                                          "content": "ok"}, image("shot1.png")]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2",
                                          "content": "ok"}, image("shot2.png")]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "3",
                                          "content": "ok"}, image("shot3.png")]},
        ]
        agent._prune_stale_screenshots(messages)
        self.assertEqual(messages[0]["content"][0], telegram_photo)  # чужие фото не трогаем
        stub = messages[1]["content"][1]
        self.assertEqual(stub["type"], "text")
        self.assertIn("shot1.png", stub["text"])
        self.assertEqual(messages[2]["content"][1]["type"], "image")
        self.assertEqual(messages[3]["content"][1]["type"], "image")

    def test_loop_prunes_third_screenshot_end_to_end(self):
        observed = []
        responses = [
            types.SimpleNamespace(stop_reason="tool_use", text="", blocks=[{
                "type": "tool_use", "id": f"eyes-{i}", "name": "eyes", "input": {},
            }]) for i in range(3)
        ] + [types.SimpleNamespace(stop_reason="end_turn", text="done", blocks=[])]

        def fake_chat(*_args, **kwargs):
            observed.append([list(m["content"]) if isinstance(m.get("content"), list)
                             else m.get("content") for m in kwargs["messages"]])
            return responses.pop(0)

        counter = {"n": 0}

        def eyes():
            counter["n"] += 1
            return agent.ToolObservation(
                "verified pixels", images=({
                    "type": "image", "path": f"C:/tmp/obs{counter['n']}.png",
                    "mime": "image/png", "detail": "auto", "origin": "computer-observe",
                },),
            )

        with mock.patch.object(agent.llm, "chat", side_effect=fake_chat), \
                mock.patch.dict(agent.TOOL_IMPL, {"eyes": eyes}):
            out = agent._terminal_tool_loop(
                system="test", messages=[], tools=[{"name": "eyes"}],
            )

        self.assertEqual(out, "done")
        final = observed[-1]
        images = [b["path"] for row in final if isinstance(row, list)
                  for b in row if isinstance(b, dict) and b.get("type") == "image"]
        stubs = [b for row in final if isinstance(row, list)
                 for b in row if isinstance(b, dict) and b.get("type") == "text"
                 and "устарел" in str(b.get("text"))]
        self.assertEqual(images, ["C:/tmp/obs2.png", "C:/tmp/obs3.png"])
        self.assertEqual(len(stubs), 1)

    def test_model_view_image_falls_back_to_png_without_pillow(self):
        import builtins
        real_import = builtins.__import__

        def no_pil(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("no pillow in this build")
            return real_import(name, *args, **kwargs)

        from pathlib import Path
        with mock.patch.object(builtins, "__import__", side_effect=no_pil):
            path, mime = agent._model_view_image(Path("C:/tmp/shot.png"))
        self.assertEqual(str(path), str(Path("C:/tmp/shot.png")))
        self.assertEqual(mime, "image/png")


class MalformedArgumentsBecomeARepairTurn(unittest.TestCase):
    """Битый JSON аргументов — оборот починки, а не тихое другое действие.

    ⚠ Прод живёт на реле, где обрыв длинных `arguments` реален. `blocks_from_openai`
    подменял непрочитанные аргументы пустым словарём, и рука со всеми опциональными
    параметрами (`check_email`, `my_agenda`, `recent_turns`) исполнялась с дефолтами:
    вместо ошибки — ДРУГОЕ действие, а намерение модели терялось без следа.
    """

    def _torn_call_blocks(self, name="check_email", raw='{"unread_only": tru'):
        """Ровно то, что приходит с реле: обрыв строки `arguments` в tool_call.

        Блок строится ТЕМ ЖЕ парсером, что и в бою, — иначе тест проверял бы
        собственную выдумку, а не путь модели.
        """
        call = types.SimpleNamespace(id="t1", function=types.SimpleNamespace(
            name=name, arguments=raw))
        blocks = agent.llm.blocks_from_openai(
            types.SimpleNamespace(content=None, tool_calls=[call]))
        self.assertNotEqual(blocks[0]["input"], {},
                            "битые аргументы снова стали вызовом без аргументов")
        return blocks

    def test_the_hand_is_not_called_and_the_turn_stays_alive(self):
        calls, steps = [], []

        def check_email(unread_only=False, limit=20):
            calls.append({"unread_only": unread_only, "limit": limit})
            return "писем нет"

        def fake_chat(*_args, **kwargs):
            steps.append(len(kwargs.get("messages") or ()))
            if len(steps) == 1:
                return types.SimpleNamespace(stop_reason="tool_use", text="",
                                             blocks=self._torn_call_blocks())
            if len(steps) == 2:
                return types.SimpleNamespace(stop_reason="tool_use", text="", blocks=[{
                    "type": "tool_use", "id": "t2", "name": "check_email",
                    "input": {"unread_only": True}}])
            return types.SimpleNamespace(stop_reason="end_turn", text="готово", blocks=[])

        trace, messages = [], [{"role": "user", "content": "го"}]
        with mock.patch.object(agent.llm, "chat", side_effect=fake_chat), \
                mock.patch.dict(agent.TOOL_IMPL, {"check_email": check_email}):
            out = agent._terminal_tool_loop(
                system="test", messages=messages, tools=[{"name": "check_email"}],
                max_iters=None, tool_trace=trace,
            )
        self.assertEqual(out, "готово")
        self.assertEqual(len(steps), 3, "ход обязан продолжиться, а не закрыться")
        self.assertEqual(calls, [{"unread_only": True, "limit": 20}],
                         "рука звалась только со прочитанными аргументами")
        results = [message for message in messages
                   if message["role"] == "user" and isinstance(message["content"], list)]
        first = results[0]["content"][0]
        self.assertEqual(first["tool_use_id"], "t1")
        self.assertIn("не распарсились как JSON", first["content"])
        self.assertIn("НЕ выполнен", first["content"])
        self.assertNotIn("__malformed_json__", first["content"],
                         "внутренний ключ пометки ей в ленту не едет")
        self.assertIn("аргументы не распарсились как JSON", "\n".join(trace))

    def test_the_pair_of_call_and_result_is_never_broken(self):
        """Блок вызова остаётся в ленте: tool_use без tool_result провайдер не примет."""
        steps = []

        def fake_chat(*_args, **_kwargs):
            steps.append(1)
            if len(steps) == 1:
                return types.SimpleNamespace(stop_reason="tool_use", text="",
                                             blocks=self._torn_call_blocks())
            return types.SimpleNamespace(stop_reason="end_turn", text="ладно", blocks=[])

        messages = [{"role": "user", "content": "го"}]
        with mock.patch.object(agent.llm, "chat", side_effect=fake_chat), \
                mock.patch.dict(agent.TOOL_IMPL, {"check_email": lambda **_: "писем нет"}):
            agent._terminal_tool_loop(system="test", messages=messages,
                                      tools=[{"name": "check_email"}], max_iters=None)
        assistant = next(m for m in messages if m["role"] == "assistant")
        ids = [b["id"] for b in assistant["content"] if b.get("type") == "tool_use"]
        answered = [b["tool_use_id"] for m in messages
                    if m["role"] == "user" and isinstance(m["content"], list)
                    for b in m["content"] if b.get("type") == "tool_result"]
        self.assertEqual(ids, answered)

    def test_the_execution_funnel_never_expands_the_mark_into_kwargs(self):
        """Возобновлённый вызов идёт той же воронкой: `**пометка` уронил бы руку TypeError."""
        impl = mock.Mock(side_effect=AssertionError("рука не должна зваться"))
        out = agent._call_tool_with_ceiling(
            "check_email", impl, self._torn_call_blocks()[0]["input"])
        self.assertEqual(impl.call_count, 0)
        self.assertIn("check_email", out)
        self.assertIn("не распарсились как JSON", out)
        self.assertIn("Снаружи ничего не изменилось", out)

    def test_read_arguments_still_reach_the_hand_untouched(self):
        seen = {}
        agent._call_tool_with_ceiling(
            "recall", lambda **kw: seen.update(kw), {"q": "х", "n": 3})
        self.assertEqual(seen, {"q": "х", "n": 3})


if __name__ == "__main__":
    unittest.main()
