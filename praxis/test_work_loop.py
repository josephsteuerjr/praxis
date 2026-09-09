"""Ход кончается её словом, а не молчанием инструментов.

ИСТОРИЯ ДЕФЕКТА, ЖИВАЯ.  10.08.2026 22:20 Егор дважды попросил переключить голос на Luna.
Praxis ответила «Я здесь. Переключаюсь на Luna.» — и прогон
`run-20260810T222023869484Z-101ff848` закрылся `done`, содержа ровно один вызов
инструмента: доставку этого самого текста.  `llm.json` не менялся.  Причина не в ней:
`_terminal_tool_loop` возвращался на первом же тексте без `tool_use`, а вызывающий писал
`done`.  Объявленное намерение принималось за сделанное дело.

ЧТО ПРОВЕРЯЕТСЯ ЗДЕСЬ.  Свойства, а не реализация:
  * текст без инструмента НЕ закрывает рабочий ход, пока есть бюджет;
  * закрывает только `task_control`, и статус прогона берётся из её слова;
  * исчерпанный бюджет закрывает ход С НАЗВАННОЙ ПРИЧИНОЙ, а не тихо;
  * выключенный рычаг возвращает прежнее поведение;
  * чат не затронут вовсе.

Сквозной тест цикла гоняет `_terminal_tool_loop` с подставным `_model_call`: настоящая
петля, ненастоящая модель.
"""
import contextlib
import os
import unittest
from unittest import mock

import run_context
import work_loop


def _fake_run(kind: str = "task_window") -> run_context.RunContext:
    """Настоящий RunContext — ключ, по которому живёт состояние рабочего хода.

    ⚠ Прогон здесь обязателен, и это не формальность теста. Состояние лежит в словаре по
    `run_id`; без прогона класть слово НЕКУДА, и так задумано: безрановые проходы не делят
    общий слот, иначе слово одного хода закрывало бы другой.
    """
    return run_context.RunContext.create(
        kind=kind, goal="проверка рабочего хода", principal_id="test", scope="owner")


class _Resp:
    def __init__(self, text, stop_reason="end_turn", blocks=None):
        self.text = text
        self.stop_reason = stop_reason
        self.blocks = blocks if blocks is not None else [{"type": "text", "text": text}]


def _on(**extra):
    env = {"PRAXIS_WORK_LOOP": "on"}
    env.update(extra)
    return mock.patch.dict(os.environ, env)


