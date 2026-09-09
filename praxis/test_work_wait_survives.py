"""Её «жду» переживает конец хода и рестарт процесса.

ЧТО БЫЛО СЛОМАНО, И ПОЧЕМУ ТЕСТЫ ЭТОГО НЕ ВИДЕЛИ.  11.08 рычаг рабочего хода подняли, и у
неё появились три закрывающих слова: `done`, `blocked`, `wait`.  Работало ОДНО.  Пауза,
поставленная её словом, узнавалась возобновлением сличением ТЕКСТА причины с множеством из
двух дословных английских строк — её причина туда не входила по построению.  План выходил
`blocked`, `blocked` — не исполняемый вид, то есть noop без эффекта: прогон не поднимался
уже никогда.  «Жду» было надгробием с надписью.

Прежние 28 тестов рабочего хода все жили ВНУТРИ одного хода: сказала слово — цикл вышел.
Ни один не переходил границу хода, поэтому вакуум был не в проверках, а в их охвате.  Здесь
проверяется ровно то, чего там не было: слово сказано ЖИВОЙ рукой (в отдельном потоке, через
потолок времени), прогон припаркован, процесс «перезапущен» — и работа продолжилась ТА ЖЕ.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import agent
import media
import run_context
import run_manager
import run_resume
import work_loop


class WorkWaitBase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-work-wait-")
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.manager = run_manager.RunManager(self.base)
        self.spool = media.MediaSpool(self.base / "workspace" / "media")
        self.previous_manager = agent._RUN_MANAGER
        self.previous_spool = agent._MEDIA_SPOOL
        agent._RUN_MANAGER = self.manager
        agent._MEDIA_SPOOL = self.spool
        self.addCleanup(self._restore)

    def _restore(self):
        agent._RUN_MANAGER = self.previous_manager
        agent._MEDIA_SPOOL = self.previous_spool

    def _work_run(self, suffix: str, kind: str = "task_window") -> run_context.RunContext:
        """Рабочее окно — прогон БЕЗ адресата, ровно как он рождается живьём.

        Канал собирается тем же конструктором и тем же неизменяемым кадром, что и в
        `_task_window`: иначе возобновление отвергло бы контекст ещё в преамбуле, и тест
        проверял бы фикстуру вместо шва.
        """
        channel = agent.ChannelContext(
            chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL, is_dm=True,
            owner=False, known=True, _scope_override="owner",
        )
        context = run_context.RunContext.create(
            run_id=f"run-work-wait-{suffix}", kind=kind,
            goal=f"work {suffix}", principal_id=agent.PRAXIS_SELF_PRINCIPAL,
            scope=channel.scope, origin_chat_id=None, origin_message_ids=[],
            delivery_chat_id=None, model_profile="voice",
        )
        persisted = self.manager.create(
            context,
            agent._run_context_markdown(
                ctx=channel, kind=kind, goal=context.goal,
                conversation="immutable conversation", history=None,
                extra="immutable runtime frame",
            ),
        )
        self.manager.transition(persisted.run_id, "running", expected="pending")
        return self.manager.context(persisted.run_id)

    def _checkpoint(self, context, *, iteration: int = 3,
                    messages: list[dict] | None = None,
                    work_state: dict | None = None) -> None:
        value = {
            "schema": run_resume.CHECKPOINT_SCHEMA,
            "iteration": iteration,
            "system": "exact system",
            "messages": messages or [{"role": "user", "content": "работа в разгаре"}],
            "tools": [{"name": "fs_read"}],
            "outbound": [],
        }
        if work_state is not None:
            value["work_loop"] = work_state
        self.manager.store_result(
            context.run_id, json.dumps(value, ensure_ascii=False, indent=2),
            call_id=f"checkpoint-{iteration}", name="tool-loop-checkpoint",
            inline_chars=128, media_type="application/json; charset=utf-8",
            event_kind="run_checkpoint", idempotent=True,
        )

    def _old_guard_receipt(self, context, draft: str = "прежний черновик") -> None:
        value = {
            "schema": agent._OUTBOUND_GUARD_RECEIPT_SCHEMA,
            "draft_sha256": agent.hashlib.sha256(draft.encode("utf-8")).hexdigest(),
            "text": draft,
            "media_queue_ids": [],
            "advisor": "not_run",
            "advisor_verdict": "",
            "advisor_reason": "",
            "praxis_decision": "send_authored",
        }
        self.manager.store_result(
            context.run_id, json.dumps(value, ensure_ascii=False, indent=2),
            call_id=f"{agent._OUTBOUND_GUARD_RECEIPT_CALL_PREFIX}:{context.run_id}",
            name="outbound-guard", inline_chars=128,
            media_type="application/json; charset=utf-8",
            event_kind="outbound_guard_result", idempotent=True,
        )

    def _old_guard_input_before(self, context, *, draft: str = "старый текст") -> None:
        value = {
            "schema": run_resume.OUTBOUND_GUARD_INPUT_SCHEMA,
            "draft_sha256": agent.hashlib.sha256(draft.encode("utf-8")).hexdigest(),
            "media_queue_ids": [],
        }
        self.manager.store_result(
            context.run_id, json.dumps(value, ensure_ascii=False, indent=2),
            call_id=f"outbound-guard-input:{context.run_id}",
            name="outbound-guard-input", inline_chars=128,
            media_type="application/json; charset=utf-8",
            event_kind="outbound_guard_input", idempotent=True,
        )

    def _terminal_model_after_checkpoint(self, context, *, text: str = "итог") -> None:
        call_id = "model-after-checkpoint"
        model_input = {
            "system": "exact system",
            "messages": [{"role": "user", "content": "работа"}],
            "tools": [{"name": "fs_read"}],
        }
        self.manager.store_result(
            context.run_id, json.dumps(model_input, ensure_ascii=False, indent=2),
            call_id=call_id, name="model-input", inline_chars=128,
            media_type="application/json; charset=utf-8",
            event_kind="model_input", idempotent=True,
        )
        self.manager.append_event(
            context.run_id, "model_started", call_id=call_id,
            role="voice", message_count=1, tool_count=1,
        )
        model_output = {
            "text": text, "blocks": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "framework": "test", "model": "test",
            "usage": {},
        }
        self.manager.store_result(
            context.run_id, json.dumps(model_output, ensure_ascii=False, indent=2),
            call_id=call_id, name="model-output", inline_chars=128,
            media_type="application/json; charset=utf-8",
            event_kind="model_output", idempotent=True,
        )
        self.manager.append_event(
            context.run_id, "model_completed", call_id=call_id,
            role="voice", stop_reason="end_turn",
        )

    def _say(self, context, action: str, **fields) -> dict:
        """Её слово ЖИВОЙ рукой: через потолок времени, в отдельном потоке.

        Прямой вызов `work_loop.claim()` здесь был бы ровно тем вакуумом, из-за которого
        первая редакция рабочего хода уехала на прод неисправной: запись из потока руки
        не доезжала до цикла, а 19 тестов звали руку в своём же контексте и молчали.
        """
        with run_context.bind_run(context):
            agent._call_tool_with_ceiling(
                "task_control", agent.tool_task_control,
                {"action": action, **fields},
            )
            return work_loop.taken()

    def _park(self, context, control: dict, *, now: float | None = None) -> dict:
        """Те самые две строки, которыми живой ход закрывает рабочее окно."""
        status, reason, stamp = work_loop.closing(control, now=now)
        self.manager.transition(
            context.run_id, status, expected="running", reason=reason, details=stamp)
        return {"status": status, "reason": reason, "details": stamp}

    def _plan(self, context):
        return run_resume.plan_resume(
            self.manager, context.run_id, outbound_roots=[self.spool.root])


class HerWaitIsNotAGrave(WorkWaitBase):
    def test_her_wait_parks_the_run_and_recovery_picks_it_up(self):
        context = self._work_run("resumable")
        self._checkpoint(context, work_state=work_loop.snapshot())
        control = self._say(context, "wait", wake_on="жду ответа реле")
        self.assertEqual(control["action"], "wait")

        parked = self._park(context, control, now=time.time() - 7200)
        self.assertEqual(parked["status"], "paused")
        self.assertEqual(parked["details"][work_loop.PAUSE_KIND_KEY],
                         work_loop.PAUSE_HER_WAIT)

        plan = self._plan(context)
        self.assertEqual(plan.kind, "continue_checkpoint")
        self.assertTrue(plan.auto_resume)

    def test_a_run_parked_by_the_previous_build_is_revived_too(self):
        """Тот самый до/после, и он не про новый API, а про поведение.

        Пауза записана ДОСЛОВНО так, как её пишет прод с 11.08 19:24: проза по-русски плюс
        `details.task_control`, но ни машинного вида паузы, ни срока — их тогда не
        существовало. На прежнем коде этот же прогон даёт план `blocked`, то есть noop
        навсегда; здесь он поднимается. Значит работа, припаркованная до выката, не
        потеряна — а это единственный способ не заплатить за починку её же ходами.
        """
        context = self._work_run("previous-build")
        self._checkpoint(context, work_state=work_loop.snapshot())
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="закрыт её словом «wait»: жду ответа реле",
            details={"task_control": {"action": "wait", "wake_on": "жду ответа реле"}},
        )
        plan = self._plan(context)
        self.assertEqual(plan.kind, "continue_checkpoint")
        self.assertTrue(plan.auto_resume)

    def test_a_pause_that_is_not_her_word_stays_exactly_as_before(self):
        """Граница: без её записи пауза остаётся тем, чем была. Мы расширили протокол, а
        не открыли дверь всему подряд."""
        context = self._work_run("not-her-word")
        self._checkpoint(context)
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="кто-то остановил ход и не сказал чем",
        )
        plan = self._plan(context)
        self.assertEqual(plan.kind, "blocked")
        self.assertIn(plan.kind, agent.run_executor.NON_EXECUTABLE_KINDS)

    def test_the_floor_holds_and_says_when_it_lifts(self):
        context = self._work_run("floor")
        self._checkpoint(context, work_state=work_loop.snapshot())
        control = self._say(context, "wait", wake_on="жду письма")
        parked = self._park(context, control)

        plan = self._plan(context)
        self.assertEqual(plan.kind, "blocked")
        self.assertIn(run_resume.WAIT_NOT_DUE, plan.diagnostics)
        self.assertIn(parked["details"]["wake"]["not_before"], plan.reason)
        self.assertIn("жду письма", plan.reason)

    def test_waiting_for_its_own_term_is_not_a_stalled_attempt(self):
        """Ожидание, назначенное её словом, не копит ей отсрочку за терпение."""
        context = self._work_run("no-penalty")
        self._checkpoint(context, work_state=work_loop.snapshot())
        self._park(context, self._say(context, "wait", wake_on="жду"))

        report = agent.resume_durable_run(context.run_id)
        self.assertTrue(report.get(run_resume.WAIT_NOT_DUE))

        before = len(self.manager.events(context.run_id))
        agent.resume_durable_runs(limit=5)
        after = self.manager.events(context.run_id)
        self.assertEqual(len(after), before)
        self.assertFalse([row for row in after
                          if row.get("kind") == "resume_attempt_idle"])

    def test_the_owner_pause_over_her_word_stays_the_owners(self):
        """Пульт сильнее её прошлого «жду»: иначе «остановись» снималось бы автоматом."""
        context = self._work_run("owner-pause")
        self._checkpoint(context, work_state=work_loop.snapshot())
        control = self._say(context, "wait", wake_on="жду")
        status, reason, stamp = work_loop.closing(control, now=time.time() - 7200)
        self.manager.transition(
            context.run_id, status, expected="running", reason=reason, details=stamp)
        self.manager.append_event(
            context.run_id, "status_changed", from_status="paused", to_status="paused",
            reason="owner asked to stop", details=dict(stamp),
            control_action="pause", requested_by="telegram:809306689",
        )
        self.assertEqual(self._plan(context).kind, "blocked")


class TheRevivedTurnIsTheSameWork(WorkWaitBase):
    def test_her_already_honoured_word_does_not_close_the_revived_turn(self):
        """Иначе поднятый ход закрылся бы тем же словом на первом тексте — и так вечно."""
        context = self._work_run("revive")
        state = {"schema": "praxis.work-loop-state.v1", "used": 5,
                 "control": {"action": "wait", "wake_on": "жду"}}
        self._checkpoint(context, work_state=state)
        self._park(context, self._say(context, "wait", wake_on="жду"),
                   now=time.time() - 7200)

        plan = self._plan(context)
        self.assertEqual(plan.kind, "continue_checkpoint")
        handed = plan.checkpoint["work_loop"]
        self.assertIsNone(handed["control"])
        self.assertEqual(handed["used"], 0)
        self.assertEqual(handed["woken"]["wake_on"], "жду")

    def test_she_is_told_why_she_is_awake(self):
        note = work_loop.woken_note({
            "woken": {"wake_on": "ответ реле", "parked_at": "2026-08-11T20:00:00.000Z"},
        })
        self.assertIn("ответ реле", note)
        self.assertIn("2026-08-11T20:00:00.000Z", note)
        self.assertIn("ТА ЖЕ работа", note)
        self.assertEqual(work_loop.woken_note({"used": 0}), "")

    def test_the_revived_turn_continues_the_work_and_lands_on_her_word(self):
        """Сквозной путь: слово → парковка → «рестарт» → работа продолжилась → её слово.

        Проверяется то, ради чего всё делалось: возобновлённый ход получает ТУ ЖЕ ленту,
        видит записку о пробуждении, и если она снова сказала «жду» — прогон снова
        паркуется, а не объявляется сделанным.
        """
        context = self._work_run("end-to-end")
        self._checkpoint(
            context, iteration=4,
            messages=[{"role": "user", "content": "первая половина работы"}],
            work_state=work_loop.snapshot(),
        )
        self._old_guard_receipt(context)
        self._park(context, self._say(context, "wait", wake_on="жду сборку"),
                   now=time.time() - 7200)

        seen: dict = {}

        def loop(**kwargs):
            seen.update(kwargs)
            agent._call_tool_with_ceiling(
                "task_control", agent.tool_task_control,
                {"action": "wait", "wake_on": "сборка ещё идёт"},
            )
            return "продолжаю ждать сборку"

        with mock.patch.object(agent, "_terminal_tool_loop", side_effect=loop):
            report = agent.resume_durable_run(context.run_id)

        self.assertEqual(report["plan_kind"], "continue_checkpoint")
        self.assertEqual(seen["start_iteration"], 4)
        self.assertEqual(seen["messages"][0],
                         {"role": "user", "content": "первая половина работы"})
        self.assertIn("рабочий ход", seen["messages"][-1]["content"][0]["text"])
        self.assertIn("жду сборку", seen["messages"][-1]["content"][0]["text"])

        manifest = self.manager.manifest(context.run_id)
        self.assertEqual(manifest["status"], "paused")
        row = [r for r in self.manager.events(context.run_id)
               if r.get("kind") == "status_changed" and r.get("to_status") == "paused"][-1]
        self.assertEqual(row["details"]["task_control"]["wake_on"], "сборка ещё идёт")

    def test_a_revived_turn_that_says_done_still_lands_done(self):
        context = self._work_run("done-after-wait")
        self._checkpoint(context, work_state=work_loop.snapshot())
        self._old_guard_receipt(context)
        self._park(context, self._say(context, "wait", wake_on="жду"),
                   now=time.time() - 7200)

        def loop(**_kwargs):
            agent._call_tool_with_ceiling(
                "task_control", agent.tool_task_control,
                {"action": "done", "evidence": "сборка зелёная, лог в result-0007"},
            )
            return "готово"

        with mock.patch.object(agent, "_terminal_tool_loop", side_effect=loop):
            report = agent.resume_durable_run(context.run_id)

        self.assertEqual(report["status"], "completed")
        manifest = self.manager.manifest(context.run_id)
        self.assertEqual(manifest["status"], "done")
        self.assertIn("сборка зелёная", manifest["terminal"]["reason"])
        row = [r for r in self.manager.events(context.run_id)
               if r.get("kind") == "status_changed" and r.get("to_status") == "done"][-1]
        self.assertEqual(row["details"]["task_control"]["action"], "done")

    def test_tool_response_resume_lands_her_word_before_the_old_guard_too(self):
        context = self._work_run("tool-response-done")
        self._checkpoint(context, work_state=work_loop.snapshot())
        self._old_guard_receipt(context)
        self._park(context, self._say(context, "wait", wake_on="жду инструмент"),
                   now=time.time() - 7200)

        def loop(**_kwargs):
            agent._call_tool_with_ceiling(
                "task_control", agent.tool_task_control,
                {"action": "done", "evidence": "результат инструмента проверен"},
            )
            return "инструмент проверен"

        plan = self._plan(context)
        runtime = agent._AgentResumeRuntime(plan)
        request = agent.run_executor.ToolResponseContinuationRequest(
            lease=agent.run_executor.ResumeLease.from_plan(plan),
            owner_token="test-owner",
            context=plan.context,
            model_input={
                "system": "exact system",
                "messages": [{"role": "user", "content": "работа"}],
                "tools": [{"name": "fs_read"}],
            },
            model_output={"stop_reason": "tool_use", "blocks": []},
            resolutions=(), checkpoint={"iteration": 2}, outbound=(),
        )
        self.manager.resume(
            context.run_id, actor="test:resume-runtime",
            reason="test acquired exact resume lease",
        )
        with mock.patch.object(agent, "_terminal_tool_loop", side_effect=loop):
            result = runtime.continue_tool_response(request)

        self.assertEqual(result["run_status"], "done")
        manifest = self.manager.manifest(context.run_id)
        self.assertEqual(manifest["status"], "done")
        self.assertIn("результат инструмента проверен", manifest["terminal"]["reason"])
    def test_legacy_done_checkpoint_lands_before_stale_guard_ordering(self):
        """Prod regression: accepted done was checkpointed, then an older guard poisoned plan."""
        context = self._work_run("legacy-done-poisoned")
        self._old_guard_input_before(context)
        control = self._say(
            context, "done", evidence="точный артефакт и зелёный тест",
            summary="работа завершена",
        )
        work_state = {
            "schema": "praxis.work-loop-state.v1", "used": 0,
            "control": dict(control), "sent": 0, "finished": False,
            "finish_note": "",
        }
        self._checkpoint(context, iteration=9, work_state=work_state)
        self._terminal_model_after_checkpoint(context, text="работа завершена")
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="recovery executor stopped before transport intent",
        )

        plan = self._plan(context)
        self.assertEqual(plan.kind, "checkpoint_control")
        self.assertEqual(plan.diagnostics, ("done",))
        with mock.patch.object(
            agent, "_terminal_tool_loop",
            side_effect=AssertionError("legacy control must not re-author"),
        ):
            report = agent.resume_durable_run(context.run_id)

        self.assertEqual(report["status"], "completed")
        manifest = self.manager.manifest(context.run_id)
        self.assertEqual(manifest["status"], "done")
        self.assertIn("точный артефакт", manifest["terminal"]["reason"])
        self.assertEqual(control["action"], "done")

    def test_legacy_checkpoint_control_never_bypasses_an_addressed_run(self):
        channel = agent.ChannelContext(
            chat_id="777", room_id="777", principal_id="777",
            is_dm=True, owner=True, known=True, addressed=True,
            address_message_id=9, address_kind="direct",
            reply_targets=((9, "Yegor", "continue"),),
        )
        context = run_context.RunContext.create(
            run_id="run-work-wait-addressed-boundary", kind="chat_turn",
            goal="addressed boundary", principal_id="777", scope=channel.scope,
            origin_chat_id="777", origin_message_ids=[9],
            delivery_chat_id="777", model_profile="voice",
        )
        persisted = self.manager.create(
            context, agent._run_context_markdown(
                ctx=channel, kind=context.kind, goal=context.goal,
                conversation="immutable conversation", history=None,
                extra="immutable runtime frame",
            ),
        )
        self.manager.transition(persisted.run_id, "running", expected="pending")
        context = self.manager.context(persisted.run_id)
        self._old_guard_input_before(context)
        work_state = {
            "schema": "praxis.work-loop-state.v1", "used": 0,
            "control": {"action": "done", "evidence": "evidence"},
            "sent": 0, "finished": False, "finish_note": "",
        }
        self._checkpoint(context, work_state=work_state)
        self._terminal_model_after_checkpoint(context, text="addressed result")
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="recovery executor stopped before transport intent",
        )
        plan = self._plan(context)
        self.assertEqual(plan.kind, "blocked")
        self.assertIn("outbound guard input", plan.reason)


class TheProtocolIsMachineReadable(WorkWaitBase):
    def test_the_store_stamps_the_same_pause_kind_the_policy_names(self):
        """Сторож расхождения. `run_manager` пишет вид паузы литералом (хранилище не должно
        зависеть от политики) — значит слова обязаны совпадать, и проверяется это поведением,
        а не сличением исходников."""
        context = self._work_run("recover")
        reports = run_manager.RunManager(self.base).recover()
        self.assertTrue([r for r in reports if r["run_id"] == context.run_id])
        row = [r for r in self.manager.events(context.run_id)
               if r.get("kind") == "status_changed" and r.get("to_status") == "paused"][-1]
        self.assertEqual(row["details"][work_loop.PAUSE_KIND_KEY],
                         work_loop.PAUSE_PROCESS_RECOVERY)
        self.assertEqual(self._plan(context).status, "paused")

    def test_the_legacy_prose_pause_is_still_understood(self):
        """История не переписывается: события, записанные до протокола, читаются как были."""
        context = self._work_run("legacy-prose")
        self._checkpoint(context)
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="process restarted; no uncertain side effect observed",
        )
        self.assertEqual(self._plan(context).kind, "continue_checkpoint")

    def test_her_blocked_names_the_obstacle_instead_of_a_shrug(self):
        context = self._work_run("blocked")
        self._checkpoint(context)
        control = self._say(context, "blocked", blocker="нет доступа к GitHub-транспорту")
        status, reason, stamp = work_loop.closing(control)
        self.assertEqual(status, "blocked")
        self.manager.transition(
            context.run_id, "blocked", expected="running", reason=reason, details=stamp)

        plan = self._plan(context)
        self.assertEqual(plan.kind, "blocked")
        self.assertIn("нет доступа к GitHub-транспорту", plan.reason)

    def test_silence_still_means_the_old_default_byte_for_byte(self):
        """Рычаг опущен или слова не было — поведение прежнее, дословно."""
        self.assertEqual(work_loop.closing(None),
                         ("done", "long work run completed", {}))
        self.assertEqual(work_loop.closing({"action": "нечто"}),
                         ("done", "long work run completed", {}))

    def test_the_floor_names_itself_in_her_frame(self):
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_LOOP": "on",
                                            "PRAXIS_WORK_WAIT_FLOOR_SEC": "900"}):
            frame = work_loop.announce("task_window")
        self.assertIn("900", frame)
        self.assertIn("не хоронит работу", frame)


if __name__ == "__main__":
    unittest.main()
