# Hélène

A desktop home for agents that remember, act and develop their own tools.

Each agent has its own character, memory, settings and working files. Talk in
the desktop window, connect Telegram, or use a paired phone and web interface
while the host is online. Choose your model provider or a supported subscription
relay. Local voice is optional.

**Downloads are temporarily withdrawn while the installation and update path is
being repaired.** Existing conversations and agent data are not being removed.
Availability will be announced on the [releases page](https://github.com/josephsteuerjr/praxis/releases).

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

Windows setup is being consolidated into one wizard, reached from the first
launch of `helene.exe` and from **Update**. It offers a new agent or an existing
data folder, a choice of program location and optional removal of the previous
program. Old code is retained as material for future self-modification.

Data and executable code are handled separately. A clean-code update keeps
memory, conversations and settings. Preserved-code updates combine authored
changes with incoming source; the verified incoming Python application owns
the independent review and the final installation transaction.

The previous application must stop before the switch. Service operations use
Windows' own administrator prompt. Old program files and shortcuts are removed
only after the new application confirms startup; data directories are retained.
The new installation creates one desktop shortcut.

The source also contains application packaging for macOS, Debian and Fedora.
Platform-specific setup and qualification are documented under [desk](desk/).

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
