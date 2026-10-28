# Verified portable source refresh

`portable_refresh.py` builds **a new ZIP**, not an installed directory. It replaces
the former directory-copy refresh entry (no current callers in this tree). It
never invokes a native compiler, migrates a profile, publishes, or qualifies GUI,
model, voice, signed wrapper, startup or update behavior.

## Inputs and ownership

- A distribution ZIP with an independently pinned SHA-256. Never pass a live
  installation, profile, or an archive made from one. Profile-bearing roots are
  refused. Only regular `Helene/` members are accepted; native attributes and
  unchanged compressed local records survive exactly.
- An explicitly selected **current repository**. The stock tree is current
  `praxis/` followed by current `helene/core/`, excluding private/cache material
  and mirror relay infrastructure. No CRLF normalization, stale
  `HELENE-SOURCE.json` pin reinterpretation, or implicit merge with an installed
  agent. `source-composition.json` names every selected source and the old
  manifest's digest as historical evidence, not new authority.
- All three frontends (app, mobile, miniapp), built from these exact inputs.
  The build receipt binds shared ui-kit/source/lockfiles and every output file.
  A changed input or output refuses a plan. Build dependencies must already be
  provisioned; the helper does not install them.
- A version-bound verification directory built by
  `localharness.source_verification_bundle`. It is copied to `verification/`;
  its manifest SHA is in the full program inventory and `python-app.json`.
  Bundle lane completeness belongs to that module's loader; this packer verifies
  the exact published files/runner identities, not a fictitious full test PASS.

## Commands

Use the source's supported Python (3.10+). No platform inference from the host:
choose `windows`, `macos`, `debian`, or `fedora` explicitly.

```text
python desk/installer/portable_refresh.py build-frontends --source SOURCE --receipt NEW-frontends.json

python desk/installer/portable_refresh.py refresh --source SOURCE --donor PINNED17.zip --expected-sha256 DONOR_SHA --platform windows --version 2.3.0-rc.1 --frontend-receipt NEW-frontends.json --verification-bundle BUNDLE --verification-sha256 MANIFEST_SHA --report NEW-plan.json
```

Planning does not extract a runtime or write a package. It streams donor bytes to
regenerate inventories; budget I/O accordingly. Read the plan, then run the same
arguments **with a different report path**, plus:

```text
--output NEW-Helene-2.3.0-rc.1.zip --expect-plan PLAN_SHA --report NEW-build.json
```

`PLAN_SHA` is the top-level `sha256` in `NEW-plan.json`. Any source/build/bundle
change makes a different plan. The output and its `.sha256` / `.artifact.json`
are exclusive; prior candidates are never overwritten. Construction needs free
space for one compressed candidate plus a small source/metadata margin. The
packer preserves previous results and removes only its own incomplete temporary
output if verification fails.

## Legacy admission is separate from pristine B

Use `--legacy-admission` **only** for the old installed updater's compatibility
handoff. The donor must already carry a declared `source-admission.json` custody
view whose exact tree/modes match. That view remains in `tree/`; **new full
source B** is sealed in `code-upstream.zip` and protected
`source-admission-upstream.zip`. The new declaration binds target version and
both immutable inputs. The existing `source_admission.activate()` verifies and
materializes B before using the incoming runtime. It is not a merge approval.
Without this flag the actual stock tree is delivered directly and stale admission
markers are removed. No baseline version is hardcoded into the packer.

## Dependencies and native identities

The full inventory is exact, including stock, runtime, verification materials and
wrappers. Target-specific optional component URL/name/hash/size references remain
the donor's immutable objects; only `app_version` changes. Current channel/core
requirement declarations are refreshed from `deskpkg` and the composed core.
This does **not** install new dependencies into the retained runtime.

For an explicitly prepared runtime dependency (e.g. psutil), supply
`--runtime-overlay DIR --runtime-overlay-sha256 MANIFEST_SHA`. DIR contains only
`manifest.json` plus declared `runtime/...` files. The manifest format is
`helene-portable-runtime-overlay.v1`, with exact `platform`, nonempty `inputs`
(pinned wheel/build provenance), and `files` mapping each path to
`{sha256,size,mode}`. The publisher verifies/provisions that dependency; the
packer verifies all exact output bytes and rejects undeclared files or writes
outside runtime. The report explicitly says `runtime_changed=true`; it never
calls this an unchanged-runtime or native rebuild qualification.

When replacing an existing dependency, optional `remove` maps each obsolete
`runtime/...` member to its exact SHA-256 in the pinned donor. The packer checks
those bytes before deletion; no globs or application/profile deletion is allowed.
Only old files absent from the replacement wheel belong in this map. This avoids
leaving a previous version's dist-info, modules or bytecode beside its replacement.

Unchanged `.app` wrappers/signatures, executable launchers and runtime records
retain their original bytes and modes. Consequently a signed wrapper's historical
Info.plist version can differ from authoritative `python-app.json`. This is
disclosed reuse, not a fake re-sign/build. A changed wrapper implementation or
native dependency still needs an actual source-bound build and native acceptance;
this source-only refresh cannot supply that evidence.

## Consumer checks

```text
python -m unittest discover -s desk/tests -p t_portable_refresh_230.py -v
```

Small real ZIPs cover four explicit platform labels, byte-identical compressed
runtime/wrapper records, source additions/deletions, all frontends, stock template
preservation, exact inventories, a genuine admission activation, pinned runtime
overlay, stale inputs, unsafe archives and failure/no-overwrite. They do not
execute Linux/macOS or the four real release donors. The parent platform executor
owns those checks and the installed2.1.1 → RC journey.
