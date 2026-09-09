# Praxis

A long-running agent that lives in files — and the applications built around it.

One repository, four folders. There is no separate "release" repository any more: the
releases of Hélène are published here, next to the core they carry.

| folder | what it is |
|---|---|
| [`praxis/`](praxis) | **the core.** The agent itself: durable runs that resume after a crash, markdown and JSONL canon instead of a database, Telegram, self-authorship. This is the tree that runs in production on the author's server; the mirror is exported from it. |
| [`helene/`](helene) | **the Windows edition of that core** — declared as a layer, not as a second copy: only the files Hélène carries differently, with a table saying how far each one is from the core and why. On 2026-09-09: 18 files out of 513. |
| [`desk/`](desk) | **the Hélène application.** The window, the channel between window, phone and mini-app, the local runner, the shell, the service, the installer — everything around the core on a Windows machine. |
| [`pult/`](pult) | **the Pult application** — the same window, but onto a core that lives on a server under a harness of its own. |

The subscription relay that gives the agent a frontier-model brain at a flat price is a
product of its own: [praxis-relay](https://github.com/josephsteuerjr/praxis-relay).

## Releases

Hélène is distributed as one archive that unpacks into a folder: an embedded Python, a
portable git, the window, the channel, the runner, the relay and the core. See
[Releases](https://github.com/josephsteuerjr/praxis/releases); the update check inside the
program points here.

## Licence

Apache-2.0 for everything in this repository — the core, its Windows edition and both
applications: `LICENSE` and `NOTICE` at the root. Praxis Relay, carried under
`praxis/relay/`, stays MIT under its own notice, and is maintained in
[its own repository](https://github.com/josephsteuerjr/praxis-relay). The third-party
components the application ships are listed in `desk/installer`.