class PolicyIsHonestAboutItsLimits(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.run = stack.enter_context(run_context.bind_run(_fake_run()))
        work_loop.reset()
        self.addCleanup(work_loop.release, self.run.run_id)

    def test_lever_off_keeps_the_old_behaviour(self):
        """Выключенный рычаг обязан возвращать прежнее поведение, а не «почти прежнее»."""
        with mock.patch.dict(os.environ, {"PRAXIS_WORK_LOOP": "off"}):
            keep, note = work_loop.decide(kind="task_window")
        self.assertFalse(keep)
        self.assertEqual(note, "")

    def test_snapshot_restores_progress_and_control(self):
        with _on():
            work_loop.spend()
            work_loop.spend()
            claimed = work_loop.claim("wait", wake_on="receipt")
            state = work_loop.snapshot()
        work_loop.reset()
        with _on():
            work_loop.restore(state)
            self.assertEqual(work_loop.used(), 2)
            self.assertEqual(work_loop.taken(), claimed)
            keep, _note = work_loop.decide(kind="task_window", control=work_loop.taken())
        self.assertFalse(keep)

    def test_invalid_snapshot_starts_cleanly(self):
        with _on():
            work_loop.spend()
            work_loop.restore({"schema": "wrong", "used": 99})
            self.assertEqual(work_loop.used(), 0)
            self.assertIsNone(work_loop.taken())

    def test_chat_turn_is_never_a_work_run(self):
        """Чат закрывается отправленным сообщением; трогать его цикл мы не вправе."""
        with _on():
            self.assertFalse(work_loop.active_for("chat_turn"))
            keep, _ = work_loop.decide(kind="chat_turn")
        self.assertFalse(keep)

    def test_plain_text_does_not_close_a_work_run(self):
        with _on():
            keep, note = work_loop.decide(kind="task_window")
        self.assertTrue(keep, "текст без инструмента закрыл рабочий ход")
        self.assertIn("task_control", note)

    def test_her_word_closes_it(self):
        with _on():
            work_loop.claim("done", evidence="llm.json: voice=gpt-5.6-luna")
            keep, note = work_loop.decide(kind="task_window", control=work_loop.taken())
        self.assertFalse(keep)
        self.assertIn("done", note)

    def test_every_closing_action_maps_to_its_own_status(self):
        """«Упёрлась» и «жду» не смеют выглядеть как «сделала»."""
        self.assertEqual(work_loop.status_for("done"), "done")
        self.assertEqual(work_loop.status_for("blocked"), "blocked")
        self.assertEqual(work_loop.status_for("wait"), "paused")

    def test_unknown_action_is_refused_not_swallowed(self):
        with self.assertRaises(ValueError):
            work_loop.claim("готово")

    def test_exhausted_budget_says_so_out_loud(self):
        """Молчаливый потолок — это поводок за спиной. Ровно на этом сгорела петля 10.08."""
        with _on(PRAXIS_WORK_CONTINUATIONS="2"):
            for _ in range(2):
                keep, _ = work_loop.decide(kind="task_window")
                self.assertTrue(keep)
                work_loop.spend()
            keep, note = work_loop.decide(kind="task_window")
        self.assertFalse(keep)
        self.assertIn("бюджет", note.lower())
        self.assertIn("2", note)

    def test_frame_announces_the_budget_before_it_bites(self):
        with _on(PRAXIS_WORK_CONTINUATIONS="5"):
            said = work_loop.announce("task_window")
        self.assertIn("5", said)
        self.assertIn("task_control", said)

    def test_frame_is_silent_when_the_lever_is_down(self):
        with mock.patch.dict(os.environ, {"PRAXIS_WORK_LOOP": "off"}):
            self.assertEqual(work_loop.announce("task_window"), "")

    def test_nudge_names_the_spend_every_time(self):
        with _on(PRAXIS_WORK_CONTINUATIONS="4"):
            note = work_loop.nudge(3, 4)
        self.assertIn("3", note)
        self.assertIn("4", note)

    def test_broken_budget_value_falls_back_instead_of_crashing(self):
        with _on(PRAXIS_WORK_CONTINUATIONS="сколько-нибудь"):
            self.assertEqual(work_loop.budget(), work_loop.DEFAULT_CONTINUATIONS)


class TheLoopItselfKeepsGoing(unittest.TestCase):
    """Сквозняк через настоящий `_terminal_tool_loop` с подставной моделью."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.run = stack.enter_context(run_context.bind_run(_fake_run()))
        work_loop.reset()
        self.addCleanup(work_loop.release, self.run.run_id)
        import agent
        self.agent = agent

    def _run(self, responses, kind="task_window"):
        calls = {"n": 0}

        def fake_call(system, messages, tools=None):
            i = calls["n"]
            calls["n"] += 1
            return responses[min(i, len(responses) - 1)]

        messages = [{"role": "user", "content": "работай"}]
        trace: list[str] = []
        with mock.patch.object(self.agent, "_model_call", fake_call), \
                mock.patch.object(self.agent, "_run_status_gate", lambda **kw: None), \
                mock.patch.object(self.agent, "_current_run_kind", lambda: kind):
            out = self.agent._terminal_tool_loop(
                system="s", messages=messages, tools=[], tool_trace=trace)
        return out, trace, calls["n"], messages

    def test_she_is_asked_again_instead_of_being_cut_off(self):
        """Тот самый случай: «Переключаюсь на Luna» больше не закрывает ход."""
        said = _Resp("Я здесь. Переключаюсь на Luna.")
        with _on(PRAXIS_WORK_CONTINUATIONS="3"):
            out, trace, n, messages = self._run([said])
        self.assertGreater(n, 1, "модель позвали один раз — ход закрылся на слове")
        self.assertTrue(any("продолжение" in t for t in trace))
        nudges = [m for m in messages if m.get("role") == "user"
                  and "рабочий ход" in str(m.get("content"))]
        self.assertTrue(nudges, "укол продолжения не доехал до ленты")
        self.assertEqual(out, "Я здесь. Переключаюсь на Luna.")

    def test_the_run_stops_when_the_budget_is_out_and_the_trace_says_why(self):
        with _on(PRAXIS_WORK_CONTINUATIONS="2"):
            out, trace, n, _ = self._run([_Resp("думаю дальше")])
        self.assertEqual(n, 3, "бюджет 2 обязан дать ровно три вызова модели")
        self.assertTrue(any("бюджет" in t for t in trace), trace)

    def test_lever_off_is_byte_identical_to_the_old_path(self):
        with mock.patch.dict(os.environ, {"PRAXIS_WORK_LOOP": "off"}):
            out, trace, n, _ = self._run([_Resp("готово")])
        self.assertEqual(n, 1)
        self.assertEqual(out, "готово")
        self.assertEqual(trace, [])

    def test_chat_turn_is_untouched(self):
        with _on():
            out, trace, n, _ = self._run([_Resp("ответила")], kind="chat_turn")
        self.assertEqual(n, 1)
        self.assertEqual(trace, [])


class TheToolSpeaksPlainly(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.run = stack.enter_context(run_context.bind_run(_fake_run()))
        work_loop.reset()
        self.addCleanup(work_loop.release, self.run.run_id)
        import agent
        self.agent = agent

    def test_done_without_evidence_is_refused_and_the_turn_stays_open(self):
        """ПРАВКА 12.08: «сделано» стало ЗАПРОСОМ, а не объявлением.

        Прежняя редакция принимала голое `done` и называла недостачу вслух — то есть
        работа объявлялась сделанной, а рядом стояла приписка, что доказательств нет.
        Это ровно тот класс, за которым дом охотится: объявленное принимается за
        сделанное. Теперь ход НЕ закрывается и возвращается ей — назвать след.
        Прежнее поведение живо под рычагом `PRAXIS_WORK_DOD=off` (тест ниже).
        """
        out = self.agent.tool_task_control("done", summary="переключила")
        self.assertIn("не принято", out)
        self.assertIsNone(work_loop.taken(), "ход закрылся без единого следа")

    def test_done_with_evidence_reads_back_the_evidence(self):
        out = self.agent.tool_task_control("done", evidence="llm.json voice=gpt-5.6-luna")
        self.assertIn("gpt-5.6-luna", out)

    def test_garbage_action_does_not_close_anything(self):
        out = self.agent.tool_task_control("почти")
        self.assertIn("task_control", out)
        self.assertIsNone(work_loop.taken())

    def test_the_hand_is_offered_only_in_work_runs(self):
        ctx = self.agent.ChannelContext(
            chat_id=None, principal_id=self.agent.PRAXIS_SELF_PRINCIPAL, is_dm=True,
            owner=False, known=True, _scope_override="owner")
        with _on(), mock.patch.object(self.agent, "_current_run_kind", lambda: "task_window"):
            names = {t.get("name") for t in self.agent.offered_tools_for(ctx)}
        self.assertIn("task_control", names)
        with _on(), mock.patch.object(self.agent, "_current_run_kind", lambda: "chat_turn"):
            names = {t.get("name") for t in self.agent.offered_tools_for(ctx)}
        self.assertNotIn("task_control", names)


class HerWordCrossesTheThreadBoundary(unittest.TestCase):
    """РЕАЛЬНЫЙ ПУТЬ. Тест, которого не было — и потому дефект прожил сутки.

    Её руки исполняются НЕ в контексте цикла: `_call_tool_with_ceiling` гоняет их в
    `contextvars.copy_context()` в отдельном потоке, потому что у руки есть потолок
    времени. Первая редакция `work_loop` держала слово в contextvars — запись уходила в
    копию и умирала вместе с потоком, `taken()` у вызывающего возвращал None ВСЕГДА.

    Девятнадцать зелёных тестов этого не поймали, потому что звали `tool_task_control`
    НАПРЯМУЮ, в своём же контексте. Здесь мы идём тем путём, которым ходит она.
    """

    def setUp(self):
        import agent
        self.agent = agent
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.run = stack.enter_context(run_context.bind_run(_fake_run()))
        work_loop.reset()
        self.addCleanup(work_loop.release, self.run.run_id)

    def test_the_ceiling_really_does_run_the_hand_in_another_context(self):
        """Сначала докажем саму предпосылку: потолок включён и он не ноль."""
        self.assertGreater(self.agent.TOOL_CEILING_SEC, 0,
                           "потолок выключен — тест ниже проверял бы не тот путь")

    def test_her_word_arrives_through_the_real_hand(self):
        impl = self.agent.TOOL_IMPL["task_control"]
        with _on():
            out = self.agent._call_tool_with_ceiling(
                "task_control", impl, {"action": "wait", "wake_on": "жду реле"})
            taken = work_loop.taken()
            keep, note = work_loop.decide(kind="task_window", control=taken)
        self.assertIn("принято", str(out))
        self.assertIsNotNone(taken, "её слово не доехало до цикла — это и был дефект")
        self.assertEqual(taken["action"], "wait")
        self.assertEqual(taken.get("wake_on"), "жду реле")
        self.assertFalse(keep, "цикл продолжил ход, хотя она сказала «жду»")
        self.assertIn("wait", note)

    def test_the_spend_counter_also_survives_the_hand(self):
        with _on():
            work_loop.spend()
            self.agent._call_tool_with_ceiling(
                "task_control", self.agent.TOOL_IMPL["task_control"],
                {"action": "done", "evidence": "проверено"})
            self.assertEqual(work_loop.used(), 1, "счётчик потерялся на границе потока")
            self.assertEqual((work_loop.taken() or {}).get("action"), "done")

    def test_one_run_cannot_close_another(self):
        """В одном тике возобновления двадцать прогонов идут в ОДНОМ потоке.

        До ключа по `run_id` слово прогона A закрыло бы прогон B — и это невозможно было
        заметить, пока состояние жило в одном общем месте.
        """
        with _on():
            self.agent._call_tool_with_ceiling(
                "task_control", self.agent.TOOL_IMPL["task_control"],
                {"action": "done", "evidence": "прогон A"})
            self.assertIsNotNone(work_loop.taken())
            other = _fake_run()
            with run_context.bind_run(other):
                self.addCleanup(work_loop.release, other.run_id)
                self.assertIsNone(work_loop.taken(),
                                  "слово прогона A видно из прогона B — они смешаны")
                keep, _ = work_loop.decide(kind="task_window", control=work_loop.taken())
                self.assertTrue(keep, "чужое слово закрыло этот ход")

    def test_release_frees_the_slot_and_stats_tell_the_truth(self):
        with _on():
            self.agent._call_tool_with_ceiling(
                "task_control", self.agent.TOOL_IMPL["task_control"],
                # След назван намеренно: этот тест про освобождение слота, а не про DoD.
                # Голое `done` с 12.08 не кладётся в слот вовсе — освобождать было бы нечего.
                {"action": "done", "evidence": "слот занят этим ходом"})
        self.assertIsNotNone(work_loop.taken())
        before = work_loop.stats()["runs"]
        work_loop.release(self.run.run_id)
        self.assertIsNone(work_loop.taken(), "слот не освободился — словарь будет течь")
        self.assertEqual(work_loop.stats()["runs"], before - 1)

    def test_without_a_run_the_word_has_nowhere_to_go_and_says_so(self):
        """Безрановый проход не делит общий слот — иначе вернулась бы та же болезнь."""
        token = run_context.reset_run if hasattr(run_context, "reset_run") else None
        self.assertIsNotNone(token, "нет способа отвязать прогон — проверка вакуумна")
        with _on(), mock.patch.object(run_context, "current_run", lambda **kw: None):
            work_loop.claim("done", evidence="в пустоте")
            self.assertIsNone(work_loop.taken())


if __name__ == "__main__":
    unittest.main()
