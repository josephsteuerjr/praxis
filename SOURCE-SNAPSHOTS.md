# Hélène 2.2.9 source and package bindings

Implementation `f043cb73cebaea40778d8f445762247d00fd8f27`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.9-windows`, commit `119ceec841d3f6afd0d413ca66852f21babb5561`, archive `Helene-2.2.9.zip`.
- **macos**: tag `source/helene-2.2.9-macos`, commit `68e16a8d023123fb0c0409a9d131e3da11600337`, archive `Helene-2.2.9-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.9-debian`, commit `e6a6b11ecff7a9d7fac440aea28d0177f352fdf5`, archive `Helene-2.2.9-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.9-fedora`, commit `1f614f3cf406b377c06f78b8d7cf54821629bf00`, archive `Helene-2.2.9-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.9B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before the selected preserve or pristine source policy. Preserve still requires independent review after agent resolution. See desk/docs/SOURCE-ADMISSION.md.

The updater offers both policies without a separate preparation command. Legacy pending agent work safely reopens the previous app; the next invocation carries only a matching resolved candidate into independent review. Native runtimes/platform adaptations retain qualified2.2.4 inputs; all five interfaces were rebuilt for the source-mode controls. Voice references remain pinned2.1.1. Rejected2.2.5 was removed;2.2.6 was withdrawn after the normal-startup-cache failure;2.2.7 was withdrawn after the owner-owned relay lifetime failure. 2.2.8 is withdrawn after its restricted merger ended without resolving conflicts. The Praxis public source mirror is unchanged.
