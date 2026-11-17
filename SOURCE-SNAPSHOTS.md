# Source and package bindings

## Hélène 2.8.7 source base

The immutable `v2.8.7` tag at
`517669de20d73c7a2e3efb2e2c55ff339dc740ab` identifies the latest released
Hélène source: the desktop host, interfaces and explicit `helene/core/` overlay
on its shared `praxis/` base. Shipped per-platform inventories bind the exact
qualified composition. The 17 November main sync includes that public history
and then adds the current cloud snapshot below; main is development source,
so it does not redefine the released 2.8.7 composition.

## Historical desktop 2.5.0 composition

The 2.5.0 build selects current `praxis/` followed by the explicit
`helene/core/` overlay, together with `desk/` and its built shared interfaces.
Each archive's `source-composition.json`, `source-inputs.json` and verification
manifest bind the selected source tree, file bytes and executable modes.
For published 2.5.0 builds, use the immutable `v2.5.0` tag and that archive's
inventories to identify the exact qualified composition.

## Historical desktop 2.4.0 composition

Use the release's immutable `v2.4.0` tag and the archive's
`source-composition.json`, `source-inputs.json` and verification manifest to
identify its exact code. The selection rule is current `praxis/` followed by
the explicit `helene/core/` overlay, together with `desk/` and its built shared
interfaces. An overlay directory alone is not the complete runtime. The release
source manifest binds the selected Git tree and each file's bytes and mode.
The cloud mirror below is synchronized source; this desktop release does not
deploy it to production.

## Stable desktop 2.3.0

The immutable `v2.3.0` tag at
`3194d41ece4de021f913c31f240d17b2faf91786` binds the released desktop source,
all four platform archives and their verification inventories. Build or inspect
that tag to reproduce the released composition. Development on main can advance
beyond that frozen desktop code.

## Current cloud Praxis mirror

`praxis/` mirrors the selected public source of running cloud Praxis on
17 November 2026: HEAD `67111dfb00d55e1e43bf73d1c6c02d979929d220`, with no
selected deployed-uncommitted files. This updates the previous `fe7d2036`
resident-skills snapshot with the subsequent epoch-tape and context fixes:
superseded revisions and duplicate deletion records retire once represented;
a pending life write holds its archive occurrence until it can be joined;
her sends from other topics remain marked evidence rather than her current
reply; follow-up and turn-trigger labels name their direction. A dismissed
stale epoch-repair blocker retains its evidence without remaining active.

`CORE-SOURCE.json` records the 796 selected live source files, their modes,
source and public digests, exclusions and historical qualification. The last
live change passed two identical full gates, each with 7,338 tests, 16 skips
and no errors, plus its 506-test focused set. Those are the original deployment
results, not fresh tests performed by this mirror sync. This sync checks source
identity, executable modes, Python syntax and publication privacy.

Two live files remain excluded: a stray runner copy and a one-off script that
reads a third party's private inbox. Eight files originally introduced into
`praxis/` by Hélène releases remain preserved. The existing two documentation
sanitizations remove third-party identifiers from comments and docstrings while
preserving the executable AST. Private memory, authored soul, workspace,
credentials and private Git history are excluded.

Praxis's server application is in
[`remote/praxis-desk/`](remote/praxis-desk/), separate from Hélène's `desk/`.
Its source remains `e52ce540fcc1a49b7d47c22bed2dc5c845572508`: all 19 installed
backend files match after line-ending normalization, and the deployed MiniApp
and PWA files still match the original build manifest. The relay source and
running image are unchanged from `RELAY-SOURCE.json`; that record's disabled
limits describe the historical 14 November capture. The three limits were
subsequently enabled as runtime configuration, without a relay code change.
No runtime is deployed or restarted by this source publication.

The source keeps the existing Ouroboros attribution in the README and original
commits. New cloud tests can expose gaps where Hélène overrides a shared module;
porting decisions and full desktop qualification belong to the next release.
Use immutable desktop tags and shipped inventories for released code.

The older source bindings below are historical provenance. `HELENE-SOURCE.json`
and `helene/PLATFORM-SOURCE.json` also retain historical inputs; each new archive
has its own generated composition inventories.

## Historical 2.2.9 bindings

### Hélène 2.2.9 source and package bindings

Implementation `f043cb73cebaea40778d8f445762247d00fd8f27`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.9-windows`, commit `119ceec841d3f6afd0d413ca66852f21babb5561`, archive `Helene-2.2.9.zip`.
- **macos**: tag `source/helene-2.2.9-macos`, commit `68e16a8d023123fb0c0409a9d131e3da11600337`, archive `Helene-2.2.9-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.9-debian`, commit `e6a6b11ecff7a9d7fac440aea28d0177f352fdf5`, archive `Helene-2.2.9-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.9-fedora`, commit `1f614f3cf406b377c06f78b8d7cf54821629bf00`, archive `Helene-2.2.9-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.9B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before the selected preserve or pristine source policy. Preserve still requires independent review after agent resolution. See desk/docs/SOURCE-ADMISSION.md.

The updater offers both policies without a separate preparation command. Legacy pending agent work safely reopens the previous app; the next invocation carries only a matching resolved candidate into independent review. Native runtimes/platform adaptations retain qualified2.2.4 inputs; all five interfaces were rebuilt for the source-mode controls. Voice references remain pinned2.1.1. Rejected2.2.5 was removed;2.2.6 was withdrawn after the normal-startup-cache failure;2.2.7 was withdrawn after the owner-owned relay lifetime failure. 2.2.8 is withdrawn after its restricted merger ended without resolving conflicts. The Praxis public source mirror is unchanged.
