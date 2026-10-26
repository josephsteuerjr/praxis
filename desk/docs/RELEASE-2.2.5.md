# Hélène 2.2.5

This release lets the isolated updater repair compatibility across the combined source candidate, including files that Git merged without a textual conflict. It also provides an explicit preparation entry for older updaters that stop before they can launch the new coordinator.

## Fixes

- The merger can read and repair any declared source input. Repairs outside the original conflict list require a concrete reason and the exact current candidate hash; before/after hashes are retained in the merge journal. Unrecorded edits, changed inputs and undeclared/private paths are refused.
- A corrected candidate receives a new frozen independent review. An earlier review cannot approve changed bytes. Authored source and settings remain selected until the normal transaction switches; original local source and program preimages remain available for rollback.
- Hélène 2.1.1's own button stops on conflicts before launching an incoming helper. The new preparation entry uses the incoming verified runtime while the old application remains open, without requiring new Forge APIs in the old authored code. It prepares the exact merge for a subsequent normal Update click; it does not install or restart the old application.
- Worker qualification handles older authored LLM modules that lack the optional `assistant_message` helper. The real worker fallback is checked through its loop and durable checkpoint. Source-only candidate checks report the absent application reviewer as unverified/skipped; source and complete package layouts still exercise it.

## Upgrading from 2.1.1

1. Download and extract the new archive into a separate folder. Keep the existing installation and its profile.
2. On Windows, run **Prepare update.vbs** from the new folder and select the existing Hélène directory containing its `helene.json`.
3. When preparation reports that the code has been reviewed, return to the old window and retry **Update** for **this same 2.2.5 release**. If the old code or selected settings changed in between, prepare again.

On macOS/Linux, run the same preparation entry with the new extracted runtime:

```sh
./runtime/bin/python3 -s app/python_update_prepare.py --old /path/to/existing/Helene
```

Use `--browser` only when you explicitly want the browser progress observer. Stop/Pause, cancellation, failed review and changed inputs retain the old program. The preparation entry rejects a different platform, the same/older release, and nested source/target installations. Future updates from current versions perform preparation through the ordinary button.

## Downloads

- **Helene-2.2.5.zip** — Windows x64 portable application.
- **Helene-2.2.5-macos-arm64.zip** — Apple Silicon portable application.
- **Helene-2.2.5-linux-x86_64.zip** — Debian-family x86_64 portable application.
- **Helene-2.2.5-fedora-x86_64.zip** — Fedora x86_64 portable application.
- **Helene-2.2.5-source.zip** — exact public source and package bindings.
- **SHA256SUMS**, individual `.sha256` files and **release-evidence.json** — integrity and scoped qualification records.
- **voice-components.json** — unchanged optional Whisper/XTTS references pinned to their existing 2.1.1 assets; no repeated model download is required by this patch.

## Qualification boundaries

Isolated server tests exercise the actual public 2.1.1 preflight, candidate repair, stale evidence rejection, Stop/Pause fences, incoming coordinator and real Forge/reviewer child processes using a deterministic localhost provider. The full bridge/preparation/install/rollback fixture checks original authored source, model settings and profile preservation. This validates lifecycle and evidence contracts; it is not a real model's semantic review of an owner's code.

Qualified 2.2.4 native runtimes and interface bytes are retained. All four final archives receive full CRC, manifest, source inventory and unchanged runtime-record checks. Publishing this release does not update an owner's installation. Owner-button/native GUI acceptance, physical microphone permissions and live provider calls are separate and are not claimed here. No public download retest is included.
