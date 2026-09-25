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
memory:** `python desk/installer/core_src.py --check --tree ../port-2409` (the working copy
of the tree, branch `port/keat-2609`). Regenerate this file with the same numbers before
trusting it: the body of this document is produced by `gen_edition_md.py` of the session
that changed the layer.

## 2026-09-26 — 1.0.0: her frame and KEAT in the edition

Mirror `praxis/`: her live master `ec9092f` (597 files compared) — `b440156` plus
the eight candidates of 25.09 (all merged into her master that day) and the slow-turns
caches. Working copy: `port/keat-2609` — the edition tree of 0.9.0 plus the port of her
KEAT and frame machinery (`keat_*`, `frame_serve`, `frame_measure`, `frame_epoch`,
`pre_model_timing`, `logical_send`, `telegram_text`; the hooks in `agent.py`, `memory_life`,
`frame_layout`/`frame_shadow`/`frame_trace`, `people`, `body_client`, `tasks`, `run_resume`,
`group_context`, `mtproto_runner`).

The check reports:

| | files |
|---|---:|
| declared here and genuinely differing | **127** |
| existing only in the working copy and NOT declared (a build from the core would refuse) | **0** |
| declared in vain (identical) | **0** |
| declared, but the layer copy is stale | **0** |
| differing but NOT declared | **18** — her work the edition has not taken, see below |
| in the core, not carried here | **36** — see below |

⚠ **What the edition's KEAT does differently, and why it has to.** Her production KEAT
served owner-DM calls with ONE message: her replies go out through the `reply` hand and were
never captured, so the "captured suffix" after her last reply was the owner's last line —
the 49–78 message tape of the legacy path fell out of every served call (measured on her
runs 21–25.09). The edition captures both sides of the owner DM (`localharness/botapi.py`),
gives the projection only when the whole hot tape is captured (`localharness/runner.py`),
and its projection accepts the renderer's delivery receipt — the one message of the
reply-hand tape that has no original — in one exact form derived from the call before it
(`keat_candidate.render_receipt`, `RECEIPT_TRANSFORM` in `keat_capture.project` and
`keat_epoch.validate`). Hence the declared `keat_candidate.py`, `keat_capture.py`,
`keat_epoch.py`, `keat_live.py`. Windows: the capture/epoch locks are `msvcrt` where
`fcntl` does not exist, directory fsync is skipped, file fsync goes through a writable
handle (`keat_epoch.exclusive`). The scheduled-wake seed has no grammatical gender.
`keat_stats.py` is her `frame_stats.py` under another name: the edition has its own
`frame_stats.py` (the 08.09 frame cuts), and KEAT readiness reads hers.

⚠ **The body has one source.** Darwin branches (`ax.rs`, `mac.rs`, the identity/process/
runtime forks) live in the working copy's `body/` and in this layer's `body/`; the mirror
does not carry them (her prod does not). The Mac build takes the body from the shipped
`tree/body`, the Windows build from `installer/body_src.py --build` with provenance
(`BODY-BUILT.json`). Since 1.0.0 `process.status` returns the supervisor's own cause
(`supervisor_error`, `job_note`) instead of an empty "failed".

### Declared here and genuinely differing (127)

