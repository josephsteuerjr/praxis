"""Пробы по незакрытым претензиям упавшего ревьюера (agent-e7fe5a76).

Его последняя строка: «Теперь я проверю поведение при краже и запущю focused
probes, чтобы подтвердить свои выводы о том, как обрабатываются tampered/
malformed control».  Вердикта не было.  Эти пробы закрывают претензию
наблюдаемо, в трёх плоскостях:

1. Кража lease: подделанный grant (чужие курсоры) должен ронять исполнение,
   а не проходить; курсоры рана при этом не двигаются.
2. Tampered control: подделанное слово (чужая схема / не-closing действие /
   не-dict) не даёт checkpoint_control-план — остаётся fail-closed путь.
3. Malformed control: не-текстовые поля режутся с ResumeEvidenceError;
   пустые строки отбрасываются; НЕ-текст не проходит молча.
"""
import unittest

from test_work_wait_survives import WorkWaitBase


class LeaseTheftProbe(WorkWaitBase):
    """Кража lease: атомарный claim обоих курсоров."""

    def test_forged_grant_fails_and_cursors_hold(self):
        import run_executor
        context = self._work_run("lease-theft")
        self._old_guard_input_before(context)
        work_state = {
            "schema": "praxis.work-loop-state.v1", "used": 0,
            "control": {"action": "done", "evidence": "evidence"},
            "sent": 0, "finished": False, "finish_note": "",
        }
        self._checkpoint(context, work_state=work_state)
        self._terminal_model_after_checkpoint(context, text="итог")
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="recovery executor stopped before transport intent",
        )
        plan = self._plan(context)
        self.assertEqual(plan.kind, "checkpoint_control")

        def stealing_acquire(lease):
            return run_executor.LeaseGrant(
                lease=lease, accepted=True,
                observed_revision="forged", observed_event_seq="forged",
                owner_token="thief",
            )

        callbacks = run_executor.ResumeExecutorCallbacks(
            acquire_lease=stealing_acquire,
        )
        outcome = run_executor.execute_resume(plan, callbacks)
        # Свойство безопасности, а не метка: подделка не завершает ранс,
        # не занимает lease, не начинает эффектов, и план строится заново.
        self.assertNotEqual(outcome.status, "completed")
        self.assertFalse(outcome.lease_acquired)
        self.assertFalse(outcome.effects_started)
        plan2 = self._plan(context)
        self.assertEqual(plan2.kind, "checkpoint_control")


class TamperedControlProbe(WorkWaitBase):
    """Tampered/malformed control: валидатор и план."""

    def test_validator_rejects_wrong_schema(self):
        from run_resume import _checkpoint_control
        self.assertIsNone(_checkpoint_control(None))
        self.assertIsNone(_checkpoint_control({}))
        self.assertIsNone(_checkpoint_control({"work_loop": None}))
        self.assertIsNone(_checkpoint_control({"work_loop": {}}))
        self.assertIsNone(_checkpoint_control({
            "work_loop": {
                "schema": "praxis.work-loop-state.v2",
                "control": {"action": "done"},
            }}))

    def test_validator_rejects_non_closing_or_non_dict_action(self):
        from run_resume import _checkpoint_control
        self.assertIsNone(_checkpoint_control({
            "work_loop": {
                "schema": "praxis.work-loop-state.v1",
                "control": {"action": "run_forever"},
            }}))
        self.assertIsNone(_checkpoint_control({
            "work_loop": {
                "schema": "praxis.work-loop-state.v1",
                "control": None,
            }}))
        self.assertIsNone(_checkpoint_control({
            "work_loop": {
                "schema": "praxis.work-loop-state.v1",
                "control": "done",
            }}))

    def test_validator_raises_on_non_text_fields(self):
        from run_resume import _checkpoint_control, ResumeEvidenceError
        with self.assertRaises(ResumeEvidenceError):
            _checkpoint_control({
                "work_loop": {
                    "schema": "praxis.work-loop-state.v1",
                    "control": {"action": "done", "evidence": 13},
                }})

    def test_validator_drops_blank_strings(self):
        from run_resume import _checkpoint_control
        clean = _checkpoint_control({
            "work_loop": {
                "schema": "praxis.work-loop-state.v1",
                "control": {"action": "wait", "summary": "   "},
            }})
        self.assertEqual(clean, {"action": "wait"})

    def test_non_closing_action_in_plan_keeps_guard_path(self):
        # done-слово с НЕ-closing действием не открывает шов: план уходит
        # в старый fail-closed путь (blocked по guard, не checkpoint_control).
        context = self._work_run("tampered-action")
        self._old_guard_input_before(context)
        work_state = {
            "schema": "praxis.work-loop-state.v1", "used": 0,
            "control": {"action": "run_forever", "evidence": "evidence"},
            "sent": 0, "finished": False, "finish_note": "",
        }
        self._checkpoint(context, work_state=work_state)
        self._terminal_model_after_checkpoint(context, text="итог")
        self.manager.transition(
            context.run_id, "paused", expected="running",
            reason="recovery executor stopped before transport intent",
        )
        plan = self._plan(context)
        self.assertNotEqual(plan.kind, "checkpoint_control")


if __name__ == "__main__":
    unittest.main()
