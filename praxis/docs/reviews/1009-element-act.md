# 1009 element-act — isolated implementation review

Proposal 3013f577, base ef8dc8f4. Not merged, deployed, restarted, or exercised on a desktop.

## Provenance and prerequisite

Read AGENTS.md, the original 1009 patch and accompanying 10.09 action note, and run
`run-20260910T211936510549Z-dfcde92c/RECAP.md`. The recap explicitly says proposal
05930f6c had no integration. Verified HEAD lacked agent `read_window` scope, schema,
signature and dispatch, while body/client reading already existed. Added only that
missing agent wiring and read-only classification. No 1109 element-find, KEAT,
mini-app registry, or unrelated prerequisite work.

Patch SHA256: `59bc178c2f0bea65b81b74a040237bfcdb7bfe5fd99c010be4b751637a8c0a09`.
The two sequential revisions of element.rs/uia.rs were inspected and ported in order;
this is an intentional port, not a claim that an apply pipeline succeeded.

## Implementation / deviations from inbox

- Adds named-element UIA action route, pure selector/receipt module, client wrapper,
  computer action/schema and tests. Eight patterns; no coordinate fallback.
- Retains computer.apps grant checks and interactive-only guard. Existing pixel input
  focus protections unchanged. New Rust guard checks target HWND/PID and implicit
  foreground immediately before pattern invocation; live selector rechecked.
- Original patch could act on the only match in an incomplete traversal. Port refuses
  any limited traversal, bounds sibling scanning/queue growth, detects depth omission,
  and propagates traversal provider failures. Deadlines cover worker startup and are
  checked again after pattern acquisition.
- Unknown selector/request fields rejected instead of silently broadening targeting.
  Nonblank selector strings preserve whitespace rather than silently trimming identity.
- New agent action requires explicit stable `idempotency_key`. Principal-scoped digest
  derives both request and operation IDs independently of payload; body journal provides
  same-key/changed-payload conflict. Manual retry must reuse key. Generic automatic
  recovery remains conservative: this action is NOT newly classified replay-safe.
  Low-level wrapper accepts request/operation IDs; direct callers must supply stable IDs.
- Formatter preserves partial-search reason/coverage and does not substitute pre-action
  state as post-action state. Receipts mean pattern invoked, not goal achieved.

## Verification evidence

134 targeted Python tests passed:
`python praxis_test.py test_element_act test_body_client test_window_loop test_computer_access_agent test_pass24_windows_body test_computer_access test_computer_file_journal -q`

Log: `/app/memory/.forge/tasks/code-63bc65ae/runs/20260910-223208-5a7e.log`.
No outbound network attempts. Earlier persisted matrix `verify-05fc0aff` passed
133 tests, py_compile and diff-check; the later test adds principal scoping and
conservative recovery assertions. `python -m py_compile agent.py body_client.py
test_element_act.py` and `git diff --check` pass.

Rust test/Clippy attempts are BLOCKED, not passed: `cargo` and `rustc` are absent.
Exact final attempt log: `.../runs/20260910-223341-7fc6.log` (exit 127).
Added pure Rust regression assertions but could not execute them. Full Python gate
was not run. No Windows-target compilation or live UIA validation performed.

## Review blockers / remaining risks

Do not merge on the Python result alone. Compile/test/clippy in an equipped Rust
checkout, including Windows-target compilation. Specifically review TreeWalker
null-interface/E_POINTER handling against windows-rs/provider semantics. Cached
property reads still use optional fallbacks in parts of the traversal: a provider
property failure can hide a competing match, so uniqueness under provider failures
needs further fail-closed hardening and regression coverage before acceptance.

UIA traversal/selection/mutation cannot be atomic against external UI changes;
revalidating a selector cannot guarantee global uniqueness after the scan. PID/HWND
checks are not process-generation identity. An already-entered COM pattern cannot
be cancelled: timeout is explicitly uncertain, and retrying under a new key may
repeat the effect. Post-action provider reads are best-effort and need Windows review.

Next: independent review of exact diff, resolve provider-failure uniqueness, Rust and
Windows compilation, then required pre-merge gates in an isolated environment. Live
UI actions remain outside this task's authorization.