| `.env.example` | `ARCHITECTURE.md` | `agent.py` |
| `appetite.py` | `body/Cargo.lock` | `body/crates/praxis-body/Cargo.toml` |
| `body/crates/praxis-body/src/artifact.rs` | `body/crates/praxis-body/src/desktop.rs` | `body/crates/praxis-body/src/dpi.rs` |
| `body/crates/praxis-body/src/element.rs` | `body/crates/praxis-body/src/identity.rs` | `body/crates/praxis-body/src/main.rs` |
| `body/crates/praxis-body/src/process.rs` | `body/crates/praxis-body/src/uia.rs` | `body/crates/praxis-bridge/Cargo.toml` |
| `body/crates/praxis-bridge/src/main.rs` | `body_client.py` | `bootguard.py` |
| `brain.py` | `canary.py` | `core/notices.py` |
| `forge.py` | `forge_intelligence.py` | `forge_process.py` |
| `formation.py` | `frame_layout.py` | `frame_shadow.py` |
| `frame_stats.py` | `frame_trace.py` | `group_context.py` |
| `keat_candidate.py` | `keat_capture.py` | `keat_epoch.py` |
| `keat_live.py` | `keat_readiness.py` | `keat_runtime.py` |
| `llm.py` | `memory_fts.py` | `memory_life.py` |
| `moderation_shadow.py` | `mtproto_runner.py` | `panel.py` |
| `perception.py` | `rooms.py` | `run_manager.py` |
| `runs_prune.py` | `selfdev.py` | `selfgit.py` |
| `sleep.py` | `stewardship.py` | `tasks.py` |
| `telegram_outbox.py` | `test_addressed_media.py` | `test_agent_resume_runtime.py` |
| `test_answer_from_the_source.py` | `test_authored_notes_agent.py` | `test_authority_context.py` |
| `test_cache_prefix_stability.py` | `test_canary.py` | `test_chat_follow_through.py` |
| `test_claim_conflicts.py` | `test_compact_self_anchor_2409.py` | `test_computer_access_agent.py` |
| `test_core_notices_2509.py` | `test_coverage_vs_current.py` | `test_deep_group_context.py` |
| `test_delivery_truth_tail.py` | `test_fast_hand.py` | `test_forge_lean.py` |
| `test_forge_submission_truth.py` | `test_forge_wake.py` | `test_frame_shadow.py` |
| `test_frame_trace.py` | `test_gate_hermetic.py` | `test_group_wake_snapshot.py` |
| `test_guard_soft.py` | `test_heartbeat.py` | `test_her_compacts_2509.py` |
| `test_history_scan.py` | `test_index_and_person.py` | `test_invariants.py` |
| `test_keat_economy_coverage.py` | `test_llm.py` | `test_memory_v2.py` |
| `test_moderation_shadow.py` | `test_multimodal_regressions.py` | `test_panel.py` |
| `test_pass11.py` | `test_pass21.py` | `test_pass23.py` |
| `test_pass23_2.py` | `test_pass23_complete.py` | `test_pass30.py` |
| `test_pass30_stage1.py` | `test_pass4.py` | `test_pass9.py` |
| `test_perceive.py` | `test_reply_hand.py` | `test_role_envelope_1509.py` |
| `test_room_authority.py` | `test_room_memory_2509.py` | `test_rooms_and_admission.py` |
| `test_run_integration.py` | `test_run_label_1509.py` | `test_run_snapshot_integrity.py` |
| `test_runner_resolve.py` | `test_say_hand.py` | `test_seam_contracts.py` |
| `test_self_desire_integration.py` | `test_selfdev.py` | `test_shell_selfdev.py` |
| `test_silero_tts_client.py` | `test_silero_tts_worker.py` | `test_stewardship.py` |
| `test_tape_hands_1609.py` | `test_tools_en_1509.py` | `test_truncation_owner.py` |
| `test_truth_agent.py` | `test_truth_runner.py` | `test_turns.py` |
| `test_webtool.py` | `test_window_loop.py` | `tool_text_en.py` |
| `turns.py` | `unanswered.py` | `webtool.py` |
| `work_loop.py` |  |  |

### Existing only here (0)

Nothing: every file of the working copy is either her file unchanged or declared above.

### Differing and NOT declared (18) — her work the edition has not taken

Every file here is byte-identical to some commit of hers (checked by blob, the commit is
named), i.e. the working copy is *behind* her, not different by design: the recall index
rework (`memory_index.py`), `telegram_admin.py` and the tests that moved with them. They are
deliberately not copied into `core/` — copying them into a file called "the edition" would
declare a lag as a design. `core_src.py --check` exits non-zero while this list is not empty,
and `build_dist.py --from-core` refuses to assemble — on purpose.

