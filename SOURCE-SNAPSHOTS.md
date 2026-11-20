# Source and package bindings

## Hélène 2.8.12 source base

The immutable `v2.8.12` tag at
`3fc735cbea094292776f33823b6f7bc7ea316b9c` identifies the latest all-platform
release. Shipped per-platform inventories bind its qualified composition.
This main sync incorporates that already-public history and then the newer
cloud source below. It does not redefine the shipped desktop composition.

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

`praxis/` mirrors the selected public code of running cloud Praxis at
`140e5719a8a87d3d145eec91e2cbb69a8bad1fc7`. [CORE-SOURCE.json](CORE-SOURCE.json) binds
804 files and their executable modes. The engine adds an independent
Telegram client for the owner's account with named tools and wake rules.
The final core passed two full Linux gates before deployment, each with 7,372 tests,
19 skips and zero failures.

`remote/praxis-desk/` mirrors app source `4f2ddd50df869c0c6b668b00736f49909b68dd66`.
The MiniApp/PWA controls the owner's Telegram service and shows its tool receipts.
All 21 deployed backend files equal this source after line-ending normalization,
and the deployed frontend was built from the same commit.

The cloud mirror retains its public templates and Hélène-origin files.
Two existing comment and docstring sanitizations preserve the executable AST.
The snapshot contains public source and excludes private runtime state and Git history.
See [RELAY-SOURCE.json](RELAY-SOURCE.json) for relay provenance.

## Historical 2.2.9 bindings

### Hélène 2.2.9 source and package bindings

Implementation `f043cb73cebaea40778d8f445762247d00fd8f27`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.9-windows`, commit `119ceec841d3f6afd0d413ca66852f21babb5561`, archive `Helene-2.2.9.zip`.
- **macos**: tag `source/helene-2.2.9-macos`, commit `68e16a8d023123fb0c0409a9d131e3da11600337`, archive `Helene-2.2.9-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.9-debian`, commit `e6a6b11ecff7a9d7fac440aea28d0177f352fdf5`, archive `Helene-2.2.9-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.9-fedora`, commit `1f614f3cf406b377c06f78b8d7cf54821629bf00`, archive `Helene-2.2.9-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.9B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before the selected preserve or pristine source policy. Preserve still requires independent review after agent resolution. See desk/docs/SOURCE-ADMISSION.md.

The updater offers both policies without a separate preparation command. Legacy pending agent work safely reopens the previous app; the next invocation carries only a matching resolved candidate into independent review. Native runtimes/platform adaptations retain qualified2.2.4 inputs; all five interfaces were rebuilt for the source-mode controls. Voice references remain pinned2.1.1. Rejected2.2.5 was removed;2.2.6 was withdrawn after the normal-startup-cache failure;2.2.7 was withdrawn after the owner-owned relay lifetime failure. 2.2.8 is withdrawn after its restricted merger ended without resolving conflicts. The Praxis public source mirror is unchanged.
