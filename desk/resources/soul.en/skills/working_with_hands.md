# Using Hélène's tools

Available schemas are supplied in tools; inspect capability state through `my_capabilities`. The current BASE defines home: `soul`, `memory`, `workspace`. Code and data may be separate. In sandbox mode, request external folders through `mount_request` with the needed access; interactive access remains bounded by user rights and current authority.

`fs_read` returns text and version; `fs_search` searches; `fs_ls` lists directories. Modify through `edit` using `replace`, `write`, `patch` or `batch`. Supply `expected_sha256`; replace every occurrence only with deliberate `all=true`. Inspect each outcome: `partial` does not mean rollback; `in_doubt` requires rereading state and logs before retrying.

`python(code=...)` accepts UTF-8 source through stdin with ready-to-use `h`. Example:

    text, version = h.read("workspace/example.py")
    h.edit("workspace/example.py", "old", "new", expected_sha256=version)
    result = h.hand("fs_search", pattern="TODO", root="workspace")

`h.read/edit/write/search` share access controls; `h.hand` calls the live parent dispatcher. A separate background script does not automatically inherit this channel. Inspect `.result` on `Refused`, `Partial` and `InDoubt`. Local Forge context is checked against the process; do not substitute another task's `task_id`.

`shell` executes a command; `python` executes code. Use Forge for sustained development. Check actual argv, cwd, exit and outcome. Request, tool reply, model-facing text and confirmed effect differ.

A timeout or Stop initiates termination of work owned by the call; check tree-exit confirmation separately. Normal exit may leave explicitly launched background work. Read large output through ResultRef. Save decisions and the next step before context compaction. Never print keys or sessions.
