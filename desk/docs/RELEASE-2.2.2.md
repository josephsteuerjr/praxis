# Hélène 2.2.2 — smoother updates and a consistent desktop

This maintenance release improves the update experience introduced in 2.2.0.

## Fixes

- **Updates continue after conflict review.** The original update request keeps its verified download and resumes automatically when the same merge passes independent review. You no longer need to press Update again. A pending update can be cancelled; closing the app cancels that continuation. Changed source or settings still require a fresh review.
- **The update window now matches Hélène.** It uses the application's typography, colours and saved light or dark appearance, with a compact layout that fits smaller windows. The native title bar is removed; the themed header provides dragging, minimize and a close button that becomes available after the transaction finishes. Completed updates stop the elapsed-time counter.
- **No temporary Python console on Windows.** Update, rollback and cleanup helpers use windowless execution, so an auxiliary console no longer remains attached to the reopened application.
- **Progress readers no longer interrupt a Windows update during brief file contention.** Receipt publication waits briefly for an external reader to release the file. Persistent access failures still surface, and the previous complete receipt remains intact.
- **Every launch opens Now.** Hélène starts on the Now screen, including after an update and before initial setup. Setup remains available through an explicit action, and onboarding can still be opened manually.
- **More accurate Forge control feedback.** Refused and unconfirmed actions are no longer reported as successful. Their original results remain available for inspection.

## Downloads

| Platform | Portable archive | Start |
| --- | --- | --- |
| Windows x64 | `Helene-2.2.2.zip` | `Start Helene.vbs` |
| macOS Apple Silicon | `Helene-2.2.2-macos-arm64.zip` | `Helene.app` |
| Debian x86_64 | `Helene-2.2.2-linux-x86_64.zip` | `Start Helene.sh` |
| Fedora x86_64 | `Helene-2.2.2-fedora-x86_64.zip` | `Start Helene.sh` |

**Which assets do I need?** Download **one program ZIP for your OS** from the table, extract the whole archive into a permanent folder, and open the listed launcher. On Linux, run `bash "Start Helene.sh"` from that folder. If Hélène is already installed as a portable app, use its **Update** action instead of unpacking files over the running app.

`Helene-2.2.2-source.zip` and GitHub’s **Source code** downloads are for developers; they are not runnable app bundles. `.sha256` files and `SHA256SUMS` verify downloads. The two `.json` files describe package checks and optional voice components; you do not need to open or install them. Select optional voice components in the app when needed.

Existing portable installations update in place, preserving the selected profile and authored code. Use Hélène's update flow rather than extracting a ZIP over a running installation. The updater already running in an older release cannot gain these improvements until 2.2.2 is installed; that first transition may still use the previous review flow.

Optional Whisper and XTTS components reuse the existing pinned 2.1.1 downloads. No model re-download is required by this maintenance release. The 2.1.1 assets remain available, and the standalone relay keeps its separate distribution.

## Verification and scope

The reviewed source passed 168 focused Python tests, startup and Forge UI checks, and TypeScript/Vite builds. The final updater changes additionally passed 27 focused observer, file-contention and update I/O tests (one platform-specific skip). The four archives reuse their qualified 2.2.0 native runtimes, with regenerated source inventories and checksums. Native update and rollback checks passed on Windows, macOS Apple Silicon, Debian and Fedora using isolated synthetic profiles. Those scenarios qualified the fixes under the provisional 2.2.1 version. Final 2.2.2 packaging updates version metadata and dependent checksums; every executable, built interface, runtime and stock-agent file is byte-identical to the corresponding tested archive. The embedded Linux source ZIPs were recompressed with identical contents and permissions. The release evidence records this qualification and the final archive hashes.

These packages remain unsigned/unnotarized. Physical microphone access, OS permission persistence, live provider authentication and the full device matrix are outside this release's acceptance checks. Repeated microphone permission prompts have not been confirmed or fixed in this release. Existing third-party licences, including XTTS model terms, continue to apply.
