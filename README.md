# Hélène

**Download, extract, open.** Python and the ready runtime are bundled; you do not
need Git, a compiler or a separate Python installation to start.

[**Download Hélène for your platform**](https://github.com/josephsteuerjr/praxis/releases/latest)

| Platform | Archive | Open after extracting |
| --- | --- | --- |
| Windows x64 | `Helene-…zip` | `helene.exe` |
| macOS on Apple silicon | `Helene-…-macos-arm64.zip` | `Helene.app` |
| Debian/Ubuntu x86_64 | `Helene-…-linux-x86_64.zip` | `Start Helene.sh` |
| Fedora x86_64 | `Helene-…-fedora-x86_64.zip` | `Start Helene.sh` |

The first window helps you create an agent or connect your existing data. A new
agent can start with the Hélène constitution or your own editable text or file.
Choose a permanent program folder, then configure your model provider. Local voice is
optional. Files named `SHA256SUMS` and `release-evidence` are verification
material; download the application archive for your system.

Hélène is a desktop home for agents with their own character, lasting memory,
settings and working files. Talk in the desktop window, connect Telegram, or use
a paired phone and web interface while the host is online. Powered by Praxis.

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
program code and agent data have separate ownership. With several selected code
roots, each resolution channel is admitted before the common application barrier.
Ready channels stay reachable for explicit peer reconciliation; every changed
composition needs its own current verification and independent review. Stop is
sent to all admitted coordinators, and the final shared application is checked
again after they settle.

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
capabilities follow the account's actual rights. Phone and browser access need
a configured connection to the host; downloading the desktop does not supply a
public server or tunnel.

Whisper and XTTS can run locally when their optional components are installed.
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
