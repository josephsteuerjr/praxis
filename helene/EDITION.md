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
of the tree, branch `port/lag-2609`). Regenerate this file with the same numbers before
trusting it: the body of this document is produced by `gen_edition_md.py` of the session
that changed the layer.

## 2026-09-26 — 1.0.1: the lag behind her is closed

Mirror `praxis/`: her live master `9a0897d` (597 files compared) — `ec9092f2` plus
the owner-DM history candidate (`ce890352`, merged into her master that evening) and her own
memory/skill edits outside the code. Working copy: `port/lag-2609` — the 1.0.0 tree (her KEAT
and frame) plus everything of hers it had missed. The layer check alone showed 18 lagging
files; a line-level pass over ALL her history since 15.08 found her edits missing from
declared files too. Taken by her commits (3-way from her nearest version): recall v8
(`memory_fts` claims and builder lock, `memory_index` without a synchronous corpus walk in
explicit recall), resume (a deliberate `blocked` is not rescheduled; a cancellation parked on
an unknown tool outcome is settled by the scanner), Forge (run context captured before
detached workers; a merge re-issues the rails manifest), archive-aware run results, her
runner with the DM candidate, moderation, perception, turns, and 50 of her tests.

The check reports:

| | files |
|---|---:|
| declared here and genuinely differing | **119** |
| existing only in the working copy and NOT declared (a build from the core would refuse) | **0** |
| declared in vain (identical) | **0** |
| declared, but the layer copy is stale | **0** |
| differing but NOT declared | **0** — the lag is closed |
| in the core, not carried here | **3** — see below |

⚠ **What the edition's KEAT does differently, and why it has to.** Her production KEAT
served owner-DM calls with ONE message: her replies go out through the `reply` hand and were
never captured, so the "captured suffix" after her last reply was the owner's last line —
the 49–78 message tape of the legacy path fell out of every served call (measured on her
runs 21–25.09; her own fix since 9a0897d: no projection unless the whole tape is captured).
The edition captures both sides of the owner DM (`localharness/botapi.py`), gives the
projection only when the whole hot tape is captured (`localharness/runner.py`), and its
projection accepts the renderer's delivery receipt — the one message of the reply-hand tape
that has no original — in one exact form derived from the call before it
(`keat_candidate.render_receipt`, `RECEIPT_TRANSFORM` in `keat_capture.project` and
`keat_epoch.validate`). Hence the declared `keat_candidate.py`, `keat_capture.py`,
`keat_epoch.py`, `keat_live.py`. Windows: the capture/epoch locks are `msvcrt` where
`fcntl` does not exist, directory fsync is skipped, file fsync goes through a writable
handle (`keat_epoch.exclusive`). The scheduled-wake seed has no grammatical gender.
`keat_stats.py` is her `frame_stats.py` under another name: the edition has its own
`frame_stats.py` (the 08.09 frame cuts), and KEAT readiness reads hers.

⚠ **Her recall v8 on Windows.** Her refresh machinery (`memory_fts` claims) stands on POSIX:
`flock` on an open file description and `rename`/`unlink` of a file that is still OPEN.
Windows has no `flock`, and `os.open` there gives no `FILE_SHARE_DELETE`, so an open file can
be neither renamed nor removed — taken as is, the claim would fail with `OSError`, a pending
request would never be claimed, and `memory_index.build` would never rebuild. The edition's
branch (`_WINDOWS` in `memory_fts.py`): the only live lock is the builder lock (`msvcrt`,
byte 0 of the stable `recall_builder.lock`), a claim is plain data, and every caller of the
claim functions holds the builder lock — so a foreign claim seen under it is an orphan (its
builder died, e.g. the window was closed mid-rebuild) and is recovered at once. The harness
services the requests every 15 minutes and runs her night cycle (`sleep.run_scheduled`)
between turns — neither existed in the edition before 1.0.1 (`desk/localharness/runner.py`).

⚠ **The body has one source.** Darwin branches (`ax.rs`, `mac.rs`, the identity/process/
runtime forks) live in the working copy's `body/` and in this layer's `body/`; the mirror
does not carry them (her prod does not). The Mac build takes the body from the shipped
`tree/body`, the Windows build from `installer/body_src.py --build` with provenance
(`BODY-BUILT.json`). Since 1.0.0 `process.status` returns the supervisor's own cause
(`supervisor_error`, `job_note`) instead of an empty "failed".

### Declared here and genuinely differing (119)

