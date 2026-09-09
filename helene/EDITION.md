# Hélène edition of the core — what differs, and why it has to

Hélène is the Windows edition of the agent whose core lives in [`praxis/`](../praxis).
The two are not one tree and never will be: a core that lives in a container on a Linux
server and a core that lives inside a portable folder on someone's PC differ by the nature
of the machine under them. Pretending otherwise produced a phantom task — "merge the cores"
— that could never be finished.

So the difference is declared instead. `core/` holds every file of the agent's core that
Hélène carries differently, and nothing else: everything not listed here comes from
`praxis/` unchanged. On 2026-09-09 that was **18 files out of 513** — 435 of the rest are
byte-for-byte identical, and the remaining 60 are the agent's own writing (`soul/`, her
constitution and skills), which belongs to her and is hers to differ.

## What is here, and how far it is from the core

| file | lines in the core | added here | removed here | why |
|---|---:|---:|---:|---|
| `agent.py` | 16949 | 165 | 109 | vision routing before the call; the model-call ledger carries chat, principal and Forge task |
| `llm.py` | 2025 | 214 | 41 | vision model of the same framework; the reasoning-effort step for GLM (`output_config.effort`); fallback trace |
| `frame_stats.py` | 250 | 189 | 223 | the cache cuts the window's System screen reads (seven axes) |
| `mtproto_runner.py` | 9482 | 92 | 91 | Telegram from a Windows runner rather than a service |
| `body/crates/praxis-body/src/desktop.rs` | 2459 | 84 | 3 | the `computer` hand on Windows: input by keystroke, not one packet burst |
| `body/crates/praxis-body/src/uia.rs` | 1826 | 34 | 3 | UI Automation: reading a window's live value when the cached pattern is empty |
| `turns.py` | 1017 | 39 | 28 | the turn tail the window's card opens in full |
| `run_manager.py` | 2611 | 21 | 1 | `os.replace` retries on Windows sharing violations |
| `perception.py` | 475 | 8 | 47 | perception without the server's transports |
| `promises.py` | 243 | 9 | 16 | the promise regexp reads a feminine past tense too |
| `forge.py` | 3504 | 12 | 2 | Forge without the server's workers |
| `unanswered.py` | 130 | 2 | 18 | the same, minus the server's mailbox |
| `panel.py` | 1861 | 3 | 12 | the panel the desk application replaces |
| `canary.py` | 339 | 3 | 8 | the frame canary on a single-machine layout |
| `frame_shadow.py` | 2404 | 4 | 0 | shadow snapshots beside a portable data folder |
| `webtool.py` | 591 | 1 | 2 | the web hand without the server's proxy |
| `moderation_shadow.py` | 149 | 1 | 1 | one path |
| `ARCHITECTURE.md` | 389 | 0 | 38 | the sections that describe the server this edition does not have |

Files that exist only here:

| file | why |
|---|---|
| `sitecustomize.py` | the sandbox root the Windows runner sets before the first import |
| `test_atomic_replace_retry.py` | proves the sharing-violation retry above |
| `test_compact_refresh.py` | proves the compaction the window shows |
| `test_vision_switch.py` | proves the vision routing above |

## The honest part

Some of what is here is not an edition at all — it is a patch waiting for its author. The
vision routing, the effort step, the call ledger and the window-reading hand were written
for Hélène and offered to the core; until they are taken there, they live here, and this
table is the only place that says so out loud.

The numbers above were measured on 2026-09-09 against the core as it runs in production.
They drift the moment either side moves, and a drifting number that nobody re-measures is
the same lie the phantom "one core" was. Re-measure before trusting them.
