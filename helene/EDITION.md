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

⚠ The order matters and it cost a lesson. When the core was re-exported on 2026-09-10 the
mirror moved ahead of the working copy, and forty files differed without being declared.
Copying them into a file called "the Windows edition" would have declared a lag as a
design. The working copy caught up first — taking her fixes where they apply, keeping the
edition where the machine underneath is genuinely different — and only then was the layer
regenerated from what actually differs.

## What is here — 55 files, checked rather than remembered

`desk/installer/core_src.py --check` compares this layer against the ACTUAL difference
between `praxis/` and the working copy. On 2026-09-10, after the core was re-exported from
production (`64f1588c`) and the working copy took her fixes that apply, it reports:

| | files |
|---|---:|
| declared here and genuinely differing | **50** |
| differing but NOT declared | **0** |
| declared in vain (identical) | **0** |
| ours alone and not declared | **0** |

Overlaying `praxis/` with `core/` reproduces the working copy **byte for byte**: 478 of 478
files, nothing diverging, nothing missing. That is what makes
`build_dist.py --from-core` possible at all; before this it refused, and rightly.

### Carried differently (50)

| `ARCHITECTURE.md` | `agent.py` | `body/crates/praxis-body/src/desktop.rs` |
| `body/crates/praxis-body/src/main.rs` | `body/crates/praxis-body/src/runtime.rs` | `body/crates/praxis-body/src/uia.rs` |
| `body_client.py` | `canary.py` | `forge.py` |
| `frame_shadow.py` | `frame_stats.py` | `llm.py` |
| `moderation_shadow.py` | `mtproto_runner.py` | `panel.py` |
| `perception.py` | `run_manager.py` | `test_agent_resume_runtime.py` |
| `test_cache_prefix_stability.py` | `test_canary.py` | `test_claim_conflicts.py` |
| `test_computer_access_agent.py` | `test_coverage_vs_current.py` | `test_fast_hand.py` |
| `test_forge_lean.py` | `test_gate_hermetic.py` | `test_group_wake_snapshot.py` |
| `test_history_scan.py` | `test_invariants.py` | `test_llm.py` |
| `test_moderation_shadow.py` | `test_panel.py` | `test_pass21.py` |
| `test_pass23.py` | `test_pass23_2.py` | `test_pass23_complete.py` |
| `test_pass30.py` | `test_pass9.py` | `test_run_integration.py` |
| `test_shell_selfdev.py` | `test_silero_tts_client.py` | `test_silero_tts_worker.py` |
| `test_truncation_owner.py` | `test_truth_runner.py` | `test_turns.py` |
| `test_vision_switch.py` | `test_webtool.py` | `turns.py` |
| `unanswered.py` | `webtool.py` | |

### Existing only here (5)

| `body/crates/praxis-body/src/element.rs` | `sitecustomize.py` | `test_atomic_replace_retry.py` |
| `test_compact_refresh.py` | `test_element_act.py` | |

## What the edition does NOT carry (26)

The third category, and the one a layer cannot express by itself: files that exist in the
core and not in this edition. Most are her newest modules, written after the last time the
working copy was reconciled, and nothing here imports them — `keat_*`, `frame_measure`,
`frame_serve`, `pre_model_timing`, `telegram_text` and their docs.

They are named rather than filtered: assembling from the core brings them along, and the
distribution grows by exactly this list. Silently dropping them would be a second, hidden
declaration; silently shipping them without saying so would be worse.

| `docs/frame-v6-canary.md` | `docs/keat-candidate-contract.md` | `docs/keat-durable-epoch.md` |
| `docs/keat-input-boundaries.md` | `docs/keat-source-adapter.md` | `frame_measure.py` |
| `frame_serve.py` | `keat_candidate.py` | `keat_epoch.py` |
| `keat_source.py` | `pre_model_timing.py` | `telegram_text.py` |
| `test_boundary_turn_delivery.py` | `test_call_attribution.py` | `test_frame_measure.py` |
| `test_frame_measure_boundary.py` | `test_frame_serve.py` | `test_frame_stats.py` |
| `test_glm_effort.py` | `test_keat_candidate.py` | `test_keat_epoch.py` |
| `test_keat_source.py` | `test_llm_inbox_audit.py` | `test_pre_model_timing.py` |
| `test_telegram_text.py` | `test_truncated_tool_use.py` | |

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

One patch is still offered and not yet taken: `desktop.element.act` — acting on a named
element through UI Automation patterns instead of a point on screen. It is not in `core/`
either, because it was written on 2026-09-10 and its letter went out the same day.

The numbers on this page drift the moment either side moves, and a drifting number that
nobody re-measures is the same lie the phantom "one core" was. There is now an instrument;
use it.
