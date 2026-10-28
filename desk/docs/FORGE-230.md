# Forge observations and ordinary review

## One journal, two views

`deskd/forge_reader.py` reads Forge's own task/unit directories; there is no UI task store. The compact read-only viewer is **above Turns** in the right column. Plans uses the same controller with existing durable intent/receipt controls. Changing conversation keeps the selected agent's team tree. Leaving a view aborts observation, never the work.

The owner-only `GET /api/forge/tasks?view=active&limit=20&cursor=…` returns `{active,recent,next_cursor}`. `active` contains every unfinished participant tree, including an old worker and a child whose parent task/result ended. It is not cropped by the recent page. Unknown, queued, paused, waiting and Stop-pending remain nonterminal. Recently completed teams are keyset-paged by observed completion/identity; the cursor never authorizes an effect. The legacy no-view list remains for existing consumers.

The runtime owner writes dated `liveness.json` observations bound to `task_id`, `unit_id`, exact supervisor PID and captured process birth. `forge_liveness` uses the installed psutil OS adapter; absent adapter, denied process read, legacy missing birth, or damaged identity means **unknown**, not dead. `alive` says the exact process was observed alive at the recorded time, not that it remains alive after disconnect. These presentation records never authorize Stop, replacement or custody transfer. The existing runtime maintenance tick observes even while admission is paused. A result is not process-exit evidence.

The viewer retains keyed nodes, selected content and focusable references when a poll changes only observations. Complete arguments/results use existing hash-addressed endpoints; Unicode offsets count code points. A cursor/generation/hash mismatch is an error, not a partial success. A network failure keeps the last tree; changing owner/agent scope aborts reads and discards the former audience's DOM. Details are owner-only over HTTP and the tunnel. Paired/public clients do not receive private text or locators; the older sanitized `/api/forge` projection remains separate.

## Ordinary review is a capability, not a completion permission

Ordinary self-development can finish without a reviewer, skip or BLOCKED override. Critics' actual findings are retained, never rewritten into PASS. `self_review` extends the existing frozen-subject panel to plans, changes and task results; supplied effect descriptions are authored claims, not independently observed effect receipts. Explicit configured automatic self-edit review remains supported; the default is opt-in. Seed guidance is delivered through the existing kit proposal flow, not a silent overwrite of authored Markdown.

Modified-preserve source updates have their own full-composition test/review gate. An ordinary review/disposition cannot open that gate. See the source-update contract for required coverage and final-byte binding.

The obsolete incompatible-extension update bypass is removed from Settings rather than hidden. Python Update submits one `update_start` job before network/extraction/model work, requires durable admission with a stable job ID and an observer-close receipt, and then reads progress. It never falls back to the old window-owned download/install path after an unconfirmed reply. Legacy non-Python installers retain their separate normal path.

## Focused consumers

From the repository root, using a dependency-complete Python:

```text
python desk/tests/t_forge_activity_230.py
python desk/tests/t_forge_owner_220.py
python desk/tests/t_forge_tree_ui.py
python desk/tests/t_forge_runner_220.py
node desk/app/test/forge-panel-220.test.mjs
node desk/app/test/update-admission-230.test.mjs
node desk/app/test/self-review-192.test.mjs
```

The visual fixture uses the real production `panel.ts`, shared Forge controller, production CSS/fonts, and actual journal/reader against a **synthetic disposable profile**, not a live agent:

```text
node desk/app/test/fixtures/forge-230-build.mjs path/to/existing/node_modules
python desk/app/test/fixtures/forge-230-serve.py 5687
```

Open `http://127.0.0.1:5687/`. It includes a terminal parent, unfinished worker/grandchild, 35 terminal teams, long Unicode output, narrow-column/theme/room/disconnect/dispose controls. Generated browser assets are rebuildable test evidence, not product distribution bytes. Source fixtures do not qualify installed Windows/macOS/Linux or a physical phone.
