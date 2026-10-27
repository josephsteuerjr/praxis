# Hélène 2.2.10 source and package bindings

Implementation `5fef0a121d056179d1713abc4dc4e28e5e59a023`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.10-windows`, commit `24eeeeb20e4755df3bc8d06c0182c0296083b6a7`, archive `Helene-2.2.10.zip`.
- **macos**: tag `source/helene-2.2.10-macos`, commit `80cfe5f25eef32464f98bf08a5af11b44cb6c422`, archive `Helene-2.2.10-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.10-debian`, commit `2555ea567525dd6fe52e95cedaad397b15cd573d`, archive `Helene-2.2.10-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.10-fedora`, commit `8a050ea0ed4bc0cbd2a4936583777a5f9954a5f2`, archive `Helene-2.2.10-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.10B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before the selected preserve or pristine source policy. Preserve still requires independent review after agent resolution. See desk/docs/SOURCE-ADMISSION.md.

The updater offers both policies without a separate preparation command. Legacy pending agent work safely reopens the previous app; the next invocation carries only a matching resolved candidate into independent review. Native runtimes/platform adaptations retain qualified2.2.4 inputs; all five interfaces were rebuilt for readable tool details and their qualified outputs were retained. Voice references remain pinned2.1.1. The Praxis public source mirror is unchanged.
