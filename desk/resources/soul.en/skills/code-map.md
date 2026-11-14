# A map of my code

A map helps locate a module; current files and results establish facts.
`КАК-УСТРОЕН-HELENE.md` is useful distribution documentation, but may become
outdated. Layout depends on platform, migration and edition.

| Location | What to find | How to check |
|---|---|---|
| `data/` | memory, soul, work files, possibly a data Git repository | verify actual path, inventory and history |
| `tree/` | core: `agent.py`, `frame_*.py`, `memory_*.py`, `llm.py`, `forge.py`, `self_model.py` | read code; establish the task root before writing |
| `app/localharness/` | startup, birth, channels, modes, migration and updates | check implementation and directory permissions |
| `app/python_app.py`, `app/first_start_window.py` | Python window and first launch | source is separate from the built interface |
| `app/deskd/` | program HTTP channel and view models | check routes, authorization and actual responses |
| `app/static/`, `app/mobile/`, `app/miniapp/` | built interfaces | locate sources and build command; update relevant surfaces |
| `runtime/` | interpreter and dependencies | distinguish them from application source |
| `helene.json` | configuration, including secrets | use authorized settings; do not dump its contents into a shared channel |

Launcher, service, relay and optional components have their own sources
and processes. I do not describe the entire shell as Rust or the entire
product as Python. Windows MCP support is a separately installed module
under its own license; its schema in context does not prove a connected server.

## Locate the cause

1. Establish the version, path and process serving the failing scenario.
2. Open a current log and check its timestamp. An old log does not establish
   the current process state.
3. Locate the handler with `fs_search`, `coding_inspect` or `code_query`.
   Check callers, schema and search completeness, not just a matching name.
4. Compare source with the bytes actually built and running. Editing source
   does not automatically change an existing process.

Logs may be named `runner.log`, `helene.log`, `service.log` or `broker.log`;
I establish their location in this installation. `memory/runs/` holds
available turn records, not a promise of a full archive of every action.
Before sharing, I check secrets and the recipient.

Data Git, source Git, a Forge working copy and update snapshots are different
histories. I check which one actually contains the needed file. See
[my-own-code](my-own-code.md) for applying and returning, and
[fence-and-broker](fence-and-broker.md) for access and elevation.

The main trap is treating a familiar path as proven. I recheck it after
an update, migration or platform change.
