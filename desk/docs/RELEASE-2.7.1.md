# Hélène 2.7.1 Beta

**Beta — published as Latest for the built-in updater.**

Every available tool now reaches the model with its complete native schema.
The old pointer mode no longer hides tools behind a preliminary describe/call
step. Python scripts can still use h.hand and h.describe. Previously recorded
call-based turns remain resumable within their original tool authority.

The Context screen now offers chat search, local dates with a time zone,
older/newer navigation, refresh, and access to retained snapshots beyond the
first thirty. Comparison and metrics are grouped separately; metrics follow
the selected chat. Late responses cannot overwrite a newly selected snapshot.

New snapshots retain the complete offered tool schemas, shared by content hash
and removed when no retained snapshot refers to them. Each schema can be
expanded and searched. Older snapshots explicitly say that their schemas were
not recorded; they are never filled with today's catalogue. Shadow context
remains a reconstruction, not a recording of the exact provider request.

Starter workflow skills describe direct native calls. Existing authored skill
files remain intact; changed stock is offered for review.

Extract the complete archive for your platform and run Hélène. This patch does
not change the platform limitations documented for 2.7.0: macOS builds are ad
hoc signed without notarization, Intel Mac local Whisper/XTTS are unavailable,
and physical phone/work-PC, public ACME, TCC, microphone, CUDA and paid-provider
acceptance remain separate. Server-born agent return is not yet supported.
