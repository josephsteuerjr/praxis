# Agent-owned updates and disposable desktop windows

## Ownership

`localharness.session_host` hosts the existing Python `Controller` and desktop
supervisor outside the UI process. It is not another scheduler or model loop.
The Controller's existing update watcher consumes the same durable
`desktop-update-request.json` used by the agent and desktop controls.

The native window uses `session_host.attach(root,event,origin,agent,browser_mode)`.
Its Controller-shaped client supports `start`, `status`, `current`, `channel`,
`readiness`, `refresh` and `invoke`. `close()` detaches an observer. `shutdown()`
requests explicit application Exit, durably cancels admitted jobs, and stops the
owner's Jobs. Detach never implies cancellation. Per-request agent selection is
context-local; two windows cannot switch each other's selected agent.

The descriptor is private data at
`DATA/memory/.control/desktop-session.json`: exact program/data identity, PID,
process birth, generation, local origin and a native-client token. Loopback
requests must match Host, Origin, token and generation. Mutating transport
failures are not replayed automatically. Reattach only reuses a measured process
birth; an unknown old owner refuses replacement. Each registered UI origin is
exact and revocable, so reopening a window does not restart its channel or agent.
There is no wildcard loopback CORS grant. The session token never enters page JS.

## Normal Update

The shell command `update_start({url,sha256,sourceMode})` first durably admits a
job, then returns `{state:'admitted',job,observer_may_close:true}`. The watcher,
not the window request, downloads, extracts, prepares and hands off the update.
`update_install` remains an admission entry for an already downloaded archive.
`sourceMode` is `preserve` or an explicitly selected `clean`; failed preservation
never silently becomes clean. The obsolete incompatible-extension override is
rejected rather than bypassing a failed compatibility check.

The existing configuration key `update.url` is the only feed setting. Both the
UI and agent-request consumer honor it. Its default remains the public stable
latest endpoint. An exact GitHub prerelease tag endpoint or a versioned JSON feed
can be selected independently without moving stable latest. JSON feeds provide
`version`, `url`, `sha256`, optional `notes`; GitHub feeds use the platform asset
and digest. Version ordering distinguishes `2.3.0-rc.1`, `rc.2` and final `2.3.0`.
Only the owner-selected feed changes; no second channel registry is created.

One job retains its stable ID, package digest, source policy, staging path,
execution owner, test/review references and mechanical/startup result. Work and
candidate snapshots live in persistent selected data, including
`DATA/memory/.source-updates`, not a master window's memory. Original cancellation
markers, failed attempts and useful results are retained. An interrupted effect
without a producer observation is attention/unknown, not permission to repeat it.
Named live coordinators are rejoined by PID+birth and their exact invocation.

There is no elapsed-age master timer. Network requests, child commands and
startup confirmation have physical operation bounds. Pause retains the job;
explicit cancellation is monotonic. Cancellation of an attention job releases
its admission slot only after its named producers are observed terminal. Unknown
custody remains pending. An interrupted file-switch journal must be recovered
before a fresh job is admitted.

## Conflict verification, independent review and exact apply

A preserve operation with actual authored source differences `L != A` is semantic
reconciliation, even if Git reports no textual conflicts. It uses the mandatory
source-update protocol. Pristine preserve and explicit clean do not acquire a
forced ordinary task-review gate.

The selected agent has these production `source_update` actions:

1. `status`, `read`, `diff`, `resolve`, `patch`: inspect and repair declared M.
2. `finish`: validate/freeze the current candidate, **not** approve or review it.
3. `verify(expected_composition_sha256)`: request the complete applicable
   version-bound battery on these exact bytes.
4. `review(expected_composition_sha256)`: request the existing independent panel
   only after the current battery's complete nonzero coverage validates.
5. Repair findings, finish and verify again before a new review. There is no
   old-full/new-smoke lane reuse after a composition change.
6. `apply(expected_composition_sha256)`: re-read canonical test and independent
   critic receipts, exact subject, all inputs and current owner controls before
   authorizing the short mechanical switch.

The host executes these requested operations; polling does not choose the
agent's semantic work. Ordinary typed plan/task-result review, an author
continue disposition, `complete:true`, a zero collection, unavailable tests,
stale hashes or a source-only clear cannot authorize conflict apply.

Composition identity binds core M, the complete final app/resources, actual
runtime members and original dependency/package manifests. Core snapshots and
Git revisions retain earlier candidates. `update_composition` preserves frozen B
app bytes and a separate editable `compound/app` M. Compound repairs require a
reason and current whole-composition identity. Changed app resources install
through the same complete preimage overlay, not by mutating the running verifier.
A core-unchanged app repair creates a new review operation identity.

