# Forge worker liveness and custody

An empty `supervisor.log`, old modification time or long model response
does not prove a hang. A fresh log, high CPU or connection on 443 also
does not prove useful progress or a safe stopping point.

Poll the exact object through `coding_agent` and supported controls.
Match task, worker, generation, execution host, events and outcome.
On Windows do not use `os.kill(pid, 0)` as a liveness probe. One PID
or an absent leader does not describe the whole tree.

Work outcome and process custody are separate. `status=unknown` means
neither success nor necessarily a live process. Do not replace
`custody_recovered=false` and `continuation.safe=false` with invented
stopped state. With `next_boundary=summary_in_flight`, do not automatically
replay a step merely because the final record is missing.

Use `coding_control` to stop and verify that the exact owned tree ended.
The supported Windows path ties proof to a durable Job binding, not
a process-name guess. Do not delete a lock or manually edit terminal
status to release the writer. Preserve working copy, results and history.

Legacy tasks without sufficient binding need supported recovery:
the owner confirms the original Windows host and, where required,
the procedure includes a full OS reboot. Do not reboot unilaterally.
Application restart is not equivalent evidence. Unknown work outcome
remains unknown even after custody was safely released.
