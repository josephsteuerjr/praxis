# Pult — the window onto a core that lives somewhere else

The second application. Where [`desk/`](../desk) is the whole of Hélène — the window, the
channel, the runner and the installer around a core on this machine — Pult is only the
window: it opens onto a core running on a server, under a harness of its own, and it never
starts anything.

That is why it is its own application and not a checkbox inside the other one. A window
that both raises a local agent and watches a remote one has to ask, on every screen, which
of the two it is talking to; on 2026-09-09 that showed up in the plainest way possible —
the settings screen of the Pult scolding a foreign harness for the absence of things a
foreign harness is not supposed to have.

## Where the code is, and why it is there

**Split done on 2026-09-10.** The window itself — the channel client, state, chat, runs,
frame and eight views, plus the frame of the settings screen — lives in
[`desk/ui-kit/window`](../desk/ui-kit/window), beside the primitives the phone, the mini-app
and the installer already share. On top of it stand two applications, each bringing only
what it does not share:

| | |
|---|---|
| [`desk/app`](../desk/app) | **Hélène.** The agent lives on this machine, so the settings screen carries its cards: model and key, Telegram, the fence of the hands with the Windows service, mounts, computer control, the data folder. |
| [`desk/pult`](../desk/pult) | **Pult.** The agent lives on a server. One card says so; the rest of that screen is the window's own business — names, phone, the server address, updates. It also asks for that address on first run, because this variant has no installer by design. |

Not a single `remote` branch remains in the composition of the shared screen, and
`desk/app/test/settings-remote.test.mjs` guards the boundary rather than the branches: the
frame must not know the local cards, Pult must not import them, and whatever differs between
the two the frame asks for instead of deciding. The Pult's bundle is 158 kB against Hélène's
200 kB — the local agent's cards are not merely hidden there, they are absent.

**This folder holds no code, and that is deliberate.** Both applications are built from the
same repository: the same shell with a different product name and icon set
(`desk/shell/build-praxis.ps1`, `desk/installer/build_dist.py --variant praxis`), the same
channel, the same package. Pult is an application built from `desk/`, not a separate
codebase, and pretending otherwise would mean copying the channel and the shell to keep a
folder from looking empty.
