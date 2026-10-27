# Hélène 2.2.7 source and package bindings

Implementation `5d396be75ed5ec8872bcbcd133d9c8c0baea614f`; qualified package/runtime base2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.7-windows`, commit `b389ec7974b0f1d8e4495d7fc48d6bc7a51faa7a`, archive `Helene-2.2.7.zip`.
- **macos**: tag `source/helene-2.2.7-macos`, commit `c7e88e78e1650611dcb094a7ea11cf32a6293f81`, archive `Helene-2.2.7-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.7-debian`, commit `bb6736a17b761777237939c3a08943168ee7072b`, archive `Helene-2.2.7-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.7-fedora`, commit `65684942454fef1b2b2d3478f8c9f18889e5df86`, archive `Helene-2.2.7-fedora-x86_64.zip`.

Snapshots bind exact packaged application inputs and actual pristine2.2.7B in helene/core/. The explicitly separate helene/admission/source/ is the shipped official2.1.1 compatibility view used only to hand custody to the incoming helper. That helper verifies/materializes realB before actual source merge and independent review. See desk/docs/SOURCE-ADMISSION.md.

Ordinary Update completes this supported transition in one invocation; no separate preparation command/launcher. Native/runtime/platform adaptations and interface bytes remain from qualified2.2.4. Voice references remain pinned2.1.1. Rejected2.2.5 was removed;2.2.6 is withdrawn after the normal-startup-cache failure. The Praxis public source mirror is unchanged.
