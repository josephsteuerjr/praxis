# Version-bound update verification (2.3)

This is the **source verification** part of modified-preserve apply. It is not
independent review, installed acceptance, a four-platform release verdict, or
permission to discard local edits. `complete` means the attempt ended. Only
`source_update_battery.validate(operation, directory)` can certify its exact full
coverage. A clear source panel still remains independently required by the update
coordinator.

## Producer / consumer seam

- A's `update_composition.prepare/describe/signature` owns the composition:
  immutable A/L/B, editable result core and `compound/app`, exact incoming runtime,
  actual dependency/package manifest identities. Frozen incoming source is never
  silently repaired.
- `source_update_battery.prepare(operation, directory, harness, stopped)` retains
  its old signature and returns `{'battery': {'root': ..., 'files': {...}}}` for
  the existing self-review source collector. The harness argument is retained for
  callers; authoritative inputs come from `update_composition.describe`.
- `prepare` also sets `operation['battery'] = {key, receipt_sha256, full_pass,
  gate_error}`. The normal updater owner must persist this before review/apply.
  Failed/unavailable lanes still produce readable evidence, not permission.
- `validate(operation, directory)` rereads the composition, bundle, executor,
  required release **and repair** lanes, raw observations/log hashes, frozen
  evidence hashes, full collected/executed/outcome coverage and skip policy. It
  raises `ValueError` for stale, partial, zero, unknown, red, cancelled or changed
  evidence. It does not trust a cached `full_pass` flag.
- Identical *whole* composition/bundle/executor inputs can reuse their complete
  attempt. Any core/app/resource/runtime/manifest/bundle change requires the full
  current recipe again. There is no independent-lane reuse policy.

An attempt interrupted without a complete receipt is retained and refused on
retry until its process custody/results are reconciled. An unresolved command
keeps its work directory. Do not delete that marker to manufacture absence.

## What “full” means here

The release bundle contains the full current source/test material, composed from
`praxis` and `helene/core`, plus desk source, fixtures and repository context. It
is not the edition overlay's smaller test list and has no guessed numeric count.
Runtime source is replaced by M in an isolated work tree; bundle test/support
sources omitted from ordinary distributions are supplied there. Deleted app
files are removed from that tree, not supplied from the old release by accident.
Direct edition/mirror imports point at the same M.

The recipe covers:

0. Before **any** Python/Node suite or build, offline `npm ci --ignore-scripts`
   in the existing locked `desk/app`, `desk/mobile`, `desk/miniapp`, and
   `desk/remote` projects. Both package.json and package-lock.json are bound
   source inputs. App imports `remote/src/settings-server`, whose qrcode resolves
   in remote's own node_modules; preparing only the three built frontends is
   insufficient. These are preparation command units, not invented test cases.
1. Actual full `praxis_test.py` bootstrap + unittest discovery on composed core.
2. Every canonical top-level `desk/tests/t_*.py` and `test*.py` module, with a fresh
   subprocess per module. Actual discovered case IDs, started IDs, terminal
   outcomes, class/module setup skips and import errors are recorded.
3. Every `desk/app/test/*.mjs` through the native Node test runner. TAP cases,
   counters, failures, skips and TODOs are retained; zero is not success. File
   concurrency is one to avoid turning a source update into a memory spike.
4. Explicit current-release native/build obligations, and native/generated-UI
   obligations introduced by the *actual repair*. Changed body/relay/hands Rust
   sources require their full Cargo lane. Changed static/mobile/miniapp outputs
   require their corresponding source build and matching unchanged verification
   inputs. No executor/toolchain/source means blocked, not platform skip.

The current Python app does **not** acquire an unconditional dependency on old
`desk/shell`, `svc` and `setup` Rust builds. Unchanged supplied native binaries
retain separate release evidence; rebuilt or changed consumers cannot inherit
that evidence. The optional native GUI/voice qualification commands are not
silently invoked by finding their filenames. Installed/native journeys remain
separately owed by the platform author.

Script-only fixtures need a publisher-declared supported entry, not an invented
unittest count. `--script-contracts` is JSON keyed by the exact suite source:

```json
{
  "desk/tests/t_cli_example.py": {
    "kind": "script", "argv": ["--input", "{work}/fixture.json"],
    "platform_skip": {"linux": "This declared Win32 consumer does not apply"}
  },
  "desk/tests/t_support_example.py": {
    "kind": "helper", "reason": "Fixture support imported by the full suite"
  }
}
```

These are examples, not existing required files. `script` records executed
command checks, **not** fabricated unittest cases. `helper` is reported
support-only and never increments passed tests. Unspecified modules use actual
unittest collection; zero cases remain a disclosed missing entry contract.
Contracts cannot replace source IDs or a required coverage list.

