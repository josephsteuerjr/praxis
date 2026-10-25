# Praxis & Hélène

**A persistent agent runtime, and a desktop home for agents you make your own.**

Praxis is the agent runtime: inspectable memory, tools, Telegram conversations,
long-running work and self-authored code. Hélène is its desktop distribution, with
independent agents, a Python-owned application and process supervisor, a shared
chat interface, files and optional local voice.

[Download Hélène](https://github.com/josephsteuerjr/praxis/releases/latest) ·
[Release history](https://github.com/josephsteuerjr/praxis/releases) ·
[Standalone Relay](https://github.com/josephsteuerjr/praxis-relay)

## Hélène 2.0.0

This release brings the Python desktop to **Windows x64, macOS Apple Silicon,
Debian and Fedora x86_64**. Choose the package for your operating system; the
runtimes and optional components are platform-specific, not interchangeable.
See the release notes for the exact tested platform, installation route and limits.

- **Python owns the application.** The window, agent lifecycle, process tree,
  Stop, update preparation and recovery are handled by Python. Native bodies and
  the embedded model relay remain separate, inspectable components.
- **A migration wizard, not a memory reset.** Keep the old application until the
  new one starts successfully. Migration retains configuration, memory, authored
  code and Git changes. Stop the old application before copying its profile.
- **Ordinary updates stay in the application.** After the initial transition,
  the Python updater prepares a checked package, hands over to a helper and can
  roll back a failed start. Do not manually overlay a running installation.
- **Voice is optional.** Whisper recognition is independent of text-to-speech.
  A voice message enters the agent conversation only after successful
  transcription; failed recognition has an explicit retry state. Inline audio
  controls support seeking, volume and playback speed.
- **Download components when needed.** Whisper, XTTS and the native rebuild kit
  are separate archives. The bundled component map pins each archive's URL,
  size and SHA-256, can reuse cached bytes and may reference an older release.
  Keep those older releases when they supply a component.

### Installation

**Windows:** run the `helene-2.0.0-setup.exe` migration/setup wizard. The small
wizard downloads `Helene-2.0.0.zip` and the selected optional components. For a
migration from the older native edition, use this new wizard rather than its
old Update button. Windows executables are unsigned.

**Apple Silicon Mac:** unpack the macOS archive into a separate folder. Open
`Migrate Helene.app` to transfer an existing profile, or use `Helene.app` for the
application. Quit the old app and disable its old launch-at-login integration
before migration. The old folder is retained. macOS may ask for local-network,
microphone or accessibility permission; those decisions remain yours. This is
not a notarized/Gatekeeper-qualified distribution.

**Debian / Fedora:** install the matching DEB/RPM with your normal package
manager, or use the matching portable archive and its `helene` entry point.
The package owns an immutable bootstrap seed; first launch copies it into a
writable per-user application directory. Removing the package does not erase
that profile. The graphical migration wizard can preserve an older profile;
fresh setup configures the current program. Linux desktop computer access uses
X11 and AT-SPI; Wayland and headless sessions have explicit limits.

Each main archive has an individual `.sha256` sidecar. `SHA256SUMS` and the
release evidence describe the published files. The platform source archives
are build inputs, not your agent's private profile.

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
