# Hélène edition of the core — what differs, and why it has to

Hélène is the edition of the agent whose core lives in [`praxis/`](../praxis) (the mirror
of her production tree). The two are not one tree and never will be: a core that lives in a
container on a Linux server and a core that lives inside a portable folder on someone's
PC or Mac differ by the nature of the machine under them. Pretending otherwise produced a
phantom task — "merge the cores" — that could never be finished.

So the difference is declared instead. `core/` holds every file of the agent's core that
Hélène carries differently, and nothing else: everything not listed here comes from
`praxis/` unchanged.

**The declaration is checkable, and every number below came from the check rather than from
memory:** `python desk/installer/core_src.py --check --tree ../port-g-2509` (the working
copy of the tree; on `main` after the merge — `../port-2409`). Regenerate this file with the
same numbers before trusting it: the body of this document is produced by
`scratchpad/gen_edition_md.py` of the session that changed the layer.

## 2026-09-25 (evening) — the layer after the review

Mirror `praxis/`: her `b440156` (586 files compared; `.env.example` now compared
too — `core_src.SKIP_NAMES_ENV` skips only `.env`/`.deploy.env` by exact name). Working copy:
`port/notices-2509` (worktree `port-g-2509`, `SYNC-HEAD.txt` carries the full hash of the
prod base) — prod `b440156` + the edition + the seven candidates of 25.09 that were also
offered to her as `candidate/*` (dedupe of the answered wake, addressee namesakes and
frozen chats, tier-fold overlap, compacts in her own voice, outbox state cache, notices
after review A10, relay terminal after review A1).

The check reports:

| | files |
|---|---:|
| declared here and genuinely differing | **119** |
| existing only in the working copy and NOT declared (a build from the core would refuse) | **0** |
| declared in vain (identical) | **0** |
| declared, but the layer copy is stale | **0** |
| differing but NOT declared | **21** — her lag, see below |
| in the core, not carried here | **73** — see below |

