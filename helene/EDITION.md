# Hélène edition of the core — what differs, and why it has to

Hélène is the Windows edition of the agent whose core lives in [`praxis/`](../praxis).
The two are not one tree and never will be: a core that lives in a container on a Linux
server and a core that lives inside a portable folder on someone's PC differ by the nature
of the machine under them. Pretending otherwise produced a phantom task — "merge the cores"
— that could never be finished.

So the difference is declared instead. `core/` holds every file of the agent's core that
Hélène carries differently, and nothing else: everything not listed here comes from
`praxis/` unchanged.

**The declaration is checkable, and every number below came from the check rather than from
memory.** Run `desk/installer/core_src.py --check` before trusting any of them.

**2026-09-25 — the mirror re-exported, the layer honest again.** The mirror `praxis/` now
stands at her `b440156` (585 files; her committed work logs — `rep*.txt`, `*.log`, `*.pid` —
are junk by rule now, `core_src.JUNK_SUFFIXES`, not by hand). The working copy
(`port/sync-2409`, worktree `port-2409`) took her 24–25.09 work: vision routing by catalog,
memory anti-mill and refresh, the "foreign I" fence in compaction, delivery, selfdev,
Forge custody — plus the edition's own changes (gender-free tool texts, an identity-generic
compaction prompt, typed relay terminals, the body result truth). `core_src.py --check
--tree ../../port-2409` reports: **layer 90 files, drifted 0, stale 0, gone 0; 38 files
differing and NOT declared** — her work the edition still has not taken (`brain.py`,
`frame_layout.py`, `frame_trace.py`, `group_context.py`, `memory_fts.py`, `memory_index.py`,
`people.py`, `run_resume.py`, `runs_prune.py`, `tasks.py`, `telegram_admin.py` and 27 test
modules), **5 only ours** (edition tests). Nothing in those 38 is an edition decision; they
are lag, and the next port takes them file by file.

⚠ **The body has one source now.** Darwin branches (`ax.rs`, `mac.rs`, the identity/process/
runtime forks) live in the working copy's `body/` and in this layer's `body/`; the mirror does
not carry them (her prod does not). The Mac build takes the body from the shipped `tree/body`
(`build_mac.body_src_for`), the Windows build from `live/body` — the same crates. The mirror's
`praxis/body` is her Windows-only body, as on prod.

⚠ The order matters and it cost a lesson. When the core was re-exported on 2026-09-10 the
mirror moved ahead of the working copy, and forty files differed without being declared.
Copying them into a file called "the Windows edition" would have declared a lag as a
design. The working copy caught up first — taking her fixes where they apply, keeping the
edition where the machine underneath is genuinely different — and only then was the layer
regenerated from what actually differs.

## What is here — 61 files, checked rather than remembered

`desk/installer/core_src.py --check --tree ../live` compares this layer against the ACTUAL
difference between `praxis/` and the working copy. On 2026-09-14, after the core was
re-exported from production (her `8cb65f14`) and the working copy took her five fixes of
13–14.09 that apply to the edition (partitioned search and inbox-relative paths, the
outbox acceptance grace and the outbox tick on its own clock, the K1 cache-ledger fields
with an explicit zero, `PRAXIS_AUTO_RECALL_K=0` honoured by the heartbeat, and the
interrupt request from the desk), and on 2026-09-15 two files of the body and the bridge
joined the layer (below), it reports:

| | files |
|---|---:|
| declared here and genuinely differing | **58** |
| declared here and existing only in the edition | **3** |
| declared in vain (identical) | **0** |
| differing but NOT declared | **29** — see below |
| in the core, not carried here | **70** — see below |

### Carried differently (58)

| `ARCHITECTURE.md` | `agent.py` | `body/crates/praxis-body/src/artifact.rs` |
| `body/crates/praxis-body/src/desktop.rs` | `body/crates/praxis-body/src/dpi.rs` | `body/crates/praxis-body/src/element.rs` |
| `body/crates/praxis-body/src/runtime.rs` | `body/crates/praxis-body/src/uia.rs` | `body/crates/praxis-bridge/src/main.rs` |
| `body_client.py` | `bootguard.py` |  |
| `canary.py` | `forge.py` | `forge_intelligence.py` |
| `forge_process.py` | `frame_shadow.py` | `frame_stats.py` |
| `llm.py` | `moderation_shadow.py` | `mtproto_runner.py` |
| `panel.py` | `perception.py` | `run_manager.py` |
| `selfdev.py` | `selfgit.py` | `test_agent_resume_runtime.py` |
| `test_canary.py` | `test_claim_conflicts.py` | `test_computer_access_agent.py` |
| `test_coverage_vs_current.py` | `test_fast_hand.py` | `test_forge_lean.py` |
| `test_gate_hermetic.py` | `test_group_wake_snapshot.py` | `test_history_scan.py` |
| `test_invariants.py` | `test_llm.py` | `test_memory_v2.py` |
| `test_moderation_shadow.py` | `test_panel.py` | `test_pass21.py` |
| `test_pass23.py` | `test_pass23_2.py` | `test_pass23_complete.py` |
| `test_pass30.py` | `test_pass9.py` | `test_run_integration.py` |
| `test_shell_selfdev.py` | `test_silero_tts_client.py` | `test_silero_tts_worker.py` |
| `test_truncation_owner.py` | `test_truth_runner.py` | `test_turns.py` |
| `test_vision_switch.py` | `test_webtool.py` | `turns.py` |
| `unanswered.py` | `webtool.py` |  |

