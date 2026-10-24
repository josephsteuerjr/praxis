# Praxis & Hélène

**Praxis** is an agent runtime with persistent memory, Python tools, Telegram
integration and durable coding tasks.

**Hélène** is the desktop application built around that runtime. It provides
conversations, agent management, settings and a window into ongoing work.

The project is designed for long-lived, self-authored agents, not only disposable
chat sessions. An agent's code, memory and settings remain inspectable.

The runtime source, the packaged desktop edition and a running installation are
different things. Publishing source does not deploy an agent, and a desktop
release does not necessarily contain every change in the current runtime mirror.

[Current Windows release](https://github.com/josephsteuerjr/praxis/releases/tag/v1.9.4) |
[Release history](https://github.com/josephsteuerjr/praxis/releases) |
[Praxis Relay](https://github.com/josephsteuerjr/praxis-relay)

## Current desktop release: Hélène 1.9.4

Hélène 1.9.4 is the Windows Python distribution. It includes the application,
its own Python runtime, the UI and bundled relay source.

The 1.9.3 release introduced a shared component-download map, startup recovery,
additional appearance controls and component installation from the application.
Version 1.9.4 fixes appearance-conflict recovery and prevents delayed manual
changes from overwriting a newer appearance selection.

### Install or make the first upgrade from Python 1.9.1

1. Open the [1.9.4 release page](https://github.com/josephsteuerjr/praxis/releases/tag/v1.9.4).
2. Download `Helene-1.9.4.zip`, extract it and run the new `helene-setup.exe`
   inside, or use the separate `helene-1.9.4-setup.exe`.
3. Keep a backup of an existing installation before upgrading.

**For the first transition from the old Python 1.9.1 application, use the new
setup wizard, not the old Update button.** That old updater looks for component
archives in the new release before loading the new code and does not understand
the cross-release component map.

The newer Python updater uses the shared map, but that does not retroactively
repair the old updater. The published 1.9.4 package does not include later,
unpublished updater repairs.

### Optional components

Whisper, XTTS and the rebuild toolchain are separate downloads. The toolchain
is not required for ordinary application use.

The component map supplies URLs, SHA-256 hashes and sizes. Versions 1.9.3 and
1.9.4 reuse unchanged large archives hosted in release 1.9.2; those older assets
remain dependencies and must not be removed.

XTTS has separate CPML/non-commercial licensing conditions. Do not assume that
the repository's Apache-2.0 license grants unrestricted use of third-party models.

## The desktop home

- **Conversations and materials:** a paper-like chat, remembered drafts, images
  and file attachments, with visible ongoing work.
- **Independent agents:** switch between separate identities, constitutions,
  memories and settings; start or stop an agent individually.
- **Voice and remote access:** optional Whisper recognition and XTTS synthesis,
  paired mobile access and a Telegram Mini App. Availability depends on setup
  and transport; the host computer must remain online for its remote interfaces.
- **Appearance:** presets, day/night/accent colors, density, panels, 70-150%
  window scale and system reset. Manual and owner-authorized agent changes use
  a shared writer.
- **Inspectable self-development:** source self-review and retained findings,
  with author decisions kept separate from independent review.

These are edition features, not a promise that every provider, voice model,
phone or desktop permission has been qualified on every machine.

## What Praxis provides

- **Persistent, inspectable memory:** conversations, facts, journals, projects,
  authored skills and ongoing intentions. Markdown and JSONL are canonical;
  SQLite/FTS accelerates retrieval.
- **Python tools and durable engineering work:** file operations, code inspection,
  commands, verification and Forge tasks with goals, source directories,
  checkpoints and retained results.
- **Independent agents:** separate constitutions, memory, settings and runtimes,
  with individual start/stop controls.
- **Telegram integration:** Bot API or a separate account through
  MTProto/Telethon, subject to the transport's capabilities and the account's
  actual rights.
- **Provider configuration:** model and endpoint/account choices, with
  compatibility limits reported explicitly.
- **Source self-review:** code-only snapshots, read-only reviewers and separate
  records for findings and author decisions.

Available tools and integrations depend on the edition, configuration, platform
and granted authority. A submitted operation, a completed process and a verified
result are distinct facts.

## Platform status

| Product or edition | Status |
|---|---|
| Current Hélène Python distribution | Windows release 1.9.4 |
| New Python macOS/Linux port | Development work; not a released, real-machine-qualified desktop distribution |
| Older native desktop editions | Historical Windows, macOS Apple Silicon and Linux packaging; not the current 1.9.x assets |
| Praxis server runtime | Separate deployment sources and configuration; not installed merely by downloading this repository |

Older native editions have different packaging, computer-access integrations and
service/broker behavior. Their capabilities must not be assumed to exist in the
current Python application. In particular, the current Python product does not
include the older privileged broker.

For an older macOS or Linux edition, use that release's own assets and
instructions. Do not assume that an installer attached to an older release is
available under the latest Windows release.

## Verification and known limits

Each release describes its own checks and remaining gaps. The 1.9.4 record
includes focused source, packaged-runtime, installer, appearance and component
consumer checks; unchanged mechanisms retain their dated 1.9.3 evidence.

These records are not a claim of a complete legacy test run, all-provider
qualification, a physical phone or Windows sign-in test, a live installed-agent
migration, or native macOS/Linux acceptance. No full-panel PASS on the final
repaired bytes is claimed.

Checksums identify delivered bytes. They do not, by themselves, prove successful
installation or acceptance on a person's machine.

## Source and release provenance

| Path | Purpose |
|---|---|
| [`praxis/`](praxis) | Sanitized production-runtime mirror; no private identity or live memory |
| [`helene/`](helene) | Declared packaged-edition layer and frozen core |
| [`desk/`](desk) | Desktop, harness, installers, mobile interfaces and platform-build sources |
| [`remote/`](remote) | Remote-hosting components |
| [`CORE-SOURCE.json`](CORE-SOURCE.json) | Praxis source provenance |
| [`HELENE-SOURCE.json`](HELENE-SOURCE.json) | Packaged-edition source provenance |

The runtime mirror and packaged edition have separate revision records.
Published release archives, checksums and evidence files describe their own
delivered contents; later source synchronization does not replace those bytes.

For development, use a single Git checkout at the repository root. Keep changes
to the runtime mirror and packaged edition explicit. Preserve the repository's
source allowlist and ignore rules; do not force-add private runtime data,
credentials, sessions, dependencies or build outputs.

Useful references:

- [Build and release guide](desk/installer/RELEASE.md)
- [Python tools and Forge guide for 1.7.0](desk/docs/PYTHON-TOOLS-1.7.0.md)
- [Praxis deployment guide](praxis/DEPLOY.md)
- [Standalone Compose bundle](praxis/docker-compose.bundle.yml)
- [Production Compose recipe](praxis/docker-compose.deploy.yml)
- [Server installation sources](desk/server/)

Deployment credentials and machine-specific state belong outside Git.

## License

Repository code is licensed under Apache-2.0; see `LICENSE` and `NOTICE`.
Praxis Relay under `praxis/relay/` retains its MIT notice. Third-party
applications, dependencies and model weights retain their own terms, including
the separate XTTS CPML/non-commercial conditions.