| `.env.example` | `agent.py` | `appetite.py` |
| `body/Cargo.lock` | `body/crates/praxis-body/Cargo.toml` | `body/crates/praxis-body/src/artifact.rs` |
| `body/crates/praxis-body/src/desktop.rs` | `body/crates/praxis-body/src/dpi.rs` | `body/crates/praxis-body/src/element.rs` |
| `body/crates/praxis-body/src/identity.rs` | `body/crates/praxis-body/src/main.rs` | `body/crates/praxis-body/src/process.rs` |
| `body/crates/praxis-body/src/uia.rs` | `body/crates/praxis-bridge/Cargo.toml` | `body/crates/praxis-bridge/src/main.rs` |
| `body_client.py` | `bootguard.py` | `brain.py` |
| `core/notices.py` | `forge.py` | `forge_intelligence.py` |
| `forge_process.py` | `formation.py` | `frame_layout.py` |
| `frame_shadow.py` | `frame_stats.py` | `frame_trace.py` |
| `group_context.py` | `keat_candidate.py` | `keat_capture.py` |
| `keat_epoch.py` | `keat_live.py` | `keat_readiness.py` |
| `keat_runtime.py` | `llm.py` | `memory_fts.py` |
| `memory_life.py` | `mtproto_runner.py` | `panel.py` |
| `perception.py` | `rooms.py` | `run_manager.py` |
| `runs_prune.py` | `selfdev.py` | `selfgit.py` |
| `sleep.py` | `stewardship.py` | `tasks.py` |
| `test_addressed_media.py` | `test_answer_from_the_source.py` | `test_authored_notes_agent.py` |
| `test_authority_context.py` | `test_cache_prefix_stability.py` | `test_chat_follow_through.py` |
| `test_claim_conflicts.py` | `test_compact_self_anchor_2409.py` | `test_computer_access_agent.py` |
| `test_core_notices_2509.py` | `test_coverage_vs_current.py` | `test_deep_group_context.py` |
| `test_delivery_truth_tail.py` | `test_element_find.py` | `test_fast_hand.py` |
| `test_forge_lean.py` | `test_forge_submission_truth.py` | `test_forge_wake.py` |
| `test_frame_shadow.py` | `test_frame_trace.py` | `test_gate_hermetic.py` |
| `test_glm_effort.py` | `test_guard_soft.py` | `test_heartbeat.py` |
| `test_her_compacts_2509.py` | `test_history_scan.py` | `test_index_and_person.py` |
| `test_keat_dm_ingress.py` | `test_keat_economy_coverage.py` | `test_keat_wake_adapter.py` |
| `test_llm.py` | `test_memory_v2.py` | `test_multimodal_regressions.py` |
| `test_panel.py` | `test_pass11.py` | `test_pass21.py` |
| `test_pass23.py` | `test_pass23_2.py` | `test_pass23_complete.py` |
| `test_pass30.py` | `test_pass30_stage1.py` | `test_pass4.py` |
| `test_pass9.py` | `test_perceive.py` | `test_reply_hand.py` |
| `test_role_envelope_1509.py` | `test_room_authority.py` | `test_room_memory_2509.py` |
| `test_rooms_and_admission.py` | `test_run_integration.py` | `test_run_label_1509.py` |
| `test_run_snapshot_integrity.py` | `test_runner_resolve.py` | `test_say_hand.py` |
| `test_seam_contracts.py` | `test_self_desire_integration.py` | `test_selfdev.py` |
| `test_shell_selfdev.py` | `test_silero_tts_client.py` | `test_silero_tts_worker.py` |
| `test_stewardship.py` | `test_tape_hands_1609.py` | `test_tools_en_1509.py` |
| `test_truncation_owner.py` | `test_truth_agent.py` | `test_truth_runner.py` |
| `test_turns.py` | `test_window_loop.py` | `tool_text_en.py` |
| `turns.py` | `work_loop.py` |  |

### Existing only here (0)

Nothing: every file of the working copy is either her file unchanged or declared above.

### Differing and NOT declared (0) — her work the edition has not taken

Nothing. Every file of the working copy is her file unchanged or declared above; the check
says "the layer matches the actual difference".

### What the edition does NOT carry (3)

Two tests and one document, each for a decision of the edition: `test_frame_stats.py` checks
her `frame_stats.py`, which the edition carries as `keat_stats.py` (covered by
`test_keat_stats.py`) next to its own frame-cuts `frame_stats.py`; `test_tool_pointers.py` pins
her Russian tool pointer, while the edition's pointer is English (`test_pointer_en_1809`);
`docs/run_retention.md` describes her server's cold run archive — the edition does not archive
runs (`runs_prune.py`), and its JSON example (`"object_key": …`) is what the distribution's
secret scan refuses by pattern. They are named rather than filtered: assembling from the core
brings them along.

| `docs/run_retention.md` | `test_frame_stats.py` | `test_tool_pointers.py` |

## How to keep this honest

1. Change the working copy first (`port-…` worktree), then `core_src.py --sync --tree …`
   for declared files, then copy any *new* edition-decided file into `core/` by hand, and
   `git rm` a layer copy that became identical to hers ("declared in vain").
2. Run `core_src.py --check --tree …`; the "not taken" list must stay empty, or shrink with
   a named reason.
3. Re-export the mirror (`core_src.py --export-core --from …`) only after the working copy
   has taken her newer files — the lesson of 2026-09-10: a mirror ahead of the working
   copy makes forty "differences" that are lags in disguise. (26.09, 1.0.1: the export to
   `9a0897d` came after the working copy took her lag; the "not taken" list went 18 → 0.)
4. The layer check sees only DECLARED-or-not; it does not see her edits missing inside a
   declared file. Before a release, pass over her history line by line (her added lines that
   are alive in her head and absent from the working copy) — 1.0.1 found three of her
   commits in `agent.py` and two in `forge.py` that way.
5. Regenerate this document from the check.

History of this file: 14.09 (layer regenerated from `8cb65f14`), 15.09 (bridge and body
joined), 17.09 (14→16.09 port), 25.09 (mirror at `b440156`, the layer after the review),
26.09 (1.0.0: mirror at `ec9092f`, KEAT and her frame in the edition), 26.09 (1.0.1: mirror at
`9a0897d`, the lag behind her closed).
