# Praxis

An agent that lives in Telegram, on a desktop, and in files — and the runtime you build
your own with.

Most of what follows is the part people re-implement badly and abandon: a real Telegram
presence, a computer that can actually be driven, isolation that is honest about what it
covers, and a byte-exact record of every call to the model. It is written, it runs in
production, and the paths below say where each piece lives.

---

## Telegram, in full — both transports, one accounting

An agent in Telegram is usually a toy bot: no history, no rooms, no idea who is speaking,
one thread of text. This is not that. **Two transports** feed one set of rooms, contacts
and archives, and the agent does not know which one carried a message.

- **Bot API** — one token from BotFather and the agent lives in Telegram without a human
  account: long-poll `getUpdates` on its own thread, text and files out, reactions, bot
  name and description, its own rooms read from its own archives.
  → `desk/localharness/botapi.py`
- **Your own account (MTProto, Telethon)** — a separate number for the agent, the same
  interface behind an adapter: dialogs, history, joining, contacts, everything a person
  has. On the author's server this is the main lane.
  → `desk/localharness/mtproto.py`, `desk/localharness/mtproto_login.py`,
  `praxis/mtproto_runner.py`

What a bot **cannot** do — read a stranger's history, list dialogs, write first to someone
who never wrote, join by invite link — comes back as a refusal in words. The agent is never
handed an imitation of a capability it does not have.

What the core adds on top of either transport:

| what | where |
|---|---|
| Forum routing done right: exact root/topic dispatch and a stable conversation id per topic — a **place** is a room, a reply is an address, and the two are never confused | `praxis/telegram_topics.py`, `praxis/rooms.py` |
| Append-only group archive with topic and participant projections, search and orientation — the agent walks into a 10 000-message chat and knows where it is | `praxis/group_context.py` |
| Address book that never stores access hashes | `praxis/telegram_contacts.py` |
| Membership state machine for owner and agent, fsync-JSONL, survives a crash mid-join | `praxis/telegram_membership.py` |
| Durable outbox: stable MTProto `random_id`, immutable staged files — a restart in the middle of a send does not duplicate and does not lose | `praxis/telegram_outbox.py` |
| Ledger of "write to someone" requests and their answers | `praxis/telegram_followups.py` |
| Challenge confirmation for account-critical operations | `praxis/telegram_confirmation.py` |
| Owner inbox with typed outcome and dedupe | `praxis/owner_delivery.py` |
| Moderation hands, duplicate audit, room registry, route table | `praxis/telegram_moderation.py`, `telegram_duplicate_audit.py`, `telegram_registry.py`, `telegram_routes.py` |
| Voice in and out: STT (faster-whisper) and TTS (Silero, Piper) on the server | `praxis/stt_rpc.py`, `praxis/silero_tts_*.py` |
| A Telegram Mini App — the same interface as the phone, entered by `initData` signature | `desk/miniapp/`, `POST /pair/telegram` |

Who may start a turn is the owner's decision (`telegram.allow_from`: owner / listed / any).
Everyone else's messages still land in memory — they simply do not wake the agent.

Twenty tools, so the model can act rather than ask: `send_message`, `send_file`,
`send_media`, `react`, `read_chat`, `read_context`, `search_chats`,
`search_private_messages`, `group_context`, `inbox_list`, `inbox_read`, `admit`, `get_id`,
`manage_room`, `freeze_chat`, `freeze_contact`, `telegram_account` (join / leave / raw
dispatch behind a confirmation), `set_avatar`, `update_profile`, `restart_mailbot`.

---

## Files, code, projects

`fs_read`, `fs_write` (refuses to clobber or truncate), `fs_edit` (exactly one occurrence
or nothing), `fs_search`, `fs_ls`, `code_outline`, `code_map`, `run`, `run_tests`,
`pip_install` into a project venv, `shell`. Projects are real: `project_create` gives one
its own git and quota (`praxis/selfgit.py`, `praxis/workshop.py`).

