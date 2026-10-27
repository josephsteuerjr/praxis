# Hélène 2.2.9 — automatic source integration through Update

2.2.9 offers two explicit source policies: preserve local changes with the selected agent resolving conflicts, or install pristine release source. Profiles, memory and settings stay on their existing paths; previous code remains available for rollback. [Release notes](desk/docs/RELEASE-2.2.9.md).

Use the ordinary **Update** button, including from the supported 2.1.1 baseline. The new updater takes custody, materializes verified incoming source, combines it with authored changes, obtains independent review and completes the transaction. Compatibility repairs can include automatically merged files. [Release notes](desk/docs/RELEASE-2.2.9.md).

Windows starts in the tray at login and invokes its own bundled shell only for commands. Startup no longer probes the WSL launcher. [Release notes](desk/docs/RELEASE-2.2.9.md).

Updates now continue after reviewed conflict resolution, helpers run without a Windows console, the frameless update window follows the application theme, and every launch opens Now. [Release notes](desk/docs/RELEASE-2.2.9.md).

Hélène runs from a folder that contains its own Python runtime, desktop application and agent code. That folder becomes the agent's home: its profile, memory, configuration and authored source stay with it. Extract the archive to a permanent, writable location and launch the entry for your operating system. A separate Python installation or compiler is not required.

The agent can develop its own working code, while ordinary application updates keep using the same directory. Changes that belong in the shared product can be reviewed and included in later Hélène releases. The migration tools preserve authored code and Git state; publishing an application release does not publish an agent's personal memory or credentials.

This release completes the move to an archive-first desktop on Windows x64, Apple Silicon, Debian and Fedora x86_64. Python owns the window, agent lifecycle, migration and update coordination. Platform runtimes and the existing native tools for operating-system access are included where needed.

## What changes in 2.2

Forge separates execution from acceptance: dependent work waits for the coordinator's decision on the exact result. Roles retain enforced tool boundaries, shared-root writers serialize, and queued work, checkpoints and full worker output remain inspectable. An editing parent explicitly waits on one immediate editing child to yield the shared writer slot, then consumes its exact result before continuing; this does not permit two concurrent writers. The owner task panel reads these records and submits task/group controls without creating an ordinary agent turn.

Source conflicts use an isolated merger plus the existing independent panel over exact A/L/B/M inventories. Changed candidates need new evidence; missing capabilities/materials keep the old program. The 2.1.1 transition admits the incoming helper through an explicitly declared compatibility view. That gate preserves the current local tree; the new helper then materializes real upstream source and performs the actual A/L/B/M merge and independent review. See [source admission](desk/docs/SOURCE-ADMISSION.md) for the distinction.

Routine updates back up settings and program/code preimages, not unchanged memory/models. In current versions preparation precedes old-window closure; the legacy 2.1.1 helper takes custody before the old owner exits and prepares with its own progress observer; a progress observer follows the durable operation. POSIX timestamp-only freshness cannot skip hashing. Manual/periodic full backups remain separate.

Application-owned XTTS and a narrow speak adapter support older authored code without overwriting it. WAV preview and actual Ogg/Opus voice-note output differ; the installed SoundFile/libsndfile codec capability is checked, without requiring an extra PyAV download. CPU synthesis needs substantial free memory: one measured Windows stock-voice run peaked near 4.9 GB worker RSS, with about 128 seconds for the first client call including loading and 9 seconds for a separate warm synthesis. These are scoped observations, not a minimum-memory or latency guarantee. Every launch opens **Now**, including after updates and before initial setup; setup and manual onboarding remain available.

Hélène 2.2.9 is delivered as four portable archives. See [the complete release notes](desk/docs/RELEASE-2.2.9.md) for the changes, transition advice and exact qualification limits. Publishing a release does not update your running agent.

## Previous changes in 2.1

### A simpler voice conversation

- Voice messages appear as compact waveform strips in the composer and conversation, with playback controls alongside the message.
- Hold the configured shortcut to record. Releasing it while Hélène is focused pauses the draft so you can listen, continue recording or send it. Releasing it while another application is focused sends the recording.
- Hold **Shift when releasing** to keep a background recording as a draft. The next hold continues it. **Esc** cancels recording and prevents the subsequent release from sending it.
- Shortcut configuration and automatic playback live in **Settings**. A small hint beside the composer links to a help popup explaining voice controls.
- The separate “live mode” flow has been removed. Recording and playback use the same conversation controls, with saved preferences and protection against replaying the same automatic audio twice.

### A refreshed first run and appearance

The onboarding page has been refreshed, and voice help follows the same visual style. Appearance settings include Studio, Glass and Terminal treatments, typography, shape and background choices. The Windows migration window uses Hélène's custom chrome, branded content typography, a small regular title/version label and a sharper window/taskbar icon.

### Unpack once; migrate into that folder

The Windows archive now contains **Start Helene.vbs** and **Migrate Helene.vbs**. They launch the bundled Python application and migrator directly. Migration copies the previous profile and authored source into this unpacked application; it does not extract and copy the whole program into a second installation directory.

The previous installation is stopped before its final profile snapshot. The new window must confirm its own profile before retirement begins. Where the supported Windows native package is registered, its own uninstaller is invoked with `--uninstall --quiet`, without the data-purge switch. The command is bound to the selected installation, and a service belonging to another installation blocks that removal. Remaining shortcuts are retargeted to the new Python entry. Old Python windows are asked to exit through their authenticated control channel; a stale lock-file label alone is not treated as proof of a live owner.

