# Engineering work in Forge

Call tools using their current supplied schemas; check capabilities through `my_capabilities`.

1. Start `coding_session` with a goal, target and suitable isolation. `target="self"` means the application's own code; `isolation="auto"` selects an available path. Check the returned source root, worktree and mode; `direct` modifies the selected tree.
2. Read files, symbols, diffs, history and diagnostics through `coding_inspect`. Obtain versions from reads.
3. Edit through `edit`: supply `expected_sha256`, inspect each file's individual outcome and reread state. `partial` promises no rollback; `in_doubt` requires resolving the outcome before retrying.
4. Use `coding_run` or persistent `coding_process`; inspect result, exit and log. Foreground Python can call tools through `h` with generation and local process ownership checks. Supply explicit `task_id` for remote tasks.
5. Use `coding_agent`, `coding_swarm` and `coding_review` only when consistent with current instructions, authority and budget. Specify a result and inspect every participant's log and outcome; control through `coding_control`.
6. Run appropriate checks and inspect `coding_verify` and the diff. Finish after verifying the outcome and retaining evidence.

Unknown outcome, process custody and task result are separate states. Start recovery with status, history and receipts; never delete locks or invent terminal state manually.

Do not promise a root broker or systemd on every computer. Before an authorised restart, preserve state and recovery instructions; afterwards verify the new generation and a live response. Keep secrets out of work logs.
