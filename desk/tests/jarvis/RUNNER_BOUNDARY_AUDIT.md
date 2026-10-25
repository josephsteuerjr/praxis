# Runner reply-only boundary audit — code-34bcff44

## Scope and result

Confirmed and fixed: `boundary_word` promoted `held=unspoken` internal notes into outbound text (including `Жду:` / `Препятствие:`). It now returns BLANK without reading `note`, regardless of prefixes, word count, legacy deliver_unspoken opt-in, birth, or available/truncated tool trace. Explicit envelope text and reply-hand delivery remain in their existing caller paths. `held=voice` retains SILENCE with normalized `why` and the window's system silence plaque.

Only production edit: the body/docstring of `localharness/runner.py:boundary_word`. No tree engine, config, services, restart, external messages, or live model calls.

## Preserved baseline (before edits)

Outside app: `C:/Program Files/Helene/data/workspace/code-34bcff44-evidence/runner-boundary/`

- `runner.original.py`: exact bytes, 224443 bytes; SHA256 `9feb57cc114a57ed16e2bdcc8fbc11c122062e6df1b039a79352416df5bde75b`
- `baseline.json`: source/baseline paths, hash, size
- `red.log`, `green.log`: complete targeted test output against original and patched source
- `runner-boundary.diff`: exact baseline-to-patch diff at verification time
- `patched-hashes.json`: runner/test hashes at verification time

Patched runner SHA256 at worker verification: `74c9cc33cfda3132a319973ecc61c4c5dbd220682f8fd720c6ecc25d18ad54ef`. Parent may subsequently integrate separately authorized runner changes; recheck combined diff.

## Regression evidence

`python -m unittest discover -s tests -p test_runner_boundary_audit.py -v`

- RED on untouched source: exit 1; 5 test methods, 51 failing subcases. Repeated against the preserved baseline through `RUNNER_BOUNDARY_SOURCE`, with full output in `red.log`.
- GREEN patched: exit 0; 5 test methods pass, full output in `green.log`.
- `python -m py_compile localharness/runner.py tests/test_runner_boundary_audit.py`: exit 0.
- Tool logs: green `20261024-031748-3699.log`; compile `20261024-031749-1228.log`; preserved red/green rerun `20261024-031808-ea0e.log`.

Tests AST-extract and execute the actual `boundary_word`, `_turn_in_window`, and `handle_bot` functions to avoid application startup side effects. Mocked collaborators only: agent envelope/run, local desk/bot transports, control delivery gate, pulse, room storage, and logging. Cases include done/wait/blocked, repeated and >3 prefixes, ordinary internal prose, short internal text, missing/truncated tool traces, desktop birth, opt-in enabled, explicit envelope delivery, reply hand already delivered (no duplicate), and held voice. These are offline function/caller regressions, not Telegram/network/GUI end-to-end tests.

## Caller inspection / remaining issues

- Desktop `_turn_in_window` and Telegram `handle_bot` only consult this function if there is neither reply-hand speech nor explicit envelope text. Their WORD fallback branches are now unreachable; left intact to keep this patch bounded. Associated old comments describe the superseded recovery behavior.
- Explicit envelope text is still trusted as the tree's outward-facing envelope contract; verifying tree provenance is separate and no tree changes were authorized.
- Voice `why` remains visible only as the existing desktop system silence explanation, intentionally preserving requested voice semantics.
- Pre-existing Telegram no-text log says silence was the agent's decision even for BLANK/unspoken. No outbound note leak there, but this is an inaccurate diagnostic classification; reported rather than widening scope.
- Legacy BOUNDARY_DELIVERS_UNSPOKEN flag is still set by desktop caller and may influence the external tree's behavior; no claim of auditing/modifying that tree engine.
- Independent read-only review requested from scout agent-2d14bb3f; parent should record its review and rerun targeted tests after integrating other runner edits.

## Integration checklist

- [x] Exact original runner saved outside app and hashed before changes.
- [x] Actual-function red baseline and green patch, explicit reply and voice coverage.
- [x] Both boundary callers personally inspected for note fallback.
- [ ] Independent scout review result attached by parent.
- [ ] Parent combined-diff verification and broader suite gate.
- [ ] Live desktop/Telegram/voice scenarios: not exercised; require separate owner-authorized controlled validation. No restart performed.
