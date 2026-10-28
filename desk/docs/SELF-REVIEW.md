# Voluntary frozen-material self-review

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
  `critics:1..8` chooses an ordinary panel's count on the same configured voice
  models, within existing Forge slot/parallel limits; omission preserves defaults.
  Identical subjects reuse prior evidence unless an explicit settled retry is
  requested. A queued or unknown execution is never blindly restarted.
  Without `subject` this remains the source-only request. An explicit subject
  freezes a `plan`, `change`, or `task_result` (see below), never a live lookup.
- `cancel`: review `id`. Intent is persisted before Forge Stop; an uncertain
  process stop stays unknown rather than being called confirmed.
- `disposition`: `id`, exact `subject_sha256`, `decision` and a nonempty `reason`.
  Decisions are `accepted`, `fix`, `deferred`, `continue-with-reason`. Acceptance
  requires a completed clear review of current bytes. An explicit continuation
  preserves the actual findings or insufficient verdict.

The agent receives the same `self_review` hand in an owner dialogue. It does not
gain a new unauthenticated route or permission to edit installation paths.

## Ordinary work and explicit subjects

Independent criticism is voluntary help, not permission to finish ordinary work.
No reviewer, `skip`, or BLOCKED override is required for ordinary `selfdev.submit`
or Forge finish. The author's own diff review, existing test/execution rules,
rights and Stop remain. Exact supplied critic text and its verdict are retained;
an author disposition does not turn findings into clear. Old skipped-review
strings still work as evidence, not as approval tokens.

`request(subject=...)` uses the same Forge workers, snapshot reader and canonical
results. Every typed subject has `kind`, a nonempty `question`, and `criteria`:

- `plan`: `text` is the exact authored plan, not a generated summary.
- `change`: `materials` explicitly declares matching `base` and `candidate`
  inventories by logical `name`. A removed/added side declares `exists:false`;
  its path must actually be absent. The frozen `changes.diff` compares these
  bytes, never an unrelated previous review.
- `task_result`: `assignment`, exact `answer`, optional `observed_effects` and
  `checks`, plus exact artifact references. Effects/checks are **author claims**,
  not host attestations of execution. `task_id` may bind provenance for any kind.

Each material is `{name, side, root, path, sha256}`; `side` defaults to `context`
outside change, `root` is `workspace`, `agent`, or `app`, and `path` is relative
to that selected root. For example a plan can cite an exact UTF-8 report in
`workspace/receipts/check.json` with `root:workspace,path:receipts/check.json`.
No ambient home/memory/persona/config collection is added. Absolute paths,
traversal, links, missing files, changed hashes, credentials and binary inputs
refuse the request rather than silently dropping materials or substituting live
text. Author text is frozen as `authored.json`; reviewers can page every byte
through `inspect`. Inline descriptor budget is 1 MiB; declared files retain the
source service's 16 MiB/file, 64 MiB total budget, with explicit refusal, not cuts.

All typed subjects and locators are owner-only. The authenticated HTTP/tunnel
and agent-channel doors deny paired/public callers; restart does not broaden
the audience. A queued ref is checked against its SHA at capture, so changing a
file while queued yields an error, not review of new text. Reusing an operation
id for different typed material is refused. A new revision needs a new request;
old snapshots/verdicts remain. Exact stale materials cannot be accepted as current.

Typed ordinary review rejects source-update coordinators/overlays. Its clear
result or `continue-with-reason` cannot satisfy the separate strict update gate.

## Settings and triggers

The `self_review` block uses the existing `/api/agent-config` writer and mtime
freshness receipt, or the desktop full-document writer. Both validate before
writing. Defaults: `after_update: false`, `after_self_edit: false`, `depth: normal`,
`models: []`, `max_parallel: 1`, `max_iters: 20`. An empty model list uses the
current voice model; names select models only on that same configured route,
never an alternate credential/provider. Parallelism is 1–8; iteration cap 1–200.
Existing explicit `after_self_edit:true` remains the owner's opt-in automatic
source-review choice; this change does not rewrite saved configuration.

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

The strict incoming coordinator may declare unchanged distributed binary app
assets **opaque** only in `harness/full-composed-app-v1`: exact current bytes
match frozen B `published_files`, the exact composition is bound, and retained
release-evidence metadata names package/source manifest SHA identities. The
reader exposes the digest, size, mode, distribution path and proof locators;
it does not pretend to decode or inspect those bytes. Unknown/changed binary
inputs remain gaps/rebuild obligations. Ordinary source/typed requests cannot
enable this representation. A critic may still return insufficient for coupling.

## Limits

A new shipped tree is not necessarily the executing selected tree. A preserved
agent or Forge lacking the explicit callback/adapter API reports unsupported;
installing a package does not silently overwrite that code. Source-only clear
does not establish installed, graphical, provider or process-lifetime behavior.
Those checks and any release decision remain separate evidence.
