# 1009 descriptor test repair

Scope: follow-up to reviewer agent-fe7e16f2 in isolated proposal 3013f577.
This worker changed only the descriptor test in `body/crates/praxis-body/src/uia.rs`
and this note. Existing proposal changes were preserved. No production edits,
KEAT, deployment, live desktop actions, merge, or dependency installation.

## Repair and review

Read actual `uia::descriptors`, `adapter_descriptor`, `handles`, and the constants
in uia.rs and element.rs. Replaced the stale one-verb expectation with exact
ordered tuples `(name, version, mutating, durable)`:

- `desktop.window.read`, 1, false, false
- `desktop.element.act`, 1, true, false

The test asserts both handlers and exact adapter capability membership, retains
unknown-handler rejection and platform availability assertion. Literal names
intentionally guard the public contract rather than merely repeating constants.
Reviewed related runtime manifest-routing tests (which iterate all descriptors),
element/client tests and searched body sources for one-verb/count-one and singleton
adapter assumptions; no other stale UIA singleton expectation found. Other
modules' descriptor assertions do not describe this UIA adapter.

## Evidence consulted before validation

Read `1009-uia-repair.md`, `1009-client-bridge-repair.md`, and actual task run logs
`20260910-225334-dc57.log` and `20260910-225352-d5ea.log`. Those attempts failed
before project compilation: host linker `cc` missing for dependency build scripts;
clippy component not installed. Current `command -v` probes for cc, gcc, clang,
and cargo-clippy returned no executable; the task-local toolchain bin directory
also lacks clippy. Therefore did not repeat the already-blocked cargo builds.
Inspection receipts: `20260910-230233-6e34.log`, `20260910-230246-2650.log` under
`/app/memory/.forge/tasks/code-63bc65ae/runs/`.

## Commands and results on the repaired tree

- `python praxis_test.py test_element_act test_body_client test_window_loop test_computer_access_agent test_pass24_windows_body test_computer_access test_computer_file_journal -q`
  — PASS, 136 tests, zero outbound connections. Receipt
  `20260910-230302-1c01.log`. These are Python tests, not execution of the Rust test.
- `git diff --check` — PASS.
- `workspace/.rust-validation/rustup/toolchains/1.94.0-x86_64-unknown-linux-gnu/bin/rustfmt --edition 2024 --emit stdout body/crates/praxis-body/src/uia.rs > /tmp/1009-descriptor-formatted.rs`
  — PASS parsing only; source not reformatted. Separate confirmed-success receipt
  for this and diff check: `20260910-230318-cfff.log`.
- `grep -R -n -E 'exactly_one_verb|descriptors.len\(\), 1|vec!\[CAPABILITY.to_string\(\)\]' body/crates/praxis-body/src`
  — no matches after repair (grep exit 1, expected for no matches).

## Gate status

**Windows compile/test gate remains NOT VALIDATED.** The repaired Rust test was
not executed. No Linux stub or source parsing result is Windows compilation.
Prior `cargo check --locked --manifest-path body/Cargo.toml -p praxis-body --target
x86_64-pc-windows-gnu` and `cargo test --locked --manifest-path body/Cargo.toml -p
praxis-body --no-run` remain blocked by missing host linker. Prior `cargo clippy
--locked --manifest-path body/Cargo.toml -p praxis-body -- -D warnings` remains
blocked by missing component. No full Python gate attempted.

Next: review this small test-only repair in the existing port, then use an equipped
isolated Windows runner to compile and execute Rust unit tests (including
`uia::tests::the_module_declares_read_window_and_element_act`) and clippy. The
provider/COM fault-injection evidence gaps in `1009-uia-repair.md` remain open;
this repair does not demonstrate any live UIA behavior or close those gates.