**2026-09-15 — the bridge and the body joined the layer.** Two fixes to a proxy incident
on a user's machine (`desk-notes/ПРОКСИ-И-МЕНЮ-ЧАТА-15.09.md`): the bridge stops warning
`peer outbound queue is closed` when simply nobody of the other role is attached — for a
desk whose controller is an HTTP poller that is every frame, 276 a day, and read as a
broken link it cost a day of hunting; and the body's artifact client goes past an
`HTTP_PROXY` in the environment when the bridge is on loopback, which on this machine it
always is. Both are edition code by the owner's decision of 2026-09-15: Praxis and Hélène
keep their own cores, and a fix made here is not carried across by default. The
consequence is stated rather than hidden — the core's bridge keeps the old log line until
someone changes it there.

### Existing only here (8)

| `sitecustomize.py` | `test_atomic_replace_retry.py` | `test_compact_refresh.py` |
| `tool_text_en.py` | `test_tools_en_1509.py` | `test_tape_hands_1609.py` |
| `test_addressed_by_default_1609.py` | `test_role_envelope_1509.py` | `test_search_chats_1509.py` |
| `test_media_survives_note_1609.py` | `test_brain_fallback_pin_1509.py` | |

⚠ The last eight arrived on 17.09 with the 14→16.09 port. `tool_text_en.py` is a *copy* of
the core file, not an edition invention — it counts as "only here" because the mirror in
`praxis/` is still the snapshot of 14.09 and does not have it yet. The same holds for six of
the seven test modules. They stop being "only here" the moment the mirror is re-exported;
until then this line is the honest statement of where they came from.

## Differing and NOT declared (35) — the core moved ahead, the edition has not caught up

These are not edition differences and are deliberately not copied into `core/`: they are
her own work of 13–14.09 that the edition has not taken yet, plus — since 17.09 —
six files the port changed *ahead* of the mirror (`rooms.py`, `frame_layout.py`,
`group_context.py`, `brain.py` and the two above them): the mirror in `praxis/` is the
snapshot of 14.09 (`e39af273`), and these carry 16.09 work. They will fall back into the
declared set when the mirror is re-exported, and that re-export is bookkeeping, not product —
see the note at the end. The rest is — the recall index rework
(bounded foreground validation, durable background refresh), the memory-life and
provenance changes behind it, `people.py`, `sleep.py`, `tasks.py`, `run_resume.py`,
`frame_trace.py`, `frame_layout.py`, and the tests that moved with them. Copying them into
a file called "the Windows edition" would declare a lag as a design (the lesson of
2026-09-10, above). The edition ships the 0.5.5 versions of these files. To close the gap:
reconcile the working copy with `praxis/` file by file, then re-run the check.

⚠ On 2026-09-15 this number grew from 27 to 29, and the growth is the
mirror telling the truth rather than a regression. Until that day `praxis/` was refreshed
by hand, which meant it was not refreshed: part of the difference was hidden behind the
mirror's own age. `installer/core_src.py --export-core` now moves her tree into the mirror
in one command — with a secret scan before the write and a `CORE-SOURCE.json` beside it
saying which of her commits this is. The mirror stands at her `e39af273`, and every file
below is the working copy lagging her, nothing else.

| `frame_layout.py` | `frame_trace.py` | `memory_fts.py` |
| `memory_index.py` | `memory_life.py` | `people.py` |
| `run_resume.py` | `sleep.py` | `tasks.py` |
| `test_authority_context.py` | `test_cache_prefix_stability.py` | `test_call_trace_k1_1309.py` |
| `test_direct_telegram_outbox.py` | `test_dossier_contract.py` | `test_heartbeat.py` |
| `test_layer7.py` | `test_memory_fts.py` | `test_memory_index_adversarial.py` |
| `test_openai_cache_usage.py` | `test_pass19.py` | `test_perceive.py` |
| `test_places_adversarial.py` | `test_resume_spin.py` | `test_rooms_and_admission.py` |
| `test_run_manager.py` | `test_runner_reconnect.py` | `test_transport_not_memory.py` |
| `test_whole_documents.py` | `test_work_wait_survives.py` |  |