⚠ **What changed on 25.09 in the declaration itself.** Review A11 (25.09) showed that
16 of the 38 files previously called "her lag" were in fact edits of the port that exist in
no commit of hers (`brain.py`, `frame_layout.py`, `frame_trace.py`, `group_context.py`,
`runs_prune.py` and 11 test modules — checked with `git log b440156 --find-object=<blob>`
in her repository for every file). Three of them hold code without which the declared
layer does not run: `agent.py` calls `frame_layout.tape(history, hands=…)`, which her
`tape(messages)` does not accept; `agent.py` writes the frame-trace reason `lever_off`
that her `frame_trace.REASONS` lacks; `localharness/runner.py` imports `runs_prune` whose
Windows retention is written in the port. Taking "her file" for any of the three would
break the frame or silently disable retention. They are declared now, together with the
edits of 25.09 (`telegram_contacts.py`, `telegram_outbox.py`, `work_loop.py`,
`.env.example` — `PRAXIS_ROOM_ENGAGEMENT=addressed` is the port's choice — and the test
modules that pin the edition's receipts). The same classification is what separates the
tables below; it is repeatable, not remembered.

⚠ **The body has one source.** Darwin branches (`ax.rs`, `mac.rs`, the identity/process/
runtime forks) live in the working copy's `body/` and in this layer's `body/`; the mirror
does not carry them (her prod does not). The Mac build takes the body from the shipped
`tree/body` (`build_mac.body_src_for`, strict for release builds since 25.09), the Windows
build from `live/body` — the same crates, and since 25.09 with provenance
(`installer/body_src.py --build` → `_body_target/BODY-BUILT.json`, checked by
`build_dist.py` like the relay's). The passport says `body.source = "tree/body"` and the
commit of the shipped tree.

### Declared here and genuinely differing (119)

| `.env.example` | `ARCHITECTURE.md` | `CODEMAP.md` |
| `_standenv.py` | `agent.py` | `appetite.py` |
| `body/Cargo.lock` | `body/crates/praxis-body/Cargo.toml` | `body/crates/praxis-body/src/artifact.rs` |
| `body/crates/praxis-body/src/desktop.rs` | `body/crates/praxis-body/src/dpi.rs` | `body/crates/praxis-body/src/element.rs` |
| `body/crates/praxis-body/src/identity.rs` | `body/crates/praxis-body/src/main.rs` | `body/crates/praxis-body/src/process.rs` |
| `body/crates/praxis-body/src/uia.rs` | `body/crates/praxis-bridge/Cargo.toml` | `body/crates/praxis-bridge/src/main.rs` |
| `body_client.py` | `bootguard.py` | `brain.py` |
| `canary.py` | `compact_places.py` | `forge.py` |
| `forge_intelligence.py` | `forge_process.py` | `formation.py` |
| `frame_layout.py` | `frame_shadow.py` | `frame_stats.py` |
| `frame_trace.py` | `group_context.py` | `llm.py` |
| `memory_fts.py` | `memory_life.py` | `moderation_shadow.py` |
| `mtproto_runner.py` | `panel.py` | `perception.py` |
| `rooms.py` | `run_manager.py` | `runs_prune.py` |
| `selfdev.py` | `selfgit.py` | `sleep.py` |
| `stewardship.py` | `telegram_contacts.py` | `telegram_outbox.py` |
| `test_addressed_by_default_1609.py` | `test_agent_resume_runtime.py` | `test_answer_from_the_source.py` |
| `test_authored_notes_agent.py` | `test_authority_context.py` | `test_cache_prefix_stability.py` |
| `test_canary.py` | `test_chat_follow_through.py` | `test_claim_conflicts.py` |
| `test_compact_self_anchor_2409.py` | `test_computer_access_agent.py` | `test_coverage_vs_current.py` |
| `test_deep_group_context.py` | `test_delivery_truth_tail.py` | `test_fast_hand.py` |
| `test_forge_lean.py` | `test_forge_submission_truth.py` | `test_forge_wake.py` |
| `test_frame_trace.py` | `test_gate_hermetic.py` | `test_group_wake_snapshot.py` |
| `test_guard_soft.py` | `test_heartbeat.py` | `test_history_scan.py` |
| `test_index_and_person.py` | `test_invariants.py` | `test_llm.py` |
| `test_memory_v2.py` | `test_moderation_shadow.py` | `test_multimodal_regressions.py` |
| `test_panel.py` | `test_pass11.py` | `test_pass21.py` |
| `test_pass23.py` | `test_pass23_2.py` | `test_pass23_complete.py` |
| `test_pass30.py` | `test_pass30_stage1.py` | `test_pass4.py` |
| `test_pass9.py` | `test_perceive.py` | `test_reply_hand.py` |
| `test_role_envelope_1509.py` | `test_room_authority.py` | `test_rooms_and_admission.py` |
| `test_run_integration.py` | `test_run_label_1509.py` | `test_run_snapshot_integrity.py` |
| `test_runner_resolve.py` | `test_say_hand.py` | `test_seam_contracts.py` |
| `test_self_desire_integration.py` | `test_selfdev.py` | `test_shell_selfdev.py` |
| `test_silero_tts_client.py` | `test_silero_tts_worker.py` | `test_stewardship.py` |
| `test_tape_hands_1609.py` | `test_tier_fold_overlap_2109.py` | `test_tools_en_1509.py` |
| `test_truncation_owner.py` | `test_truth_agent.py` | `test_truth_runner.py` |
| `test_turns.py` | `test_webtool.py` | `test_window_loop.py` |
| `tool_text_en.py` | `turns.py` | `unanswered.py` |
| `webtool.py` | `work_loop.py` |  |

### Existing only here (0)



`sitecustomize.py` is imported by the engine explicitly; the rest are the edition's own
test modules (they do not ship in the distribution).

### Differing and NOT declared (21) — her work the edition has not taken

Every file here is byte-identical to some commit of hers up to `b440156` (checked by blob),
i.e. the working copy is *behind* her, not different by design: the recall index rework,
`people.py`, `tasks.py`, `run_resume.py`, `telegram_admin.py` and the tests that moved with
them. They are deliberately not copied into `core/` — copying them into a file called "the
edition" would declare a lag as a design. The next port takes them file by file (each of
`frame_layout.py`, `frame_trace.py`, `group_context.py` is *both* behind her and changed by
the port, so those three need a merge, not a copy). `core_src.py --check` exits non-zero
while this list is not empty, and `build_dist.py --from-core` refuses to assemble — on
purpose.

| `memory_index.py` | `people.py` | `run_resume.py` |
| `tasks.py` | `telegram_admin.py` | `test_call_trace_k1_1309.py` |
| `test_direct_telegram_outbox.py` | `test_dossier_contract.py` | `test_layer7.py` |
| `test_memory_fts.py` | `test_memory_index_adversarial.py` | `test_openai_cache_usage.py` |
| `test_pass19.py` | `test_places_adversarial.py` | `test_resume_spin.py` |
| `test_run_manager.py` | `test_runner_reconnect.py` | `test_telegram_admin.py` |
| `test_transport_not_memory.py` | `test_whole_documents.py` | `test_work_wait_survives.py` |

### What the edition does NOT carry (73)

Files that exist in the core and not in this edition — mostly her KEAT frame and runs
retention modules (`keat_*`, `frame_measure`, `frame_serve`, `pre_model_timing`,
`telegram_text`, `run_retention`, `logical_send`), their docs and tests, and the review
notes. Nothing in the edition imports them. They are named rather than filtered: assembling
from the core brings them along, and the distribution grows by exactly this list.

| `CONTRIBUTORS.md` | `docs/frame-v6-canary.md` | `docs/keat-candidate-contract.md` |
| `docs/keat-dm-staged-widening.md` | `docs/keat-durable-epoch.md` | `docs/keat-ingress-history-gaps.md` |
| `docs/keat-input-boundaries.md` | `docs/keat-runtime-staging.md` | `docs/keat-source-adapter.md` |
| `docs/reviews/1009-client-bridge-repair.md` | `docs/reviews/1009-descriptor-test-repair.md` | `docs/reviews/1009-element-act.md` |
| `docs/reviews/1009-rust-validation.md` | `docs/reviews/1009-uia-repair.md` | `docs/run_retention.md` |
| `frame_epoch.py` | `frame_measure.py` | `frame_serve.py` |
| `keat_candidate.py` | `keat_capture.py` | `keat_control.py` |
| `keat_economy.py` | `keat_epoch.py` | `keat_live.py` |
| `keat_readiness.py` | `keat_runtime.py` | `keat_source.py` |
| `logical_send.py` | `pre_model_timing.py` | `telegram_text.py` |
| `test_boundary_turn_delivery.py` | `test_call_attribution.py` | `test_citation_audit_1609.py` |
| `test_compacts_parse_cache_1609.py` | `test_element_find.py` | `test_frame_epoch_1509.py` |
| `test_frame_levers_1309.py` | `test_frame_measure.py` | `test_frame_measure_boundary.py` |
| `test_frame_serve.py` | `test_frame_stats.py` | `test_glm_effort.py` |
| `test_head_stable_1309.py` | `test_keat_agent_ingress.py` | `test_keat_candidate.py` |
| `test_keat_capture.py` | `test_keat_control.py` | `test_keat_dm_ingress.py` |
| `test_keat_economy.py` | `test_keat_economy_coverage.py` | `test_keat_epoch.py` |
| `test_keat_group_root.py` | `test_keat_group_runner_rollback.py` | `test_keat_live.py` |
| `test_keat_native_ingress.py` | `test_keat_provider_rollback.py` | `test_keat_readiness.py` |
| `test_keat_runtime.py` | `test_keat_source.py` | `test_keat_wake_adapter.py` |
| `test_mtproto_hot_window.py` | `test_outbox_cancel_late_2109.py` | `test_outbox_citation_1409.py` |
| `test_outbox_settled_1309.py` | `test_pre_model_timing.py` | `test_run_archive_read.py` |
| `test_run_events_missing_1309.py` | `test_run_resume_model_input_fallback.py` | `test_run_retention.py` |
| `test_telegram_text.py` | `test_tool_pointers.py` | `test_truncated_tool_use.py` |
| `tools/cache_saw_daily.py` |  |  |

## How to keep this honest

1. Change the working copy first (`port-…` worktree), then `core_src.py --sync --tree …`
   for declared files, then copy any *new* edition-decided file into `core/` by hand.
2. Run `core_src.py --check --tree …`; the only acceptable non-zero cause is the "her lag"
   list above, and it must shrink, never grow, without a named reason.
3. Re-export the mirror (`core_src.py --export-core --from …`) only after the working copy
   has taken her newer files — the lesson of 2026-09-10: a mirror ahead of the working
   copy makes forty "differences" that are lags in disguise.
4. Regenerate this document from the check.

History of this file: 14.09 (layer regenerated from `8cb65f14`), 15.09 (bridge and body
joined), 17.09 (14→16.09 port), 25.09 morning (mirror re-exported at `b440156`), 25.09
evening (this revision).
