# Code proposals

A proposal is an engineering workspace with checks and history, not
expanded authority. Establish the active task, actual root, isolation
and recovery before starting. Risk labels grant no rights over outside
infrastructure, secrets or publication.

Start with `proposal(action="list")` to avoid duplicate work. Where
available, `proposal(action="start", reason=...)` opens a working copy;
check response and scope before writing. `edit(proposal_id=..., action=...)`
changes the selected copy; `proposal(action="submit", id=..., title=..., why=...)`
enters the supported process. Read actual requirements from schema and results.

For substantial changes, check tests, diff and behavior. A narrow green
test does not establish the whole scenario. Investigate red results;
where a mechanism permits a justified `override_reason`, record the
specific reason, risk and evidence boundary. It replaces neither
validation nor another source-update mechanism's requirements.

A merge changes files, not necessarily a running process. If needed,
perform `restart_self` within authority and verify readiness and the
original scenario. Retain what changed, why, checks, merge outcome and
recovery path. Promise neither automatic restart nor rollback without
confirmation in the current implementation.