**Durable coding** is a separate lane for work that outlives one turn: `coding_session`
binds a goal to a directory in an isolated worktree, `coding_process` supervises long
processes, `coding_agent` spawns independent subagents with fresh context, `coding_swarm`
coordinates them over a dependency graph, `coding_checkpoint` / `coding_verify` /
`coding_learn` close the loop. → `praxis/work_engine.py`, `praxis/forge_worker.py`,
`praxis/forge_swarm.py`

The agent also writes its own code: `start_proposal` / `submit_proposal` opens a branch and
a separate checkout, runs the full test suite in a sandbox and records the verdict; the
owner merges — or the agent does, if the owner allowed it. → `praxis/selfdev.py`

---

## Remote: the same window onto a core that lives elsewhere

The agent can live on this machine or on a server, and the window does not change. `remote`
mode points it at a channel over HTTPS (`desk/server/`, Docker, with the STT box);
`carry.py` exports the whole agent — memory, constitution, skills, its personal git, logins
— as one archive and imports it on the other side. The Pult application is the same window
onto a core running under a harness of its own. → `desk/localharness/carry.py`,
`desk/server/README-СЕРВЕР.md`, `pult/`

---

## Computer use: UIA, screen, input, processes

A native driver, not a screenshot loop. `praxis-body` speaks UI Automation through COM,
takes the screen, sends input, walks files and runs processes inside Job Objects, and
journals what it did; `praxis-bridge` is a WSS relay with a durable frame spool and
content-addressed artifacts. Protocol `praxis.body.v1`. → `praxis/body/crates/`

Three ways to run it, and the difference is who owns the desktop:

- **delegated from the sandbox** — the agent sits in an AppContainer while the driver lives
  outside it, so windows are reachable even under isolation;
- **interactive** — the owner's own rights, nothing more and nothing less;
- **Session 0** — a LocalSystem service (`desk/svc/`) with a rights broker: one named pipe,
  one token, a journal with before/after. For people who know what that sentence means.

Rights are four scopes (`computer.read`, `computer.files`, `computer.process`,
`computer.apps`), granted by the owner as checkboxes and re-read on every call. Typing goes
one `SendInput` at a time with a pause — recipients with asynchronous input (WinUI, TSF)
read `VK_PACKET` when they drain the queue, and a burst arrives as one last character.

---

## Isolation that is honest about its edges

**The mode is the fence around the tools**, and there are two of them
(`desk/localharness/modes.py`):

- **`sandbox`** — the agent's `shell` runs in a Windows container and file tools cannot
  leave the product folder; the owner's secrets are closed to it. No administrator rights
  needed. The fence covers `shell` and the file tools — and the documentation says so
  instead of implying more.
- **`interactive`** — no fence: everything runs with the owner's rights. Elevation is asked
  for one action at a time, through a UAC prompt.

The one door out of the sandbox is a folder: what the owner lists in `sandbox.mounts`
appears to the agent as `data/workspace/mnt/<name>`; the agent may **ask** with
`mount_request`, only a human opens it. Same shape for privileges: `broker_request` asks
for one command, the window shows it, the broker runs it only after a "yes".

The Windows service is an option **on top of** either mode, never a third mode: it starts
the harness in the owner's session, keeps the broker, and does not touch isolation either
way.

---

## About a hundred tools — and the agent sees all of them, every turn

The tool set is code-generated per channel (owner, the agent itself, trusted people with a
scope) — 99 on the author's install — and the window shows the same descriptions the model
reads. Speech and turn (`reply`, `say`, `stay_silent`, `narrate`, `end_turn`, `speak`),
memory and self-authorship (`recall`, `remember`, `journal`, `update_self`,
`manage_identity` — versioned revisions of SOUL / VOICE / CURRENT — `write_skill`,
`manage_desire`), self-tuning (`switch_brain`, `manage_perception`, `manage_appetite`,
`manage_autonomy`, `focus`, `rest`, `my_capabilities`), its own code, Telegram, files,
durable coding, runs, intentions and time, web, computer, host control.
→ `praxis/tool_offerings.py`