## What the edition does NOT carry (70)

The third category, and the one a layer cannot express by itself: files that exist in the
core and not in this edition. Most are her modules of the KEAT frame and the runs
retention — `keat_*`, `frame_measure`, `frame_serve`, `pre_model_timing`, `telegram_text`,
`run_retention`, `runs_prune`, `logical_send`, their docs and tests — plus the 1009
review notes. Nothing in the edition imports them.

They are named rather than filtered: assembling from the core brings them along, and the
distribution grows by exactly this list. Silently dropping them would be a second, hidden
declaration; silently shipping them without saying so would be worse.

| `docs/frame-v6-canary.md` | `docs/keat-candidate-contract.md` | `docs/keat-dm-staged-widening.md` |
| `docs/keat-durable-epoch.md` | `docs/keat-ingress-history-gaps.md` | `docs/keat-input-boundaries.md` |
| `docs/keat-runtime-staging.md` | `docs/keat-source-adapter.md` | `docs/reviews/1009-client-bridge-repair.md` |
| `docs/reviews/1009-descriptor-test-repair.md` | `docs/reviews/1009-element-act.md` | `docs/reviews/1009-rust-validation.md` |
| `docs/reviews/1009-uia-repair.md` | `docs/run_retention.md` | `frame_measure.py` |
| `frame_serve.py` | `keat_candidate.py` | `keat_capture.py` |
| `keat_control.py` | `keat_economy.py` | `keat_epoch.py` |
| `keat_live.py` | `keat_readiness.py` | `keat_runtime.py` |
| `keat_source.py` | `logical_send.py` | `pre_model_timing.py` |
| `run_retention.py` | `runs_prune.py` | `telegram_text.py` |
| `test_boundary_turn_delivery.py` | `test_call_attribution.py` | `test_element_find.py` |
| `test_frame_levers_1309.py` | `test_frame_measure.py` | `test_frame_measure_boundary.py` |
| `test_frame_serve.py` | `test_frame_stats.py` | `test_glm_effort.py` |
| `test_head_stable_1309.py` | `test_keat_agent_ingress.py` | `test_keat_candidate.py` |
| `test_keat_capture.py` | `test_keat_control.py` | `test_keat_dm_ingress.py` |
| `test_keat_economy.py` | `test_keat_economy_coverage.py` | `test_keat_epoch.py` |
| `test_keat_group_root.py` | `test_keat_group_runner_rollback.py` | `test_keat_live.py` |
| `test_keat_native_ingress.py` | `test_keat_provider_rollback.py` | `test_keat_readiness.py` |
| `test_keat_runtime.py` | `test_keat_source.py` | `test_keat_wake_adapter.py` |
| `test_llm_inbox_audit.py` | `test_mtproto_hot_window.py` | `test_outbox_citation_1409.py` |
| `test_outbox_settled_1309.py` | `test_pre_model_timing.py` | `test_run_archive_read.py` |
| `test_run_events_missing_1309.py` | `test_run_resume_model_input_fallback.py` | `test_run_retention.py` |
| `test_runs_prune.py` | `test_telegram_text.py` | `test_tool_pointers.py` |
| `test_truncated_tool_use.py` |  |  |

## The honest part

Some of what was here was not an edition at all — it was a patch waiting for its author.
The vision routing, the effort step for GLM, the call ledger and the window-reading hand
were written for Hélène and offered to the core; until taken there, they lived here, and
this table was the only place that said so out loud.

**They were taken.** The core's own commit of 2026-09-10 — "GLM effort wire dialect and
durable Forge call attribution" — carries the effort step (`output_config.effort` in
`llm.py`, the z.ai Anthropic dialect) and the call ledger (`forge_task_id` in `llm.py` and
`forge_worker.py`); the vision routing and `read_window` are in there too. All four are now
the core's, not an edition of it, and the tables above no longer count them.

The `desktop.element.act` patch offered on 2026-09-10 — acting on a named element through
UI Automation patterns instead of a point on screen — has been taken too: on 2026-09-14
`test_element_act.py` and the body crate are identical on both sides, and they left this
layer.

The numbers on this page drift the moment either side moves, and a drifting number that
nobody re-measures is the same lie the phantom "one core" was. There is now an instrument;
use it.
