# praxis-desk — Praxis's server application

This is the exact source of the application Praxis runs beside her core on the server:
the `praxis-desk` backend (the channel the phone talks to) and the two phone front ends
built on top of it, the Telegram MiniApp and the installable PWA. It is a snapshot of what
is deployed in `/opt/praxisdesk`, not a Hélène package.

| Path | What it is |
|---|---|
| `backend/` | `deskapp.py`, `deskctl.py` and `deskd/` as installed in `/opt/praxisdesk`; the `test_*.py` files are its focused regressions |
| `desk/miniapp`, `desk/mobile` | the MiniApp and the PWA |
| `desk/ui-kit` | the shared phone interface both front ends build from |
| `recipe/` | the container recipe; the compose file adds the speech-state mount to the shared one |
| `test-entry.ts`, `browser_steps.test.mjs` | browser regressions for the step cards, turns and Forge views |
| `VALIDATION.md` | how each revision was built and checked |

## Why it is here and not in `desk/`

The repository's rule is that Praxis's windows are built from `desk/` rather than kept as a
second codebase (see [`../README.md`](../README.md)). Her server application started that
way, then moved on its own line in November 2026: voice and XTTS service controls
(`deskd/xtts_ui.py`, `ui-kit/praxis-voice.*`, `ui-kit/praxis-service.ts`), her phone
projection (`deskd/praxis_phone.py`), and the step cards with real outcomes, nested calls,
full turn history and Forge subagents. Hélène has since carried the phone steps into
`desk/` (2.6.0) and gone further there (the MiniApp as the whole programme), so the two
lines now differ in both directions. Folding this snapshot back into `desk/` is a separate
porting job; until then it is kept here byte for byte, so the code that actually serves
Praxis is not only on the server.

## Build and check

`npm ci` and `npm run build` separately in `desk/miniapp` and `desk/mobile`. The deployed
bundles then get the Fish voice extension applied to each output with
[`praxis/fish_miniapp_patch.py`](../../praxis/fish_miniapp_patch.py), preserving the PWA
manifest identity. Backend regressions run from `backend/` as separate processes:
`python -B -m unittest -q test_steps_outcome`, `test_steps_phone_receipts`,
`test_praxis_phone`, `test_praxis_controls`, `test_forge_snapshot`.

`SOURCE.json` records where this snapshot comes from and the digests that tie it to the
deployed files.

14 November2026: source44ff961 includes origin-message turn titles, separate operator-probe history and numeric model-generation progress. Both builds, backend44, browser12, argv10, six live HTTPS groups passed. Physical phone acceptance is separate. Old deployment assets were retained.

14 November2026 (later): source e52ce54 adds a code_query summary on step cards (operation, evidence rows, completeness and coverage, projected by the desk backend from the saved result), readable targets for frequent hands, and Telegram fullscreen on phones only. Backend50, browser13 (320/390/820), argv10, both builds; staged on80 live runs with0 errors. Only praxis-desk restarted; old assets retained.
