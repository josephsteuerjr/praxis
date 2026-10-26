# Native source self-review

Hélène uses existing Forge reviewer units, not another scheduler. The runner
installs `localharness.self_review_integration.Integration` after importing the
selected agent and Forge. An independent maintenance thread services controls and review progress, including while the ordinary model turn is busy. Pause retains requests without admitting new reviewers; Stop remains
independent of review, including failed or unavailable review.

## Owner controls

The existing authenticated channel exposes `GET /api/self-review` and
`POST /api/self-review`. Owner keys are required; paired-device access is denied
at both the HTTP and tunnel doors. POST returns a durable **queued** receipt,
not a completed review or author approval. GET returns `settings`, `reviews`,
`commands`, `capability` and the last `runtime` observation, including its time.
Reconnecting reads these same records.

Actions:

- `request`: optional `depth` (`quick`, `normal`, `deep`), `intent`, and `retry`.
  Identical subjects reuse prior evidence unless an explicit settled retry is
  requested. A queued or unknown execution is never blindly restarted.
- `cancel`: review `id`. Intent is persisted before Forge Stop; an uncertain
  process stop stays unknown rather than being called confirmed.
- `disposition`: `id`, exact `subject_sha256`, `decision` and a nonempty `reason`.
  Decisions are `accepted`, `fix`, `deferred`, `continue-with-reason`. Acceptance
  requires a completed clear review of current bytes. An explicit continuation
  preserves the actual findings or insufficient verdict.

The agent receives the same `self_review` hand in an owner dialogue. It does not
gain a new unauthenticated route or permission to edit installation paths.

## Settings and triggers

The `self_review` block uses the existing `/api/agent-config` writer and mtime
freshness receipt, or the desktop full-document writer. Both validate before
writing. Defaults: `after_update: false`, `after_self_edit: true`, `depth: normal`,
`models: []`, `max_parallel: 1`, `max_iters: 20`. An empty model list uses the
current voice model; names select models only on that same configured route,
never an alternate credential/provider. Parallelism is 1–8; iteration cap 1–200.

Update receipts are observed independently of the old “notice reported” marker.
Actual merged/conflicting overlays, absent merge bases, missing materials and
skipped code inputs force a persisted required review even with optional
after-update review off. Exact conflict paths select frozen base, agent, release
and actually selected candidate bytes from retained carry materials. Missing
conflict sides remain an explicit evidence gap;
startup and package health do not wait for review. Preserving an old code tree
alone is not claimed to be a conflicting merge.

Confirmed shell/fs-write/fs-edit self-edits use the existing attribution and
autocommit path. `SELF_EDIT_REVIEW_API = 1` installs one callback in place of the
legacy immune enqueue and pre-shell evaluator, avoiding duplicate paid reviews.
The code-only snapshot excludes memory, persona, settings and credentials.

## Conflicting source updates

`source_update` uses a dedicated restricted Forge merger, not an ordinary Jarvis conflict turn. Only retained conflict paths can change. The mandatory panel receives inventory-bound A/L/B/M, explicit absence/modes and material gaps; syntax-ready is not review-clear. Findings create a bounded new merger attempt and candidate-bound review operation. Apply rechecks canonical critic bytes, inputs and owner Stop/Pause. Unknown/unavailable evidence retains the old program with `needs_attention`. Historical dedicated completion events are also quieted.

Downloaded files cannot retrofit the already-running old updater. Preserved authored Forge without the explicit adapter remains unsupported, not silently replaced.

## Limits

A new shipped tree is not necessarily the executing selected tree. A preserved
agent or Forge lacking the explicit callback/adapter API reports unsupported;
installing a package does not silently overwrite that code. Source-only clear
does not establish installed, graphical, provider or process-lifetime behavior.
Those checks and any release decision remain separate evidence.
