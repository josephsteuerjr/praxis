# Source and package bindings

## Desktop 2.5.0 composition

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

`praxis/` mirrors the selected public source of the running cloud Praxis on
2026-11-14: docs HEAD `a3f18a7e439fb22ebc012b272e44ab30970b8100`, runtime
`771695e8d184416e3ef98815d5780ca4f9f7f22a`, and final test-isolated source
`7a4dfea8f1f3b83dfaa2a87479cf1b3e36b30b0e`, with no deployed-uncommitted
product files. Since the previous mirror (`24d09a6`), this adds titles bound to
the actual triggering messages, separate operator-probe history, numeric model
generation progress, and the `code_query` hand shared with Forge. Semantic search
runs through owned child processes; its Python AST evidence, hash-bound pages
and structural cache make incomplete or uncertain results explicit. External
roots use a process-memory cache; remote task roots are explicitly unsupported.

The relay reports numeric progress without exposing argument text. Its three
generation limits remain zero (disabled), pending owner approval of thresholds.
The original long silent attempt's whitespace explanation remains unproven.
The final source passed two identical 434-module gates, each with 7,307 tests,
16 skips and no errors; all 24 installed semantic-search tests passed. Relay
130 tests and strict Clippy, app backend 44/browser 12, and six live HTTPS groups
passed. No paid provider probes were made; physical phone acceptance is separate.

The semantic-search interface draws on Ouroboros v7.10.0 (MIT)
`tools/query_code.py` and `code_navigation.py`; implementation reuses Praxis
Forge and owned search, with no source copied.
`CORE-SOURCE.json` records all selected source digests, mirror digests and
executable modes, together with the canonical mirror digest.

Two live files stay out of the mirror: a stray copy of `mtproto_runner.py`
committed alongside a skill and a one-off script that reads a private inbox
path. Eight files that Hélène releases 1.7.0 and 2.0.0 had placed into
`praxis/` were never part of live Praxis; they are retained so the next
Hélène composition does not lose them silently. Private memory, soul,
workspace, credentials and private Git history are excluded. Two public
documentation sanitizations (third-party handles and a name in comments)
preserve the executable code.

No gate was re-run for this mirror: it publishes code that is already live,
and the live qualification receipts are named in `CORE-SOURCE.json`. The next
Hélène composition takes this base. New Praxis tests (from 2026-11-13 and
2026-11-14) enter it while their subject modules remain overridden by older
`helene/core/` files, so they are expected to show that gap at the next desktop
release gate until the engine changes are ported. Immutable desktop
tags and shipped per-platform inventories retain the authority for each
released composition.

Praxis's own server application (the MiniApp/PWA and the `praxis-desk`
backend) lives in [`remote/praxis-desk/`](remote/praxis-desk) as the exact
deployed source; see its README.

The records below describe historical 2.2.9 deliveries. Their immutable tags,
digests and older voice references are provenance, not the current desktop source
selection. `HELENE-SOURCE.json` and `helene/PLATFORM-SOURCE.json` are retained as
historical inputs; current composition inventories are generated for each new
archive.

## Historical 2.2.9 bindings

### Hélène 2.2.9 source and package bindings

Implementation `f043cb73cebaea40778d8f445762247d00fd8f27`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.9-windows`, commit `119ceec841d3f6afd0d413ca66852f21babb5561`, archive `Helene-2.2.9.zip`.
- **macos**: tag `source/helene-2.2.9-macos`, commit `68e16a8d023123fb0c0409a9d131e3da11600337`, archive `Helene-2.2.9-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.9-debian`, commit `e6a6b11ecff7a9d7fac440aea28d0177f352fdf5`, archive `Helene-2.2.9-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.9-fedora`, commit `1f614f3cf406b377c06f78b8d7cf54821629bf00`, archive `Helene-2.2.9-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.9B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before the selected preserve or pristine source policy. Preserve still requires independent review after agent resolution. See desk/docs/SOURCE-ADMISSION.md.

The updater offers both policies without a separate preparation command. Legacy pending agent work safely reopens the previous app; the next invocation carries only a matching resolved candidate into independent review. Native runtimes/platform adaptations retain qualified2.2.4 inputs; all five interfaces were rebuilt for the source-mode controls. Voice references remain pinned2.1.1. Rejected2.2.5 was removed;2.2.6 was withdrawn after the normal-startup-cache failure;2.2.7 was withdrawn after the owner-owned relay lifetime failure. 2.2.8 is withdrawn after its restricted merger ended without resolving conflicts. The Praxis public source mirror is unchanged.