| file | same bytes as her commit |
|---|---|
| `memory_index.py` | `32941ae` |
| `telegram_admin.py` | `6979772` |
| `test_call_trace_k1_1309.py` | `3ad76fd` |
| `test_direct_telegram_outbox.py` | `7eec744` |
| `test_dossier_contract.py` | `7eec744` |
| `test_layer7.py` | `32941ae` |
| `test_memory_fts.py` | `f6a683f` |
| `test_memory_index_adversarial.py` | `f6a683f` |
| `test_openai_cache_usage.py` | `32941ae` |
| `test_pass19.py` | `32941ae` |
| `test_places_adversarial.py` | `074e34c` |
| `test_resume_spin.py` | `77256cf` |
| `test_run_manager.py` | `885651d` |
| `test_runner_reconnect.py` | `85d52b1` |
| `test_telegram_admin.py` | `6979772` |
| `test_transport_not_memory.py` | `9b64400` |
| `test_whole_documents.py` | `9b64400` |
| `test_work_wait_survives.py` | `77256cf` |

### What the edition does NOT carry (36)

Files that exist in the core and not in this edition: her KEAT design notes and reviews
(`docs/`), the tests of her Telegram runner integration that the edition does not run
(`mtproto_runner` is not started by the product harness; KEAT ingress is tested at the
harness seam — `desk/tests/t_keat_owner_stream.py`, `t_keat_live_tree.py`), her
Russian tool-pointer test (the edition's pointer is English and pinned by
`test_pointer_en_1809`), her runs-retention module (the edition has `runs_prune.py`) and
the daily cache-saw tool. They are named rather than filtered: assembling from the core
brings them along, and the distribution grows by exactly this list.

| `CONTRIBUTORS.md` | `docs/frame-v6-canary.md` | `docs/keat-candidate-contract.md` |
| `docs/keat-dm-staged-widening.md` | `docs/keat-durable-epoch.md` | `docs/keat-ingress-history-gaps.md` |
| `docs/keat-input-boundaries.md` | `docs/keat-runtime-staging.md` | `docs/keat-source-adapter.md` |
| `docs/reviews/1009-client-bridge-repair.md` | `docs/reviews/1009-descriptor-test-repair.md` | `docs/reviews/1009-element-act.md` |
| `docs/reviews/1009-rust-validation.md` | `docs/reviews/1009-uia-repair.md` | `docs/run_retention.md` |
| `test_boundary_turn_delivery.py` | `test_call_attribution.py` | `test_compacts_parse_cache_1609.py` |
| `test_element_find.py` | `test_frame_stats.py` | `test_glm_effort.py` |
| `test_keat_dm_ingress.py` | `test_keat_group_root.py` | `test_keat_group_runner_rollback.py` |
| `test_keat_native_ingress.py` | `test_keat_wake_adapter.py` | `test_mtproto_hot_window.py` |
| `test_outbox_cancel_late_2109.py` | `test_outbox_citation_1409.py` | `test_outbox_settled_1309.py` |
| `test_run_archive_read.py` | `test_run_events_missing_1309.py` | `test_run_retention.py` |
| `test_tool_pointers.py` | `test_turn_dedupe_2409.py` | `tools/cache_saw_daily.py` |

## How to keep this honest

1. Change the working copy first (`port-…` worktree), then `core_src.py --sync --tree …`
   for declared files, then copy any *new* edition-decided file into `core/` by hand.
2. Run `core_src.py --check --tree …`; the only acceptable non-zero cause is the "her work
   not taken" list above, and it must shrink, never grow, without a named reason.
3. Re-export the mirror (`core_src.py --export-core --from …`) only after the working copy
   has taken her newer files — the lesson of 2026-09-10: a mirror ahead of the working
   copy makes forty "differences" that are lags in disguise. (26.09: the export to `ec9092f`
   came after the port of her candidates and KEAT; the "not taken" list went 21 → 18.)
4. Regenerate this document from the check.

History of this file: 14.09 (layer regenerated from `8cb65f14`), 15.09 (bridge and body
joined), 17.09 (14→16.09 port), 25.09 (mirror at `b440156`, the layer after the review),
26.09 (1.0.0: mirror at `ec9092f`, KEAT and her frame in the edition).
