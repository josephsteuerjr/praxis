# Hélène 2.2.6

Use the ordinary **Update** button. The supported 2.1.1 transition now hands custody to the new updater, performs the actual source merge and independent review, and completes the transaction in one invocation.

The rejected 2.2.5 manual-preparation release is withdrawn. Its extra preparation launcher and command-line workflow have been removed. A new version/filename avoids the old updater's refusal to replace a previously cached ZIP with different bytes.

## Changes

- A declared compatibility admission view lets the old 2.1.1 updater hand control to the incoming helper while carrying authored source unchanged. The helper then verifies and materializes the real pristine upstream source before the real A/L/B/M merge and review. The admission gate is not source acceptance.
- Compatibility repairs can include automatically merged source files. Every such repair has a reason, current-candidate binding and before/after journal; changed candidates require fresh independent frozen review.
- First startup and migration materialize the real source before controller/core imports or profile seeding. Interrupted promotion is recoverable. Unexpected authored, private or linked content is refused, and completed admission never overwrites later self-edits.
- Existing Stop/Pause, stale-input, custody, source preservation and rollback fences remain authoritative. A failed review retains/restores the previous program.

## Downloads

- **Helene-2.2.6.zip** — Windows x64 portable application.
- **Helene-2.2.6-macos-arm64.zip** — Apple Silicon portable application.
- **Helene-2.2.6-linux-x86_64.zip** — Debian-family x86_64 portable application.
- **Helene-2.2.6-fedora-x86_64.zip** — Fedora x86_64 portable application.
- **Helene-2.2.6-source.zip**, **release-evidence.json**, **SHA256SUMS** and individual `.sha256` files — source, scoped qualification and integrity records.
- **voice-components.json** — unchanged optional Whisper/XTTS references to existing 2.1.1 assets; this patch does not require another model download.

For a first installation, extract the application into its permanent writable folder and launch it normally for your operating system. Existing supported 2.1.1 users update through the normal button. The initial legacy path transfers custody before closing its old owner; the incoming progress observer covers the remaining preparation and switch.

## Qualification

The regression runs unmodified public 2.1.1 update-install code in a separate old-owner process: one invocation, real helper admission/old-owner exit, verified incoming materialization, real restricted Forge/reviewer children using a deterministic localhost provider, semantic repair, canonical frozen review, actual overlay, controlled app-readiness confirmation, completion and rollback. Original authored source, model settings and profile fixture bytes are preserved. First startup, recovery and data-refusal checks are separate gates.

This qualifies lifecycle, integration and evidence contracts; it is not a real model's review of an owner's code or native GUI/device acceptance. Existing qualified native runtimes/platform adaptations/interface bytes are retained. All four archives receive CRC/manifests/source/admission/upstream/runtime-record checks. No owner installation, live provider/Telegram/production action or public download retest is claimed. The actual supported legacy baseline is explicit; unqualified historical baseline combinations are not represented as accepted.
