# Hélène

**Download, extract, open.** Python and the ready runtime are bundled; you do not
need Git, a compiler or a separate Python installation to start.

[**Download Hélène for your platform**](https://github.com/josephsteuerjr/praxis/releases/latest)

| Platform | Archive | Open after extracting |
| --- | --- | --- |
| Windows x64 | `Helene-…zip` | `helene.exe` |
| macOS on Apple silicon | `Helene-…-macos-arm64.zip` | `Helene.app` |
| macOS on Intel | `Helene-…-macos-x86_64.zip` | `Helene.app` |
| Debian/Ubuntu x86_64 | `Helene-…-linux-x86_64.zip` | `./helene` |
| Fedora x86_64 | `Helene-…-fedora-x86_64.zip` | `./helene` |

The first window helps you create an agent or connect your existing data. A new
agent can start with the Hélène constitution or your own editable text or file.
Choose a permanent program folder, then configure your model provider. Local voice is
optional. Files named `SHA256SUMS` and `release-evidence` are verification
material; download the application archive for your system.

Hélène is a desktop home for agents with their own character, lasting memory,
settings and working files. Talk in the desktop window, connect Telegram, or use
a paired phone and web interface while the host is online. Powered by Praxis.

## Included in 2.7.2 Beta

Fixes ChatGPT subscription sign-in on the desktop: browser and device-code
logins no longer stop right after they start. See the
[patch and limits](desk/docs/RELEASE-2.7.2.md).

## Included in 2.7.1 Beta

All tool schemas are offered natively. The Context screen adds searchable chats,
local timestamps, snapshot navigation and inspection of recorded tool schemas.
ChatGPT sign-in uses a device code with bundled praxis-relay 1.2.0, including
headless servers. Confirm the code on your phone or computer.
See the [patch and limits](desk/docs/RELEASE-2.7.1.md).

## Included in 2.7.0 Beta

Full server transfer and return, a built-in server bridge, the whole programme in
Telegram, agent reflection and shared owner context. See the
[changes and limits](desk/docs/RELEASE-2.7.0.md).

## Included in 2.6.1 Beta

Windows installation and updates no longer request administrator rights. Protected
old files are retained and reported. See the [correction and limits](desk/docs/RELEASE-2.6.1.md).

## Included in 2.6.0 Beta

Updated hands and action panels, complete phone history, in-app server installation
and updates, a Linux ARM64 server package, non-admin cleanup, and macOS launch recovery.
Read the [beta release notes and limits](desk/docs/RELEASE-2.6.0.md).

## Included in 2.5.3

Lower idle CPU use through corrected inbox wakeups, lighter local session
observation, and adaptive Forge polling. Global hotkeys, voice and task controls
are retained. Read the [2.5.3 hotfix details](desk/docs/RELEASE-2.5.3.md).

## Included in 2.5.2

Scrolling and rubber-band return use the full elapsed frame time. The spring
settings are preserved. Turn details open at their measured content height;
fast reversals and delayed content continue from the visible position.
Reduced-motion preferences remain respected. A separate Intel Mac package includes its own Python runtime;
local Whisper and XTTS are unavailable in that package.

Read the [2.5.2 changes](desk/docs/RELEASE-2.5.2.md).

## Included in 2.5.1

Opening a prepared clean-update folder now resumes the graceful handoff
from the selected previous installation before starting the new application.
Pending data moves complete automatically during normal startup.

Windows updates now handle system shortcuts that have no filesystem target.
Such links stay unowned, allowing retirement of the old program folder to
continue. Actual failed shortcut operations still stop the update with an error.

Read the [2.5.1 correction](desk/docs/RELEASE-2.5.1.md).

## Included in 2.5.0

- Application-owned asynchronous Fish and XTTS voice delivery after one speak call.
- Recording submission on hotkey release, compact playback and thirty-minute warm Whisper residency.
- Codex Fast mode with model-specific speed and reasoning controls across desktop and Mini App.
- Clean extraction and restart updates with retained agent data and shared model storage.
- Native Windows handover and truthful successful-response status improvements.
- Forge command admission, owned-job Stop and Pause/finish custody repairs.

Read the [2.5.0 changes and evidence boundaries](desk/docs/RELEASE-2.5.0.md).

## Included in 2.4.0

- Independent Fish speech for local agents, alongside XTTS, with private keys,
  explicit voice/model selection and durable paid-preview recovery.
- Persistent optional voice components and model files, shared across program
  versions; personal samples stay with the agent and survive migration.
- Fullscreen Telegram Mini App access through your own bot, with the current
  tunnel address refreshed automatically. The temporary-address PWA flow is retired.
- Restored desktop-to-VPS transfer with SSH host verification, resumable import,
  persistent Stop and guarded recovery.
- Two explicit source-update cards, verified private configuration writes and
  aligned light/dark forms at desktop and phone widths.

Read the [2.4.0 changes and limits](desk/docs/RELEASE-2.4.0.md).

## The 2.3 desktop foundation

The stable release collects the 2.3 candidate work across the desktop, the agent
runtime and the update system:

- Praxis-style pages and a detailed right panel for actual model inputs, tool
  arguments, execution receipts, worker activity and results.
- Forge tasks with nested workers, observed model identity, partial outputs,
  history and explicit control/acceptance states.
- Durable host-owned updates that continue after a window closes, with source
  preservation, independent review, explicit clean replacement and rollback.
- Shared-application/multiple-core composition, scoped verification and isolated
  execution, with truthful cancellation and process custody.
- First-launch/migration flows that preserve existing profiles; a new agent can
  start with the Hélène constitution or custom text/file before provider login.
- Local Whisper/XTTS and remote Fish controls with free-form delivery, explicit
  voice/model selection and exact paid-preview recovery after an interrupted
  request or page reload. Remote provider credentials stay on the server.
- Portable Windows, Apple-silicon, Debian 12 and Fedora 44 archives, with inspected
  native Cocoa and Qt/X11 windows and explicit package/source evidence.

Read the [complete 2.3.0 feature and validation notes](desk/docs/RELEASE-2.3.0.md),
or [download 2.3.0](https://github.com/josephsteuerjr/praxis/releases/tag/v2.3.0).

## Your agent, your files

Memory is kept in inspectable Markdown and JSONL, with SQLite search indexes.
Conversations, projects, skills and retained runs stay with the agent's data
folder. Pause, Continue and emergency Stop give you direct control over its
work. Private memory and credentials are separate from the public source code.

Agents can search and edit files, use a shell and work with configured computer
tools. Forge coordinates engineering tasks, worker results and independent
review. The interface exposes the model's request, the observed execution and
the result returned to the model.

## Install, connect, update

Normal **Update** starts a durable, in-place job owned by the Python host,
not the setup window. Closing an observing window does not cancel that job;
Pause, Continue and Stop remain explicit controls. Reopening the interface
attaches to the same operation. The selected data folders stay connected.

The short setup/migration entry is separate: create a new agent, connect an
existing data folder, or migrate an old installation. It hands the work to the
host; the mechanical switch confirms the new program's startup or rolls back.
See [Update and migration ownership](desk/docs/UPDATE-SESSION-HOST-230.md).

Updates offer preserved source with conflict resolution, checks and independent
review, or clean release code. Memory, conversations, settings and keys stay
with the selected data folders. Legacy migration follows its explicit preview;
program code and agent data have separate ownership. New preserve updates bind
all selected core variants and one shared application into one candidate. One
coordinator/hand reconciles it, then one separate test launcher produces the
whole-composition report for independent review. Every affected profile's Stop
and Pause remain binding. Changed candidates need fresh matching evidence;
programme application/rollback does not move or mandatorily archive agent data.
Earlier admitted per-core operations retain their original protocol and receipts.

The incoming package owns the coordinator and verification policy, before the
installed program is replaced. Experimental preserve protocol v2 does not emulate
older RC verification bundles: an incompatible bundle refuses before application,
never silently changes Preserve into Clean. Clean replacement remains an explicit
choice that replaces authored program changes, not the separate agent data.

Verification binds the whole final composition and all upstream, integration and
cutover deltas. The normal tier runs a published contract floor, changed suites,
direct importing consumers and author-requested widening; unknown impact, verifier,
cutover, dependency and non-Python changes use the full recipe. Selected PASS is
not full PASS. Omitted lanes and core modules are listed in the review evidence.
`update.source` finish/verify accepts `full_tests`, `extra_tests`, `test_reason` to
widen; the executor CLI also offers `--full-tests`, `--extra-test`, `--test-reason`.

Windows uses one attempt-local ACL-protected Python/dependency copy, with private
source, home, build and cache directories per execution slot. Native children
cannot ordinarily write, create, replace or delete shared inputs; this is not a
hostile same-account-code sandbox. Other platforms retain private copies and
per-command dependency checks. Expensive preparation is limited separately from
execution. Flat core modules are partitioned before imports; nested discovery
retains its complete traversal. No mutable hardlinks or earlier test verdicts are
shared. Settled completion, Stop and report-publication failures clean disposable
stands; logs/receipts remain outside Temp. Unknown process custody retains its
stand. Hard crashes can leave a recorded stand for explicit reconciliation, not
an automatic age-based delete.

During an update the window can close for several minutes. Hélène opens it
again automatically; do not launch another copy while the update is running.

Desktop archives run as your logged-in user: no `su` or root desktop session
is needed.

**Restore upstream code**, beside Update, is the emergency route back to the
installed release's clean source while keeping current data. The reusable
`app/helene_setup.py` entry opens setup and also supports recovery.

## Models, Telegram and voice

Bring your own model access and credentials. Provider quotas and charges remain
with the provider. The standalone [Praxis relay](https://github.com/josephsteuerjr/praxis-relay)
is distributed separately.

Telegram supports a bot connection or a separate MTProto account. Their
capabilities follow the account's actual rights. The Mini App opens through the
bot at the host's current tunnel address; the host must remain online, or the
agent can be transferred to a supported VPS.

Whisper and XTTS can run locally when their optional components are installed.
Fish is an alternative synthesis provider using your own Fish API access.
CPU speech synthesis can take time. XTTS weights use the Coqui Public Model
License, including its non-commercial conditions. Consult the bundled licenses
and third-party notices.

## Source and contribution

This repository contains the Hélène desktop and the Praxis agent runtime.
Changes should describe the behavior they improve, the path actually exercised
and any remaining limits. Source tests, packaged installation and acceptance
on a person's machine are separate pieces of evidence.

Created by Yegor Kosyrev. Original and third-party attribution is retained in
[LICENSE](LICENSE) and [NOTICE](NOTICE).

Thanks to [Ouroboros](https://github.com/razzant/ouroboros) by
[Anton Razzhigaev](https://t.me/abstractDL) & Andrew Kaznacheev for the
open-source ideas that informed Praxis's exact-argv `run_command` with declared
outputs and scratch paths, and the design of its code navigation.
