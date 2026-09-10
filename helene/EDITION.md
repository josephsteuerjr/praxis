# Hélène edition of the core — what differs, and why it has to

Hélène is the Windows edition of the agent whose core lives in [`praxis/`](../praxis).
The two are not one tree and never will be: a core that lives in a container on a Linux
server and a core that lives inside a portable folder on someone's PC differ by the nature
of the machine under them. Pretending otherwise produced a phantom task — "merge the cores"
— that could never be finished.

So the difference is declared instead. `core/` holds every file of the agent's core that
Hélène carries differently, and nothing else: everything not listed here comes from
`praxis/` unchanged.

**The declaration is now checkable, and it was checked.** `desk/installer/core_src.py
--check` compares the layer against the ACTUAL difference between `praxis/` and the working
copy, and names three outcomes: undeclared, declared in vain, ours alone. Run it before
trusting any number on this page.

⚠ What it said on 2026-09-10, right after the core was re-exported from production
(`64f1588c`): 19 files declared here really do differ, **0 are declared in vain** — and 40
differ without being declared. Those forty are not a hidden edition. Three are the
element-acting verb written that same morning and not yet offered; the rest are this
working copy lagging a core that moved the day it was published. The layer is regenerated
after the working copy catches up, not before: copying "we are behind" into a file called
"the Windows edition" is exactly the lie the previous measurement made.

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

Some of what was here was not an edition at all — it was a patch waiting for its author.
The vision routing, the effort step for GLM, the call ledger and the window-reading hand
were written for Hélène and offered to the core; until taken there, they lived here, and
this table was the only place that said so out loud.

**They were taken.** The core's own commit of 2026-09-10 — "GLM effort wire dialect and
durable Forge call attribution" — carries the effort step (`output_config.effort` in
`llm.py`, the z.ai Anthropic dialect) and the call ledger (`forge_task_id` in `llm.py` and
`forge_worker.py`); the vision routing and `read_window` are in there too. All four are now
the core's, not an edition of it, and the rows below shrink accordingly at the next
regeneration.

One patch is still offered and not yet taken: `desktop.element.act` — acting on a named
element through UI Automation patterns instead of a point on screen. It is not in `core/`
either, because it was written on 2026-09-10 and its letter went out the same day.

The numbers on this page drift the moment either side moves, and a drifting number that
nobody re-measures is the same lie the phantom "one core" was. There is now an instrument;
use it.
