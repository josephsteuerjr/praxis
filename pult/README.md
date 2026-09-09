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

**The code still lives in `desk/`.** Pult is built today from the same shell with a
different product name, icon set and configuration (`desk/shell/build-praxis.ps1`,
`desk/installer/build_dist.py --variant praxis`), and the window carries the difference in
branches. Moving it here — its own front on the shared kit, the branches gone — is the next
step, and this file exists so the folder is not a promise made only in a chat.
