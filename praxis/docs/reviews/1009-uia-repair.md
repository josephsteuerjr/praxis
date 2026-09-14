# 1009 UIA false-uniqueness repair

Scope: repair reviewer `agent-887c5d25`'s two blockers in the existing isolated
3013f577 port. Only `body/crates/praxis-body/src/uia.rs` and this note were edited
by this worker. No KEAT, 1109, bridge/client edits, installs, desktop actions,
merge, deployment or restart. Read the reviewer's actual result and shared mailbox.

## Changes

- Replaced `act_walk_result(Result<Element>)`, which treated all `E_POINTER` as
  absence, with raw TreeWalker calls preserving HRESULT separately from the nullable
  interface. Successful null is absence; **any failed HRESULT aborts** before
  choosing/mutating, even when the output pointer is null. Returned interfaces are
  owned and dropped on the failure path as well.
- Cached role, name (exact or substring), and automation ID reads now propagate
  failure when required by the selector. A failure on a competing element cannot
  quietly remove it from the match set. Optional display-only fields may still be
  omitted. Required value reads preserve raw pattern-query status versus successful
  null, then require successful interface cast and value read. This is deliberately
  conservative: provider errors (including unsupported-pattern errors rather than
  successful null) fail the action, not classify the node as a non-match.
- Live selector revalidation uses the same strict name/ID/value behavior; values
  preserve whitespace. Existing complete-traversal-before-choice, nth handling,
  deadlines, PID/HWND and implicit foreground guards remain intact.
- Adapter metadata now includes `desktop.element.act`, retaining its existing name
  for compatibility. Display snapshot helpers remain best effort and are not used
  as matching evidence.

## ABI evidence (actual dependency, not guessed native signatures)

Inspected existing task-local registry source `windows-0.62.2`,
`src/Windows/Win32/UI/Accessibility/mod.rs` (workspace lock dependency).

- Lines 18327 onward: generated `GetFirstChildElementBuildCache` uses vtable call
  followed by `Type::from_abi`, which loses successful-null vs provider-E_POINTER.
- Vtable lines 18395/18397: first-child and next-sibling BuildCache each take
  `(this: *mut c_void, element: *mut c_void, cache: *mut c_void,
  output: *mut *mut c_void) -> HRESULT`. New code uses exactly this ABI and
  `Interface::{vtable, as_raw, from_raw}` ownership.
- Lines 10147/10148: GetCurrentPattern/GetCachedPattern return HRESULT separately
  from an IUnknown output, with `UIA_PATTERN_ID` argument. New strict value reader
  uses these signatures, then `IUnknown::cast`.
- **SetValue concern rejected:** `IUIAutomationValuePattern::SetValue` at line
  18600 accepts `&windows_core::BSTR` in these bindings. The existing call is correct
  and unchanged. `IValueProvider::SetValue` at line 19068 accepts PCWSTR; that is a
  different interface.

## Verification on this repair

- `git diff --check`: PASS; inspected scoped diff and final call sites.
- Task-local Rust 1.94 `rustfmt --edition 2024 --emit stdout
  body/crates/praxis-body/src/uia.rs`: PASS parsing (output only in /tmp; no
  unrelated reformat).
- Added three Rust regressions in `uia.rs`: successful null vs provider E_POINTER
  (including non-null failure output); unreadable duplicate name aborting before
  exact/nth selection (None, 0 and 1); optional display vs required field errors.
- Extracted **actual** pure helpers, actual `element::Choice/choose`, and these
  three tests into `/tmp/1009-uia-pure.rs`; `rustc --edition 2024 --crate-name
  uia_pure --test --emit metadata ...`: PASS typechecking (only unused Choice
  fields warnings in extracted fixture). **Tests were not executed**: metadata
  checking is not test execution or Windows compilation.
- `cargo check --locked --manifest-path body/Cargo.toml -p praxis-body --target
  x86_64-pc-windows-gnu`: BLOCKED before project compilation, linker `cc` missing
  for quote/proc-macro2/libc build scripts.
- `cargo test --locked --manifest-path body/Cargo.toml -p praxis-body --no-run`:
  same BLOCKED host linker error.
- `cargo clippy --locked --manifest-path body/Cargo.toml -p praxis-body -- -D
  warnings`: BLOCKED because cargo-clippy is not installed. No install attempted.
  These use the already-installed task-local toolchain, not a global toolchain.

Evidence logs under task `code-63bc65ae/runs`: `20260910-225334-dc57.log`
(Windows check), `20260910-225352-d5ea.log` (test/clippy),
`20260910-225408-1fbf.log` (parse/diff inspection),
`20260910-225426-439d.log` (pure regression metadata typecheck).

## Remaining gates / limits

Not a successful Rust build or provider test receipt. Requires Windows-target
compilation and actual execution of Rust tests on an equipped runner. Requires
synthetic COM/provider fault injection to verify real ABI calls, successful null,
failed HRESULT, failed required cached property, and no mutation on all these
paths. Pure helper regressions do not prove provider behavior or COM lifetime.
UIA trees remain non-atomic: a provider can change another element after traversal;
selected-element live revalidation does not create a transactional tree snapshot.
Synchronous mutation already in flight can outlast caller timeout and must remain
reported as potentially having acted. No runtime claims are made here.
