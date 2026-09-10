# Frame v6: owner-DM canary (not an epoch migration)

Default is unchanged. `measure` remains observation only. `serve` is a distinct
opt-in seam in `agent._model_call`, before durable model-input persistence and
`llm.chat`. The actual selected system is persisted, not just measured.

## Scope and safety contract

- Requires `PRAXIS_FRAME_V6=serve` and **only**
  `PRAXIS_FRAME_V6_STREAMS=dm-<PRAXIS_OWNER_ID>` (actual positive numeric owner ID).
  Also requires current `ChannelContext.is_dm`, `owner_audience`, matching chat
  and room IDs; identity-blind experiments are excluded. Groups, other DMs,
  no-chat work, wildcard/multiple streams and missing owner configuration stay live.
- Uses measure's fresh K/E assembly (shadow source selectors), not shadow capture,
  epoch files, frozen E, flattened history, or scrubbed model-input receipts.
  E sources and offered-tool pointers are reread every model iteration.
- The whole finalized live dynamic system tail is carried verbatim in T, including
  transport state and extra_system. It is NOT reconstructed from trace coverage.
  The live role tape (current situation, evidence, images, assistant calls and tool
  results) and actual tool schemas keep their original objects at the boundary.
- A turn's existing dynamic tail retains its existing live lifetime. It is not
  independently rerendered mid-tool-loop; evolving role messages/tools remain live.
- Receipts contain `variant=serve`, `frame_serve.status=served|fallback`,
  `served_variant=v6|live`, assembly and resume enums. Old live trace geometry is
  not claimed for a served system. Assembly failures fall back to live, without
  storing exception text. No extra shadow bodies or epochs are written by serve.
- A successful model-input receipt additionally stores `frame_v6_live_system`,
  scrubbed exactly like system. Durable tool-response continuation explicitly uses
  that live artifact, maintaining exact tool-use/result pairing. Ordinary loop
  checkpoints already contain live system. Resume is intentionally live, not v6:
  an unbound reconstructed context cannot silently serve a stale owner snapshot.

This is a conservative fresh-E serving canary, **not** migration of the A/T
accumulator, not frozen-epoch serving and not proven cache savings. Original
message evidence may duplicate E. Shadow block wording inherited from frozen
renderers is qualified by an explicit canary freshness note.

## Proposed activation — not executed

1. Complete independent review, targeted regressions and repository release gates
   on an exact clean commit; preserve immutable code rollback ref/tar. Record an
   explicit owner-authorized canary receipt and a visible notice to Praxis naming
   the changed frame, scope, fresh-E policy, resume fallback and rollback.
2. Verify actual live checkout/process/environment and owner-DM routing. Never
   infer live state from this proposal. Do not print `.env` or private contents.
3. Set these exact values in the deployment environment (substitute real owner ID;
   do not put shell variable syntax literally into the dotenv value):

   ```text
   PRAXIS_FRAME_V6=serve
   PRAXIS_FRAME_V6_STREAMS=dm-<actual-positive-owner-id>
   ```

   Keep existing `PRAXIS_OWNER_ID` unchanged. No group stream is supported.
   Apply through the existing deployment process. `agent.py:569` and
   `mtproto_runner.py:63` load dotenv with `override=True`: a mounted `.env`
   can override Compose values on startup. Verify the effective selected knobs,
   without printing secrets. Mounted dotenv edits are picked up on runner restart;
   changes to Compose-only environment require service recreation.
4. Send one owner DM with a tool round trip. Inspect redacted receipt metadata:
   `frame_serve.status=served`, `served_variant=v6`; inspect actual model-input
   locally for new system plus unchanged tape/tools and rollback artifact. Verify
   subsequent iterations and terminal delivery, no duplicate actions, and that
   group/nonowner/no-chat turns have no serve receipt. Never publish private input.
5. Exercise durable continuation: it must use live system and exact pending tool
   pairing, without replaying completed side effects. Observe fallback/error rates
   and latency. Do not call successful delivery alone semantic equivalence.

## Rollback

Set `PRAXIS_FRAME_V6=off` (or restore prior mode), clear
`PRAXIS_FRAME_V6_STREAMS`, restore the effective configuration source (including
mounted dotenv overrides), and restart or recreate as appropriate above. Verify
next request uses live input and lacks a serve receipt. Within a running process
the selector rechecks environment each call; editing dotenv alone without a
restart does not update that process environment.
Durable canary continuations roll back to stored live system even while `serve`
remains enabled. Rollback is prospective: do not resend delivered replies or replay
completed tools. If reverting code too, first drain/resolve canary pending runs
using this rollback-aware code; older code does not understand the extra artifact.

## Verification follow-up (isolated proposal, 2026-09-08)

