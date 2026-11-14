"""Native edition coverage for warm workers and stop ownership, using loopback only."""
import dataclasses
import unittest
import llm
import model_step
import test_model_workers_1411 as warm
import test_step_control_1311 as force


class EditionWorkers(unittest.TestCase):
    setUp = force.ForceControl.setUp
    stop_all = force.ForceControl.stop_all
    cancel = force.ForceControl.cancel
    wait_for = force.ForceControl.wait_for
    provider = force.ForceControl.provider
    kwargs = force.ForceControl.kwargs
    invoke = warm.WarmWorkers.invoke
    pool = warm.WarmWorkers.pool
    test_warm_reuse_and_terminal_cleanup = warm.WarmWorkers.test_twenty_warm_calls_reuse_one_generation_and_clean_terminal
    test_changed_config = warm.WarmWorkers.test_new_config_and_model_seen_without_restarting_generation
    test_parallel_reset = warm.WarmWorkers.test_parallel_same_run_not_serialized_and_both_cancel
    test_reset_preserves_sibling = warm.WarmWorkers.test_cancel_idle_closes_admission_and_preserves_other_run
    test_wire_and_response = force.ForceControl.test_provider_wire_and_normalized_response_match_direct_adapter
    test_actual_adapter = force.ForceControl.test_actual_durable_adapter_uses_child_with_identical_request
    test_reset_before_response = force.ForceControl.test_reset_kills_provider_before_first_response
    test_error_contract = force.ForceControl.test_provider_errors_preserve_retry_and_terminal_contract

    def test_edition_metadata_is_not_lost_in_transport(self):
        identity={'version':model_step.VERSION,'generation':'g','run_id':'run-fixture','lease':'l','attempt':'a'}
        reply=llm.LLMResponse(text='answer', continuation={'response_id':'fixture'},
            account_route='opaque-fixture',service_tier_requested='priority',service_tier='priority')
        value=dict(identity,response=dataclasses.asdict(reply))
        model_step._validate_result(value,identity)
        self.assertEqual(dataclasses.asdict(llm.LLMResponse(**value['response'])),dataclasses.asdict(reply))
        error=llm.TornStreamError('fixture',partial=reply)
        error.service_tier_attempted=True
        error.service_tier_requested='priority'
        error.service_tier_actual='priority'
        with self.assertRaises(llm.TornStreamError) as caught:
            model_step._raise(model_step._error(error))
        self.assertEqual(dataclasses.asdict(caught.exception.partial),dataclasses.asdict(reply))
        self.assertTrue(caught.exception.service_tier_attempted)
        self.assertEqual(caught.exception.service_tier_actual,'priority')


if __name__=='__main__':unittest.main()
