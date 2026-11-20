# Hélène 2.8.13 Beta

- **Recovery finishes the job.** When the last unresolved unit of a task is custody-recovered, the task itself is closed as stopped. After a witnessed Windows reboot (or a Linux host reboot) the queue no longer shows an ownerless active task forever. Nothing is fabricated: the historical outcome stays lost or unknown exactly as before, and the unsafe checkpoint still must not be continued.
- **Answers you can follow.** `prepare_recovery` / `recover` answers are written in the agent's own language (ru/en/fr/zh) and always state the exact next step: restart this computer yourself, which units are still unresolved and how to close them, or that the queue is clean and a new stage is a new task on the same worktree.
- **Reverse cases.** A task left active by an earlier version (recovery settled, task still active, resume refused because it was never paused) closes with one `recover` call without naming an agent — or with a group Stop, whose receipt now explains what an honest continuation looks like.

Only the agent core changed; the desktop UI and the subscription relay are the same as in 2.8.12.