On Debian and Fedora, the portable migration flow preserves the profile, working source and Git state before removing the identified native package. On macOS, use the bundled migration application to copy an existing profile; the previous application folder is retained. Ordinary updates then operate in the same portable folder, with recovery when the replacement fails to start.

### Optional voice components, independent relay

**Whisper and XTTS are the only optional large component downloads.** Their platform-specific runtime/model archives are pinned by size and SHA-256 and reused from the retained 2.1.1 release. They are downloaded only when selected. Do not delete the 2.1.1 tag or its voice assets: the bundled component manifests deliberately refer to those immutable URLs. The native rebuild kit is no longer offered as a new component download. Existing optional installations are preserved during migration and updates.

The subscription relay is a separate product: [praxis-relay 1.1.0](https://github.com/josephsteuerjr/praxis-relay/releases/tag/v1.1.0). Hélène can use its independently distributed executable without installing a compiler. XTTS model use remains subject to the model's CPML non-commercial terms; bundled third-party notices and license files remain included.

## Downloads and first launch

| Platform | Archive | Entry after extracting the whole folder |
| --- | --- | --- |
| Windows x64 | [Helene-2.2.9.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.2.9/Helene-2.2.9.zip) | **Start Helene.vbs**; **Migrate Helene.vbs** for an existing installation |
| macOS Apple Silicon | [Helene-2.2.9-macos-arm64.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.2.9/Helene-2.2.9-macos-arm64.zip) | **Helene.app** or **Migrate Helene.app** |
| Debian x86_64 | [Helene-2.2.9-linux-x86_64.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.2.9/Helene-2.2.9-linux-x86_64.zip) | **Start Helene.sh**, using your file manager's “Run as a program” action |
| Fedora x86_64 | [Helene-2.2.9-fedora-x86_64.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.2.9/Helene-2.2.9-fedora-x86_64.zip) | **Start Helene.sh**, using your file manager's “Run as a program” action |

Keep the complete folder together, in a location you can write to. Use the in-app updater for subsequent updates; do not unpack a new archive over a running application. The Windows scripts use Windows Script Host and the bundled Python runtime. Linux desktops may ask you to trust a launcher. Operating-system permission prompts remain under your control.

There is no new Windows setup executable or DEB/RPM in this release. Each application archive has a `.sha256` sidecar; `SHA256SUMS` covers the attached delivery files. `voice-components.json` lists the pinned optional downloads, and `release-evidence.json` records the exact application bytes and source snapshots.


## More about Hélène and Praxis

## A home for independent agents

Each agent has its own constitution, memory, settings, files and runtime.
Choose a default, switch agents, or open one specifically. Creation can use a
provided constitution, an inherited one or your own text. A dedicated Doctor
agent can inspect an installation and propose repairs; it is not an invisible
background repair service.

The chat interface includes remembered drafts and reading position, files,
images, work and context views, explicit Pause/Continue and emergency Stop.
A paired phone/web interface and Telegram Mini App provide additional surfaces
while the host is online. Availability depends on the configured transport and
network; no public tunnel or server is supplied by downloading the desktop.

## Telegram, memory and tools

Telegram can use a Bot API token or a separate MTProto/Telethon account.
Their capabilities differ. Incoming events are retained before attention and
admission decisions; room/topic routing, explicit replies, staged media and
outgoing receipts preserve what actually happened. Account tools act only
within the account's real rights.

Markdown and JSONL are the inspectable memory canon; SQLite/FTS accelerates
lookup. Journals, room memory, projects, skills, authored identity and retained
runs support continuity. Authored code and character changes are not permission
for an installer to silently reset a living agent's files.

Forge records engineering work with a goal, source directory, workers,
checkpoints and results. File search, exact edits, shell commands and computer
tools are separate capabilities. A recorded action or a green unit test does
not by itself prove that an entire task succeeded.

## Models, relay and local voice

Use your configured provider or supported subscription relay. Credentials,
account access, quotas and provider charges are separate from the application.
The embedded Hélène relay is an adaptation for this desktop; the standalone
`praxis-relay` repository is a different distribution.

Whisper and XTTS run locally when installed. CPU synthesis can be slow and is
not advertised as real-time conversation. XTTS model weights are governed by
the **Coqui Public Model License**, including its non-commercial conditions;
that is separate from the licenses of the application and code dependencies.
The rebuild kit carries the selected native source and offline build inputs,
not a promise that every third-party toolchain is freely redistributable on
every platform. Consult the supplied LICENSE, NOTICE and third-party notices.

## What the qualification does — and does not — establish

Platform work exercised real packaged consumers: native windows, controlled
migration and failure/retry paths, profile/Git preservation, selected local
speech inference and native rebuilds. These are scoped tests on disposable
profiles, not a claim of universal compatibility or production account testing.

Physical microphone, sign-in, all authenticated model providers, all computer
permissions, arbitrary large-profile performance and every desktop environment
are not a completed full-QA matrix. In particular, macOS permission dialogs
were observed without granting the user's permissions. The release notes keep
platform-specific exceptions next to the download instructions.

## Source and contribution

This repository contains the Praxis runtime and Hélène source. Release assets
carry platform-specific source snapshots and their provenance where builders
use different qualified inputs. Private conversations, credentials and live
profiles are not publication inputs. Development is a separate activity from
upgrading the agent already living on your machine.

Created by Yegor Kosyrev. Original and third-party attribution is retained in
LICENSE and NOTICE. Contributions should state what changed, which consumer
was exercised and what remains unverified.

Source code: [current Praxis and per-platform Helene snapshots](SOURCE-SNAPSHOTS.md).
