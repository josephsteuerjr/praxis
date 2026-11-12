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

Bundled praxis-relay 1.2.0 supports device-code sign-in for both account slots.
Settings show the code, confirmation link, expiry and cancellation. Confirm on
your phone or computer; headless servers no longer require a local browser.
Desktop browser sign-in remains available, and an existing local account can
still be transferred to a server. Saved credentials remain on the host running
the relay. Account changes invalidate the saved model catalogue.

The server owner channel controls sign-in through a restricted local socket;
the agent cannot read relay credentials or its private control key. Native
qualification uses synthetic credentials for persistence and restart checks.
Completing real account consent remains an owner interaction.

Extract the complete archive for your platform and run Hélène. This patch does
not change the platform limitations documented for 2.7.0: macOS builds are ad
hoc signed without notarization, Intel Mac local Whisper/XTTS are unavailable,
and physical phone/work-PC, public ACME, TCC, microphone, CUDA and paid-provider
acceptance remain separate. Server-born agent return is not yet supported.
