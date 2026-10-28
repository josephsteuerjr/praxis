# 1009 client/bridge repair after agent-5babd7a7

Isolated proposal 3013f577 only. No deployment, merge, restart, desktop action or KEAT edit.

## Fixes

- Selector serialization preserves every nonempty string, including whitespace-only exact
  automation_id/name. Empty strings still mean omitted (wrapper defaults). Corrected the
  test that previously endorsed dropping a whitespace-only identity; added both-field coverage.
- Bridge spool now atomically binds device/request/operation identity to a SHA256 digest of
  execution, capability and args when storing an outbound Invoke. Deadline is intentionally
  excluded so an identical manual retry can renew its transport budget. Bindings survive
  acknowledgement, response expiry and restart and apply to HTTP and websocket producers.
- A different intent or mismatched request/operation pair fails before outbound enqueue.
  HTTP admission returns 409, ok:false, code:id_conflict. Existing Python call() already
  returns failed admission immediately and never polls the old terminal cache in that case.
  Terminal receipt priority/preservation is unchanged; no cache clearing or action replay
  policy relaxation. Identical retries still reach the body's existing journal.
- Legacy cached/pending IDs without a binding fail closed: intent cannot be proven, so
  returning their cached success for a new admission would be unsafe. This deliberately
  prevents even identical legacy retries through HTTP; callers should inspect existing
  receipts, not blindly mint new action keys. No old cache is overwritten.

## Verification

136 tests PASS:
`python praxis_test.py test_element_act test_body_client test_window_loop test_computer_access_agent test_pass24_windows_body test_computer_access test_computer_file_journal -q`

`python -m py_compile body_client.py test_element_act.py` and `git diff --check` PASS.
Log: `20260910-224056-ccb3.log` in task run evidence. Zero outbound test connections.
Python regression models HTTP admission plus immutable response cache: A succeeds, identical
A returns its result, changed B returns id_conflict without a third response poll.

Added real Rust HTTP/spool regression for the same sequence, no B enqueue, unchanged A
cache, and binding persistence across acknowledgement/reopen; added spool tests for both ID
aliases, renewed deadline and legacy cache rejection. These Rust tests are NOT executed:
`cargo test --locked --manifest-path body/Cargo.toml -p praxis-bridge` exits 101 at dependency
build scripts because native linker `cc` is absent (log `20260910-224111-89c9.log`). Task-local
Rust 1.94 rustfmt installed and run on both bridge files; formatting parses but does not prove
compilation. No Rust/Windows compile claim. No full Python gate.

## Limitations / next review

Bindings intentionally have no expiry: forgetting them would permit same-key intent rebinding.
Storage growth/retention needs a separate explicit lifecycle policy. Legacy acknowledged work
with neither response nor pending frame cannot be reconstructed; body journal remains final
side-effect deduplication authority. HTTP callers now receive admission conflicts directly;
websocket conflicting submissions fail store and are logged/dropped (not written over a
terminal result), matching existing websocket spool-error handling. A separate correlated WS
error protocol is outside this HTTP client repair. Rust build/test/clippy in an equipped
isolated checkout remains mandatory. UIA property completeness is owned by the Rust reviewer.
