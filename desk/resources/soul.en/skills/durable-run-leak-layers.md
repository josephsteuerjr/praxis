# Accepted task control did not become terminal

If a live list shows paused/blocked while `events.jsonl` contains accepted `task_control`, do not close the manifest manually. Check whether control was actually accepted, its generation and checkpoint, and what happened afterwards.

Two historical diagnostic layers:

1. After execution, an outbound guard compares an old receipt with a new draft. A mismatch may raise `DurableExecutionError` and `process_recovery` over accepted control. Inspect the ordering of `_land_addressee_free_work_control` and `_prepare_authored_delivery` in `_AgentResumeRuntime`.
2. Preflight prevents execution entirely. Repeated `resume_attempt_idle` does not establish progress. Inspect `plan_resume` and eligibility for `checkpoint_control` for addressee-free work without outstanding calls or checkpoint outbound.

Read the latest `run_checkpoint`, `work_loop.control` and corresponding control result. Preserve lease/cursor and integrity checks; do not treat an arbitrary log line as authority to terminate.

Check a focused regression and actual `status_changed` / `recap_written` events through the supported path. Successful control parsing does not establish child-process exit or release of the Forge writer.

Old wait parking, owner pause, missing checkpoints and process custody are different classes. Do not close them in bulk or resume an explicit owner pause. A historical merge in one project does not establish that every installed version contains the fix.