---

## The whole outgoing frame, captured

Every call to the model is recorded in full — not a summary, the frame itself.
`praxis/frame_trace.py` marks the zones — `persona`, `dynamic`, `evidence`, `situation`,
`messages`, `tools` — and attaches the metadata to an idempotent `model-input` receipt;
`mark()` returns the same object, so the frame is byte-for-byte identical with the
instrument and without it, and three different zeros stay distinguishable ("did not fit the
budget" ≠ "branch not taken" ≠ "empty"). `praxis/frame_layout.py` holds the shape: foreign
text enters the document only through a gutter (`> `, `>CR> `) and never stands in column
zero; the end of a section is counted by a meter, not guessed by a regexp.

Calls land in `memory/.state/llm_calls.jsonl` with role, model, tokens and cache; the
window shows what exactly went out and what came back from cache; shadow snapshots of the
frame are compared against each other. Every substantial turn is a durable run —
`praxis/run_manager.py`: manifest, append-only WAL, `ResultRef`, recovery, resume on a
strict plan with budgets.

---

## And the rest, briefly

- **Memory is files.** `soul/SOUL.md`, `VOICE.md`, `self/CURRENT.md` with versioned
  self-authorship and provenance; skills as markdown; a journal, dossiers of people,
  desires, notes. Append-only life memory with a hot layer and compaction
  (`praxis/memory_life.py`), trust classes for sources (`praxis/memory_provenance.py`), a
  rebuildable SQLite FTS (`praxis/memory_fts.py`). A portable git ships with the product,
  so every edit to the soul is committed and any of it can be rolled back.
- **A subscription for a brain.** [Praxis Relay](https://github.com/josephsteuerjr/praxis-relay)
  turns a ChatGPT Plus/Pro subscription into a local OpenAI-compatible API and ships inside
  Hélène as `helene-relay.exe`. No Codex system prompt on top of the agent's constitution;
  the model catalog comes from the backend; tool schemas are rewritten for the strict
  validator on the fly — proven on a turn carrying 98 tools.
- **Window, phone, mini-app.** A Tauri 2 window whose static files are read from disk (edit
  the interface without rebuilding the exe): chat with rooms, the turn panel with steps and
  tool receipts, context, the agent's files with editing, journal, system, settings. The
  phone is a PWA behind a QR code; the mini-app is the same application inside Telegram.
- **One archive to install.** Embedded Python with every dependency, portable git, the
  window, the service, the core, the computer driver and the relay. Updating in place
  merges settings, never touches `data/`, and replaces the interface only if the release
  changed it.

---

## Praxis, Hélène, and who to talk to

**Praxis** is the agent this runtime was written for and against: she lives on a server,
moderates a chat, writes her own code, keeps her own memory — and she is on Telegram as
[@praxis_intelligence](https://t.me/praxis_intelligence).

**Hélène** is the Windows edition: a runtime for building **your own** agent, not a
finished personality. It ships with a template constitution and twenty skills, and what the
agent becomes from there is between you and it.

Author: Yegor Kosyrev — Telegram [@tatarskiy_e4pochmak](https://t.me/tatarskiy_e4pochmak).

---

## The repository

| folder | what it is |
|---|---|
| [`praxis/`](praxis) | the core: durable runs, files-as-canon memory, Telegram, self-authorship |
| [`helene/`](helene) | the Windows edition of that core, declared as a layer — only the files that differ, with a table saying how far each one is and why |
| [`desk/`](desk) | the Hélène application: window, channel, local runner, shell, service, installer |
| [`pult/`](pult) | the Pult application: the same window onto a core that lives on a server |

Releases of Hélène are published here — one archive that unpacks into a folder. The relay
is a product of its own: [praxis-relay](https://github.com/josephsteuerjr/praxis-relay).

## Licence

Apache-2.0 for everything in this repository — `LICENSE` and `NOTICE` at the root. Praxis
Relay, carried under `praxis/relay/`, keeps its own MIT notice. Third-party components the
application ships are listed in `desk/installer`.
