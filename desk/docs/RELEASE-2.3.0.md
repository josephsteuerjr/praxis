**Hélène 2.3.0 brings the 2.3 candidate work together into a stable, four-platform desktop release.** It includes the application, the Praxis agent runtime, editable source and a ready Python environment.

Download the ZIP for your system, extract it into a fresh folder, and open:

| System | Application |
| --- | --- |
| Windows x64 | `Helene/helene.exe` |
| Apple silicon | `Helene/Helene.app` |
| Debian 12 or Fedora 44 x86_64 | `Helene/helene` |

Git, a compiler and a separate Python installation are not required to start. For an existing authored installation, use the application's update flow; the agent's data folder is separate from the program.

**A more useful desktop and right panel.** The interface follows the Praxis application: consistent paper, typography and controls across its pages, a calmer Now screen and practical onboarding. The right panel exposes the actual model request, tools and observed results. Long outputs remain readable and can be opened in pages; incomplete results remain identified as incomplete. Settings have a navigable index, while the agent's work, conversations, files, context and diagnostics have their own views. Navigation and polling now have one owner, so leaving a page retires its old requests and delayed replies do not revive an obsolete view.

**Forge work you can inspect and control.** Engineering work is presented as tasks with nested workers, roles, requested models, observed execution and results. Model inputs, tool arguments, execution receipts and outputs stay attached to the operation that produced them. A worker finishing is distinct from its result being accepted. The owner can inspect progress, history and review evidence, interrupt work and distinguish an acknowledged command from an unconfirmed outcome. Partial payloads, interrupted reads and unavailable control routes keep their actual status instead of becoming success messages.

**Updates owned by the host, not a window.** An ordinary update is a durable in-place operation. Closing its observing window does not cancel the job; reopening attaches to the same operation. The application explains when it will close and reopen automatically. The coordinator, test launcher and review have separate responsibilities. Stop and Pause remain explicit controls, and cancellation settles only after the identified producers are confirmed absent. A lost response, process identity or custody proof remains unresolved rather than being automatically replayed.

**Preserve authored code or choose clean release code.** Preserve mode integrates actual agent/owner changes, including additions and deletions, with isolated conflict resolution and independent review. Updates involving several core variants and one shared application are treated as one composition. Changed candidates need matching fresh evidence. The normal verification tier covers its published contract floor, affected suites and importing consumers; unknown impact or changes to the verifier, cutover, dependencies and other languages widen the recipe. Clean mode explicitly chooses release code while keeping the separate profile. Rollback restores the prior code selection. An emergency Restore upstream route is available beside Update.

**First launch, migration and agent identity.** The first window can create an agent, connect existing data or migrate an older installation. New agents can use the Hélène constitution or an editable custom text/file. The first SOUL is materialized by the explicit Create action, before provider sign-in. Existing agents and selected data folders stay connected, while an ambiguous missing-data situation is not silently treated as a fresh birth. Setup and migration have their own preview, progress and recovery path, including a native Qt migration window on Linux. Portable programs run as the logged-in user.

**Local agents, server agents and everyday connections.** The desktop distinguishes an agent living beside the window from one living on a server. Server settings are written through that server's channel rather than through a local lookalike configuration. Bot and MTProto Telegram connections, paired phone/web access and configured computer tools remain part of the application. Computer access follows the device's actual grants; the window exposes its observed availability. Model accounts, API endpoints and optional local providers remain owner-configured. The standalone Praxis relay is a separate product. Pause, Continue and emergency Stop remain separate from ordinary window closure.

**Voice in the conversation.** Local Whisper recognition and optional XTTS synthesis work alongside inline voice messages and waveforms. Hold-to-record shortcuts, draft parking, cancellation and playback remain available. Remote Fish controls add the full paged catalogue, explicit voice and model selection, and separate fields for the words and free-form delivery instructions. Drama 3 and S2.1 Pro are explicit alternatives. Previewing does not select a voice or send a message. A lost paid response or a page reload retains the exact request body and identity; an explicit replay uses the original intent, even if the visible draft has since changed. Another channel credential cannot restore that saved intent. Audio size and SHA-256 are checked before playback, and local cancellation does not claim to have stopped the vendor's generation.

Fish controls appear when the selected server offers its speech API. The server owns the provider credentials and its rollout; this desktop release does not install the backend. Local speech components are optional. XTTS model weights retain the Coqui Public Model License and its non-commercial conditions; third-party notices remain bundled.

**Cross-platform fixes behind the release.** Preserved updates now remove obsolete application files after composing the accepted result, retain executable modes and leave browser state alone. Native POSIX guardians retire nested test sessions when their launcher dies or is stopped. Remote publishers inherit the selected tree's scope rather than another agent's data lease. Browser audit servers and Playwright consumers live outside the automatic offline update lane. Cross-origin speech requests use channel Bearer authentication without requiring cross-origin cookies.

**What was verified.** Final Windows bytes were extracted and admitted by their bundled interpreter. The exact macOS archive opened in a real Cocoa window. Debian 12 and Fedora 44 archives ran as UID1000 under Xvfb/DBus, with the native Qt migration wizard and update regressions. All nine native screenshots were inspected. Targeted native regressions, 134 Node checks and 12 Fish browser checks passed, including real two-origin HTTP and fresh-page replay. GUI service data were synthetic. Physical microphone/TCC, live voice quality, a new independent whole-panel review and a rerun of the full historical battery are outside this release's stated evidence.

Qualified runtimes and native wrappers retain their exact existing bytes. A signed macOS wrapper can retain its historical Info.plist version; `python-app.json` identifies 2.3.0. Exact artifact hashes, source identities and validation scope are supplied in `SHA256SUMS`, `SOURCE-2.3.0.json`, `RELEASE-EVIDENCE.json` and `NATIVE-GUI-2.3.0.zip`.

### Reported Windows RC26 upgrade issue

An upgrade from the Windows 2.3.0-rc.26 portable build has been reported to stop
with `MigrationError: icacls не подтвердил защиту временного конфига`.
The support bundle confirms the temporary-configuration ACL refusal but does
not contain the underlying `icacls` failure. This specific report is under
investigation; 2.3.0 has not been qualified as its fix. The updater's refusal
before writing private configuration must remain in place.
