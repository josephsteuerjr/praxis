# Hélène 2.2.8 source and package bindings

Implementation `355cdddda0e16920732801e0f952ffb2572522ad`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.8-windows`, commit `4ecf35f30023e2d3af49b1a7f20087f2c9ce3cd4`, archive `Helene-2.2.8.zip`.
- **macos**: tag `source/helene-2.2.8-macos`, commit `f80c83207038ca2b7a2d2d0e8fdc09b8a9151615`, archive `Helene-2.2.8-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.8-debian`, commit `8ad453de89c0e24f9cf649b66bd3a5822aa8c174`, archive `Helene-2.2.8-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.8-fedora`, commit `34cafcdd7019e5d026d9b2c010caea915f2a5d79`, archive `Helene-2.2.8-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.8B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before actual source merge and independent review. See desk/docs/SOURCE-ADMISSION.md.

Ordinary Update completes this supported transition in one invocation; no separate preparation command/launcher. Native/runtime/platform adaptations and interface bytes remain from qualified2.2.4. Voice references remain pinned2.1.1. Rejected2.2.5 was removed;2.2.6 was withdrawn after the normal-startup-cache failure;2.2.7 is withdrawn after the owner-owned relay lifetime failure. The Praxis public source mirror is unchanged.
