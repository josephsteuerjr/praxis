# Hélène 2.2.6 source and package bindings

Implementation `010b66e44a1f51d77aec81e3eead7df92024a628`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.6-windows`, commit `8a3eca6f80b07472d6a7ada073279c58c7152baa`, archive `Helene-2.2.6.zip`.
- **macos**: tag `source/helene-2.2.6-macos`, commit `07a582ef68fc9283d772633c782f6975149ba99b`, archive `Helene-2.2.6-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.6-debian`, commit `c4ade9c826bd9ddd8081d7b6b439808e6cfd464d`, archive `Helene-2.2.6-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.6-fedora`, commit `fcd8e12bdcdddb2b0e6fd4705987f36b0f9baa86`, archive `Helene-2.2.6-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.6B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before actual source merge and independent review. See desk/docs/SOURCE-ADMISSION.md.

Ordinary Update completes this supported transition in one invocation; no separate preparation command/launcher. Native/runtime/platform adaptations and interface bytes remain from qualified2.2.4. Voice references remain pinned2.1.1. Rejected2.2.5 is withdrawn. The Praxis public source mirror is unchanged.
