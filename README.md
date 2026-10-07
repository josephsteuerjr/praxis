# Praxis & Hélène

**A persistent AI agent, and a desktop home for agents you make your own.**

Praxis is the agent runtime: memory, tools, Telegram, durable work and self-authorship.
Hélène brings it to a desktop with a calm, paper-like interface, independent agents,
voice, files and a dedicated Doctor. An agent can run locally or on your server.

[Download Hélène](https://github.com/josephsteuerjr/praxis/releases/latest) ·
[Release history](https://github.com/josephsteuerjr/praxis/releases) ·
[Relay](https://github.com/josephsteuerjr/praxis-relay) ·
[Build and release guide](desk/installer/RELEASE.md)

## A home for several agents

- **Independent agents.** Each agent has its own constitution, memory, settings, files
  and runtime. Switch agents in the window, choose the default, or open a specific one.
- **A deliberate beginning.** Create an agent from the default constitution, inherit and
  edit an existing constitution, or write your own. Inspect the actual constitution file.
- **Individual controls.** Start and stop one agent. Keep another running. Changes to the
  agent list appear immediately, and files open from the selected agent's own tree.
- **Clear background work.** Setup and settings explain that active agents can keep
  receiving messages and doing tasks after the window closes. A paper tray card shows
  the selected agent, engine status, agent switching and explicit open, stop and exit actions.
- **Recoverable removal.** Removal requires the agent ID and moves its files into an
  archive instead of silently erasing its memory.
- **A Doctor.** Create a dedicated diagnostic agent from the Agents card. It measures
  the installation, distinguishes observations from diagnoses, proposes treatment,
  verifies the result and reports it. Its constitution keeps it separate from patients'
  conversations and requires owner approval for treatment beyond quiet reversible checks.

The Doctor is a product feature, not an invisible repair service. Its knowledge and
character can develop through the same authored mechanisms as other agents.

## Conversations, images, files and voice

- **Paper-like chat.** Readable typography, resizable panels, remembered drafts and reading
  positions, visible activity, and controls that reflect the actual engine state.
- **Tasks and wake-ups.** Inspect ongoing work, schedule future wake-ups and return to
  continuing intentions from dedicated views.
- **Context and diagnostics.** Inspect retained context, files, activity and journals
  when understanding or diagnosing an agent's work.
- **Materials and storage.** View material sizes as a diagram and explicitly confirm
  removal of eligible project or cache folders.
- **Images.** Generate and edit images through the ChatGPT subscription relay from a
  Codex or GLM conversation. Images have independent settings and reference-image support.
  View the whole image inline, open the internal viewer, fit or zoom, then return to chat.
- **File handling.** Attach ordinary documents as well as pictures. Choose folders,
  search and select several files, or use paste and drag-and-drop. Save with an explicit
  name and destination, a replacement decision and a completion receipt. Remote-agent
  attachments are saved to the person's computer.
- **Voice.** Record in chat, including the Alt+Enter shortcut. Local speech engines use
  Whisper for recognition and Piper for synthesis; the server supports its STT/TTS lane.
  Engine updates preserve downloaded models where compatible.
- **Phone and web.** Use the paired mobile interface or Telegram Mini App with the same
  conversation and settings. Configure an external HTTPS address for access away from home.

An image relay can remain available while a different provider supplies the main voice.
Empty image credentials are handled correctly, and updates preserve the auxiliary relay.

## Telegram as a real place

Use a Bot API token or a separate Telegram account through MTProto/Telethon. The transports
have different capabilities; unsupported operations return an explicit explanation.

The runtime includes:

- Private and group conversations, exact forum-topic routing, reply targets and room modes.
- Messages from people are archived before wake-up and admission checks. The agent wakes
  for its current name, authenticated profile name, exact @username or a reply to its message.
- Offline startup and later connection loss recover automatically without reopening the
  window. Telegram reception health reflects the actual connection, including reconnects.
- Topic-aware archives, history retrieval, conversation search and participant orientation.
- Real message IDs reconcile replayed posts and edits. MTProto catches up missed history,
  includes the account's messages sent on another device, and applies received deletions.
  Bot API catches up its retained update queue within Telegram's history and privacy limits.
- An optional server gateway carries Bot API HTTPS and MTProto WSS with a private access
  key and verified TLS. Configure the route in the Telegram settings card.
- Contacts, membership, profile and moderation tools within the account's actual rights.
- Durable outgoing messages and staged files, idempotency keys and delivery receipts.
- Reactions, media, follow-ups and owner-controlled admission.
- Reusable Telegram skills for raw account operations, search, history archaeology,
  reconnect delivery, thread hygiene and moderation.

Under the explicit reply-hand contract, an agent speaks by calling `reply`. Ordinary
model text is its private note, and a completed empty delivery plan does not prove that
a message was sent.

## Memory, continuity and self-authorship

Markdown and JSONL are the inspectable canon. SQLite/FTS accelerates lookup rather than
becoming the source of the agent's behaviour.

- Recall, facts, room memory, journals, personal notes, projects and ongoing intentions.
- Compaction and retained history with provenance, refresh and recovery mechanisms.
- Authored skills and identity revisions rather than installation-time overwrites of a
  living agent's constitution.
- Independent model/provider settings and recorded context about the current runtime.
- Durable runs with model calls, tool results, checkpoints, artifacts and resume state.
- Recorded frames and integrity-checked result references for diagnosing what a run saw.

The current Praxis mirror also includes confidence-aware memory hooks, open questions,
Forge task contracts and the literal durable-frame reader. Frame records may be scrubbed
for secrets: they certify the retained representation, not byte identity with provider traffic.

## Engineering work that survives a chat turn

Forge gives work an explicit goal, source directory and durable record. It supports
isolated worktrees, supervised processes, independent coding workers, dependency-aware
swarms, checkpoints, verification and lessons. The current Praxis task contract can record
success criteria and verification commands and reuse matching verification evidence.

The ordinary tool set includes file search, exact edits, patches, code outlines, shell
commands, tests, web access and computer tools. Tool descriptions and compact pointers
are generated for the current channel and authority; an agent can inspect the full schema.

Self-development uses proposals and reviewable changes with checks and rollback records.
The owner controls whether proposals can merge automatically.

## Local, remote and computer access

Hélène can host an agent locally or connect to a server. Transfer tools carry memory,
skills, authored code changes and continuity between installations. Owner-authorized
relay-login transfer supports a connected server without putting credentials in chat.

Computer access uses native bodies and explicit scopes for reading, files, processes and
applications. Windows provides UI Automation, screen capture, input, a service and a
Session 0 broker. Linux and macOS have their own desktop integrations and platform permissions.

Execution mode and service mode are separate choices. Interactive execution uses the
owner's rights; sandbox and broker support depend on the platform. The installation
shows the capabilities and limits of the selected mode rather than promising a universal fence.

## Updates that preserve a living installation

- A separate update path with preparation, progress, cancellation and a clear result.
- Existing data/code paths and constitutions are retained.
- Agent-authored code changes are carried by three-way reconciliation; unresolved
  changes remain available for inspection.
- Installed-code receipts are captured after reconciliation, so a legitimate carried
  change does not trigger an automatic rollback by itself.
- An open acceptance trial is reported explicitly before another update can begin.
- Terminal turn cancellation releases the active turn while a model request is still
  waiting and discards its late response. Network failures can retry the same channel
  with the same conversation frame.
- Windows has trial/rollback controls. Backup and agent-transfer paths preserve the
  relevant authored data and avoid duplicating downloaded dependency caches.

## Platforms and downloads

| Platform | Delivery |
|---|---|
| Windows | Installer, portable ZIP, optional voice-engine archive, native desktop and service |
| Linux | DEB and RPM packages, Electron desktop, service and rights-broker integration |
| macOS, Apple Silicon | Native archive and installer script built by GitHub CI from the matching Windows release |
| Server / phone | Server channel, paired mobile interface and Telegram Mini App |

On macOS, install or update from Terminal as the user signed into the desktop:
no `sudo`, no `su`. The installer opens the setup window in that person's session.

```sh
curl -fsSL https://github.com/josephsteuerjr/praxis/releases/latest/download/install.sh | sh
```

For an archive already downloaded, add `-s -- --from /path/to/Helene-1.4.3-macos-arm64.zip`
after `sh`. The script extracts the archive and opens the setup window.

Linux Electron cannot infer a stationary touchpad contact from wheel events; its scrolling
settings expose that limitation. macOS screen and accessibility operations depend on TCC
permissions. Package/build checks and real-machine interaction are separate evidence.

## Source and release provenance

| Path | Purpose |
|---|---|
| [`praxis/`](praxis) | Sanitized current production runtime; no private identity or live memory |
| [`helene/`](helene) | Declared edition layer for Hélène's frozen package core |
| [`desk/`](desk) | Desktop, harness, Doctor, installers, mobile interfaces and platform builds |
| [`remote/`](remote) | Remote-hosting components |
| [`CORE-SOURCE.json`](CORE-SOURCE.json) | Production Praxis source provenance |
| [`HELENE-SOURCE.json`](HELENE-SOURCE.json) | Frozen Hélène core, layer and package/CI source provenance |

The production mirror and the packaged edition have separate revision records. Published
assets include SHA-256 checksums. The macOS workflow checks that its source Windows archive
exists under the same version before compiling, then validates its contents and passport.

## Licence

Apache-2.0 for everything in this repository — `LICENSE` and `NOTICE` at the root. Praxis
Relay, carried under `praxis/relay/`, keeps its own MIT notice. Third-party components the
application ships are listed in `desk/installer`.