`harness/` contains final M app/resources, including factory soul/skill resources
(not a live private persona); `distribution/` freezes both original source and
program manifests. Unchanged published image/font/icon binaries can be represented
as opaque identity evidence only with exact B digest, current composition and
resolvable frozen release proof. This is not decoded source reading or review
PASS. Modified/unknown binaries stay gaps and need their applicable rebuild and
verification. Critics retain the ability to find the evidence insufficient.

See [VERIFICATION-BUNDLE-230.md](VERIFICATION-BUNDLE-230.md) for bundle creation,
locked offline dependency provisioning and execution. When its platform's pinned
wheel closure is supplied, `verify` provisions a private job-scoped dependency
target, retains its receipt, and validates it before execution. It never installs
into the global interpreter; an incomplete prior acquisition remains unknown,
not a blind repeat. Canonical Python unittest
suites retain their real entry. Top-level pytest test functions use an AST-bound
pytest-file lane with actual fixtures, collected IDs, executed IDs and terminal
outcomes; they are not a zero-test unittest pass. The trusted executor binds
`HELENE_TEST_SOURCE_ROOT` to its isolated composed repository. Exact declared
platform skips are evidence, not broad missing-dependency exemptions.

## Short migration and legacy bridge

The manual migration window selects data/mode and calls `migration_job.admit`.
It leaves only after the persistent session owner has acknowledged the durable
job. Existing migration/copy/source preparation runs outside that window.

An incoming staged update from an older Python edition enters `legacy_update`
directly: normal Update does not open another long-lived master. After durable
admission the old UI/service data lease is explicitly released and recorded. The
same selected data and authored source are then owned by the existing supervisor.
Verified incoming B hosts the process owner and channel (`runtime_program`), while
the selected agent keeps its compatible original runner/interpreter until M passes
the gate. The temporary hand forwards commands to the verified incoming tool;
runner reloads use the owning Controller, never resurrect an old UI. The old
config is not rewritten. The hand must support verify/review/apply; finish-only is
not accepted as that capability. Transition-only environment is cleared before
the permanent successor starts.

For old 2.2.x callers, the historical `.python-migrator-PID.json` filename is a
compatibility admission carrier. Its body explicitly reports
`admission_kind: durable-agent-job`, `wizard_opened:false` and the durable owner.
New consumers must not infer a shown window or completed installation from its
presence. Old UI wording may remain old until its own files are updated.

The final updater/helper runs from retained incoming runtime/code outside the
files it replaces. It acknowledges target custody before the old host leaves,
waits for old readers, validates exact preimages, switches, proves startup and
retains rollback evidence. It has no model or review loop. Stop/Pause are checked
between mechanical steps; an interrupted file operation finishes with a known
commit/restoration result rather than a fabricated no-effect receipt.

## Portable program and data

The program stays in its selected existing folder. New data defaults to
`Path.home()/Helene` (`%USERPROFILE%/Helene` on Windows). Existing configured data
is attached at its actual old address and never relocated silently. Creating a
new agent refuses another populated profile; bootstrap control-only preseed bytes
are accepted only for that exact fresh migration identity.

`portable_profile.bind_program` records program/profile identity and converts
only recognized prior program-owned path fields. Program-relative code, app,
runner, runtime and recognized relay paths survive an actual folder move.
Absolute external overrides stay external. A missing pointer or changed/missing
data identity asks for explicit reconnection; it does not seed a new personality.
Two agents cannot accidentally select the same writable profile. Recovery uses
the same data identity and existing lease/custody mechanisms.

## Inspecting/stopping without a window

Use the current/incoming program's native Python, with explicit selected paths:

```text
runtime/python.exe -B -s app/localharness/session_host.py --root PROGRAM --control-tree DATA --control status
runtime/python.exe -B -s app/localharness/session_host.py --root PROGRAM --control-tree DATA --control stop
```

On macOS/Linux use `runtime/bin/python3`. `pause`, `resume` and `exit` are also
available. During cutover, when the old session is gone but its pinned helper
owns the transaction, the CLI uses the durable current-job controls. A stop
request is not proof of OS exit; returned producer observations and canonical
terminal receipts retain pending/unknown state. Panic/native emergency controls
remain separate and must not wait for model/review work.

## Verification boundaries

Focused source/transport fixtures, real process/HTTP admission and small temporary
Git/profile copies establish their stated seams only. They are not actual model
execution, a whole installed upgrade, native four-OS acceptance or a passing full
product battery. Successful authored preservation requires the real full bundle
and actual independent panel; no fixture-generated ready record substitutes for
that consumer journey.