The dedicated regression
`test_agent_resume_runtime.AgentResumeRuntimeTests.test_served_receipt_real_resume_rolls_back_without_repeating_effect`
now exercises actual `_model_call` receipt persistence, `resume_durable_run`, the
real resume planner/executor/runtime, terminal tool loop and fake `llm.chat`.
A completed synthetic send receipt survives recovery; the fake send runs once,
the provider sees exact live structured system on recovery even with `serve`
still enabled, tool IDs/result references survive, and a second resume neither
calls the provider nor creates another delivery intent. Saved first input is the
served provider input with live rollback artifact; recovered input is live without
that artifact. Source assembly and external provider/send I/O are fake; planner,
executor, journal persistence and continuation are not replaced.

Exact checks:

```sh
python praxis_test.py -q test_agent_resume_runtime.AgentResumeRuntimeTests.test_served_receipt_real_resume_rolls_back_without_repeating_effect
# 1 passed; zero network attempts
python praxis_test.py -q test_agent_resume_runtime test_run_resume test_run_executor test_resume_backoff test_resume_spin test_frame_serve test_frame_measure test_frame_measure_boundary test_frame_shadow test_frame_stats test_frame_plain
# 329 run, OK (1 skipped); zero network attempts
python praxis_test.py -q test_rails test_rails_truth
# 169 passed; zero network attempts
python praxis_test.py -q test_agent_resume_runtime test_run_resume test_run_executor test_resume_backoff test_resume_spin test_frame_serve test_frame_measure test_frame_measure_boundary test_frame_shadow test_frame_stats test_frame_plain test_frame_trace test_rails test_rails_truth
# before author witness correction: 563 run, 1 failure, 1 skipped
# after author witness correction: 563 run, OK (1 skipped); zero network attempts
```

Initial failure (now resolved by author): `test_frame_trace.TheModuleSaysWhatItCannotDo.test_the_lever_stays_out_of_the_rails_manifest_in_this_slice`
asserts `rails.manifest_drift()['ok']`; actual drift is only
`value_stale=['evaluator_mirror']`. Source witness moved `agent.py:15432` to
`agent.py:15441`; no evaluator behavior changed. No assertion/policy weakened.

Canonical mechanism inspected: `rails.py` module contract and `sync_md()`,
`manifest_drift()` documentation, maintenance caller `sleep.py:683`.
Canonical `rails.sync_md()` was invoked with **only `RAILS_MD` redirected to a
temporary output**, never against her soul file. Generated drift is clean.
The sandbox-generated patch was inspected and removed from the deliverable.
It also changes environment-dependent model names, evaluator availability,
room-load annotations, identity/archive counts, pacing and aux budgets: this is
not a line-only regeneration. Do not apply a worker-environment snapshot as live
truth. AGENTS self-authorship invariant and sole maintenance writer contract
require Praxis to accept/synchronize her manifest in the intended environment
through her own maintenance/identity mechanism. No identity event was forged by
this worker. At 20:25 UTC the parent/author explicitly reported acceptance and personally
changed only the source witness `15432` → `15441` in `soul/rails.md`.
This worker preserved that concurrent edit; the complete 563-test command above
was rerun afterwards and passed (1 skipped). The sandbox-generated patch was
not applied and has been removed; it is not a deployment instruction.

An initial direct `python -m pytest -q` run of resume/executor/frame/rails suites
failed additionally because rails attempted `/run/praxis-serverd/serverd.sock`
under inherited production environment; the network fence blocked it. No fence
was disabled. The documented `praxis_test.py` entrypoint strips inherited
production knobs and the runs above remove those environmental failures.
Logs: `/tmp/frame-targeted-gate.log`, `/tmp/frame-rails-gate.log`,
`/tmp/frame-resume-gate.log` (initial combined run), and
`/tmp/frame-resume-gate-after-author.log` (passing combined rerun).

Independent review previously found missing/empty SOUL/VOICE degraded-K handling;
serving now falls back to live for that case. At 20:53 UTC both clean release
runs on commit e6de9b88 completed successfully: each ran 4971 tests, OK (skipped=6),
in 394.066s and 385.071s. Supervisor proc-df77fb50 exited 0. Durable logs:
`/app/workspace/frame-canary-e6de9b88-evidence/praxis-canary-e6de9b88-gate1.log`
(SHA256 a2dcca73809707f8e7797fd744322aa3d1ca81dc01f54fbdc16ec14ba189d23c), and
`/app/workspace/frame-canary-e6de9b88-evidence/praxis-canary-e6de9b88-gate2.log`
(SHA256 2b0f736dcf2572793042b3e4572f7918ad057e442c15f56ae055a20ad4f5c082).
These are clean release gates, not proof of production serving. This tree has
not yet been merged, deployed, restarted or activated.
