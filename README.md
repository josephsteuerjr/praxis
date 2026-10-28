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

The first window helps you create an agent or connect your existing data. Choose
a permanent program folder, then configure your model provider. Local voice is
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

First launch and **Update** open the same setup wizard. Create a new agent,
connect an existing data folder, or migrate an old installation. Choose the
program location and whether to remove the previous program after the new one
has confirmed startup.

Keep your code changes by combining them with the incoming release and letting
the new Python runtime's independent panel review the result. This also applies
to older native installations. Alternatively, choose clean upstream code and
keep the current data folder connected. Previous source and Git are archived.
Memory, conversations, settings and keys stay with the selected data folders.

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