Individual test skips require exact `platform -> lane_id::case_id -> reason`
entries in `allowed_test_skips`; a missing dependency is not automatically a
platform skip. Whole-platform script applicability is also explicit. Failed,
expected-failure, TODO and unavailable results do not open apply.

## Build and package

Use the **target incoming runtime**, not the operator's unrelated global Python.
Capture on each provisioned native executor (the product already supplies its
Python runtime):

```sh
PYTHONPATH=desk TARGET_PYTHON -s -m localharness.source_verification_bundle \
  capture-executor --output executor-linux.json --packages verification-packages.json
```

`verification-packages.json` is a JSON list of the provisioned verification
package closure names. Their exact installed versions are pinned; unrelated
extra packages do not invalidate it. Without `--packages`, native site packages
are captured (not the user's site). The target interpreter, Node and any actually
needed Cargo/npm binaries are bound by SHA. Missing unused Cargo does not invent
a prerequisite. The actual verification Python must be inside, and hash-bound
to, the incoming runtime composition.

Combine captures into `{"win32": ..., "linux": ..., "darwin": ...}`; Debian and
Fedora may use different bundle artifacts/locks when their native executors
differ. Freeze source after other writers and frontend builds finish, then:

```sh
PYTHONPATH=desk python -s -m localharness.source_verification_bundle build \
  --source /path/to/frozen-repository --output /path/outside/repo/verification \
  --version 2.3.0 --locks executor-locks.json \
  --script-contracts script-contracts.json --allowed-skips platform-skips.json
```

Optional `--native-lanes` / `--build-lanes` JSON arrays name actual current release
consumer roots; they do not restore legacy crates by default. Dynamic repair
obligations are added independently. The builder returns the raw `manifest.json`
SHA. Ship the directory as `program/verification` and include **all** its files
in the program inventory. Set `python-app.json.verification_bundle_sha256` and
propagate that value to the incoming coordinator. Alternatively an explicitly
bound operation can supply `verification_bundle = {root, sha256}`.

The portable repacker independently checks bundle/source-origin closure,
including test sources, rather than accepting a stale bundle with the same
version string. `load(root, sha, version)` checks every source, runner and
optional dependency wheel, and re-derives the full recipe. Source material has a
256 MiB default byte cap, including composed copies; no models, installed
profiles, owner Git history or multi-GB runtime copies enter this bundle.

### Optional offline dependency acquisition

An executor may be pre-provisioned. Alternatively `--wheelhouses` accepts a JSON
`platform -> wheelhouse_path` map. Each wheelhouse has `wheels.json`:

```json
{"packages":{"distribution-name":{
  "version":"1.0", "filename":"distribution_name-1.0-py3-none-any.whl",
  "sha256":"<exact wheel SHA256>"
}}}
```

Publish the actual dependency closure, including pip if the embedded runtime
lacks it; the versions must match its executor lock. The builder includes those
exact wheels and a hash-required requirements file. Explicit acquisition is:

```sh
PYTHONPATH="$PROGRAM/app:$PROGRAM/tree" "$PROGRAM/runtime/bin/python3" -s \
  -m localharness.source_verification_bundle provision \
  --bundle "$PROGRAM/verification" --sha256 "$MANIFEST_SHA" --version 2.3.0 \
  --python "$PROGRAM/runtime/bin/python3" --output "$PRIVATE_CACHE/new-test-site"
```

It uses offline, no-deps, hash-required pip into a **new private target**, never
system pip, network downloads or a runtime clone. A failed target/log remains
retained. The returned `python_dependencies`, `dependency_receipt` and
`dependency_receipt_sha256` fields go into `operation.verification_executor`.
Their exact files are rechecked before use and at apply. Declared verification
packages are checked in the real subprocess; adding this directory cannot turn
an unavailable dependency into a claimed pass without executing the tests.

Native Node/Cargo/npm can be supplied by the selected explicit executor. Compiler
caches are only used when `CARGO_HOME` or `RUSTUP_HOME` paths are explicitly
supplied in `verification_executor`; otherwise isolated empty homes are used.
Npm requires an explicitly selected absolute existing `npm_config_cache`
directory in that same executor. No cache means unavailable; missing content
means retained offline ENOTCACHED command failure, not network acquisition or
a platform skip. An ordinary machine never silently borrows Yegor's SSH server,
credentials or a global model installation.

Npm preparation uses the selected hash-bound Node/npm tools and source-lock
integrities, with isolated user/global npm config paths and explicit offline,
cache, no-audit/no-fund flags. Each npm command records exact argv, cwd,
package/lock SHA256, selected tool paths/hashes and cache path; input hashes are
rechecked before and after execution. The cache path enters the whole attempt's
executor identity, but mutable cache contents are checked by npm's locked
integrities, not mistaken for immutable source. Build obligations remain the
same actual three frontends and run `npm run build` after preparation, without
another ci. Full suite collection, skip policy and no-per-lane-reuse are unchanged.
`t_verification_node_inputs_230.py` exercises the actual offline dependency and
cross-project import ordering with a tiny synthetic tarball; it is not proof of
the product's three builds or four-platform acceptance.

### Explicit owned native test inputs

`verification_executor.native_env` accepts **only** `HELENE_BODY_DIR`,
`HELENE_BODY_BIN`, `DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, and `XDG_RUNTIME_DIR`.
These are selected local test capabilities, never inherited desktop/session
values. All are omitted unless explicitly supplied. Empty, non-string or unknown
keys refuse the declaration rather than silently changing the test contract.
Body/runtime directory and binary paths must be absolute and existing. Example
for an already provisioned **owned** Linux stand (replace every example value):

```json
{
  "native_env": {
    "HELENE_BODY_DIR": "/owned/program",
    "HELENE_BODY_BIN": "/owned/program/helene-body",
    "DISPLAY": ":97",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/owned/session/bus",
    "XDG_RUNTIME_DIR": "/owned/session/runtime"
  },
  "native_files": {
    "/owned/program/helene-body": "<exact lowercase SHA256>",
    "/owned/program/helene-bridge": "<exact lowercase SHA256>"
  }
}
```

The surrounding object is merged into the existing selected executor alongside
its Python/tools/dependency fields, not a general environment/settings map.
Windows/macOS use `HELENE_BODY_DIR` and their actual body/bridge pair; Windows
names have `.exe`. The existing consumers also recognize `praxis-body` and
`praxis-bridge`. Every recognized file present in the selected directory must
appear in `native_files` as absolute path -> actual SHA256, with at least one
complete pair. Missing, symlinked, mismatched or unrelated file declarations
refuse before execution. If supplied, `HELENE_BODY_BIN` must name a body in that
same pair directory. There is no fallback to an ambient production binary.

Normalized environment values and exact native paths/hashes enter the executor
identity and therefore the whole attempt key. `prepare` and `validate` re-read
them; each physical command also rechecks the pinned files before launching.
Changing a display/session value cannot reuse the old full receipt. No
per-lane reuse, cache, Python dependency, owner-control or process-custody rule
changes. No binary is executed merely to validate this declaration.

Actual consumers are `t_body.py` / `layout.body_target` / `body._exe_pair`,
`t_body_linux.py`, and `t_body_macos.py`. The Linux executor owns a non-root
Xvfb 1280x800 desktop, EWMH manager, session bus and enabled AT-SPI, following
`installer/linux/desk-stand.sh`; this API neither creates nor certifies that
stand. Windows/macOS similarly require their own native graphical session.
No `PRAXIS_HANDS`, host executable override, legacy payload opt-in, credential or
GUI-disable flag is silently manufactured. An applicable native test lacking
material remains unqualified (and its undeclared skip blocks full validation),
not an automatic platform skip or PASS.

Native-input SHA binding proves selected bytes, **not** that the body was built
from changed M. Changed Rust still requires the applicable Cargo obligation and
separate native build/source provenance. Full manifest execution and installed
journeys remain separate evidence. Focused producer regressions are
`python -B desk/tests/t_verification_native_inputs_230.py`; they launch only a
Python environment probe and existing protocol fixtures, never a real body.

## Native execution / Linux handoff

The normal route is the agent's `source_update(verify)` action under the durable
host's existing job lease. For the parent's isolated native stand only, a CLI
adapter is also available:

```sh
PYTHONPATH="$PROGRAM/app:$PROGRAM/tree" "$PROGRAM/runtime/bin/python3" -B -s \
  -m localharness.source_update_battery --directory "$PLAN_DIR" \
  --output "$EVIDENCE_DIR/verification-execution.json"
```

`PLAN_DIR/pipeline.json` must already name that native incoming composition,
version, bundle and selected executor. This CLI performs no model/review call,
never changes the canonical pipeline, and writes `sources`, `battery`, validation
or an exact blocking reason. The sole job owner can persist the returned battery
reference under its lease; do not race the normal running host or silently
rewrite Windows paths into Linux identities.

HOME/data/temp are isolated and credentials/proxies/model settings are stripped.
The Python adapter also denies accidental Python writes outside work, including
native Known Folder paths. This is **not** an OS security sandbox: native tests
and subprocess consumers require the chosen isolated native executor/profile.
Command groups/Windows Jobs use existing `command_process` custody and Stop.
Full raw logs/observations remain hashed; reviewers receive full sanitized logs
and the exact full-case report, not just a selected passing tail.

Focused Windows verification for this implementation:
`python -B desk/tests/t_source_update_battery_2810.py`. Its native green fixtures
are explicitly stubbed protocol tests; Python/Node fixture execution and the
synthetic offline wheel consumer are real. They are not a product full battery,
real voice, taskbar-click or four-platform qualification.
