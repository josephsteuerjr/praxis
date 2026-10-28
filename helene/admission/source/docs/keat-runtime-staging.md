# KEAT runtime staging and verification

This checkout is an implementation proposal, **not deployment evidence**. No live
configuration, service or release ref is changed here. The older offline
contracts remain documented in `keat-durable-epoch.md`. The requested
`workspace/keat-live-step-blocker-20260909.md` was absent at baseline
`64f1588c`; it was not reconstructed from private runtime data.

## Non-negotiable activation conditions

- Capture is a separate operation from serving. Only newly observed or newly
  authored occurrences get identity. Legacy histories, restored model-input
  snapshots and equal text do not gain historical authority through migration.
- A deployment policy must explicitly name capture issuer, policy revision,
  destination audience and transfer/presence state. Run ownership and current
  channel labels are routing facts, not historical grants.
- Current authorization is read independently on every checkpoint/reopen under
  the same cooperative transaction lock used by revocation. Missing, expired,
  changed or revoked authority rejects the KEAT candidate. It does not rewrite
  the existing live request or interrupt its tool-safe fallback.
- The committed durable object binds exact source and authorization pins,
  ordered projection, A boundary, full tools, request identity and checkpoint.
  Unpublished preparation is not a served receipt. Provider cache hits are not
  established by this transaction.
- Role tape/tool pairing stays exact. Do not move an assistant tool-use or its
  pending result into text, reconstruct by matching content, or retry side
  effects outside the existing durable executor/idempotency mechanism.
- Account-critical secret material must remain in the existing encrypted TTL
  spool. KEAT is not an alternate unredacted model-input archive.

## Current implementation vs activation

The runtime transaction API recognizes owner/group/wake/window as independent
staging modes. That is **not** a claim that all production ingress adapters are
complete. Native owner-DM text and one narrowly scoped scheduler adapter are
implemented but configuration-default-off and fail closed. The latter supports
only scheduled task occurrences whose authority identity is the exact pair of the
scheduler task `id` (`task_id`) and that concrete occurrence coordinate. For new
tasks it issues a source receipt while `tasks.add` creates the concrete occurrence
and persists its opaque reference inside `memory/tasks.json`; `due()` does the same
when it first materializes a recurring deadline, and `mark_fired()` does it when it
advances to the next one. The capture-ledger issue and task-file replacement are
deliberately separate fail-closed writes, not a source-authority transaction: a
crash may leave an unreferenced ledger receipt, but firing cannot discover or adopt
it. Conversely, a task row saved without a receipt (including any issue/save fault
or legacy row) uses the unchanged legacy wake path; firing never repairs it by
minting authority. The firing path only verifies/adopts the exact persisted
`task_id` + occurrence reference: it does not mint creation authority, infer it
from goal text, or treat scheduler routing as issuer/audience authority. Changed
goal, task id, occurrence coordinate, policy fields, or current narrowing rejects
adoption. Direct/ad-hoc wake, window, group, and media source authority are not
supported by this adapter and remain on their unchanged fallback paths. The other
boundaries remain in `docs/keat-ingress-history-gaps.md`. Until a mode's actual
production path carries capture-issued references, it must stay unbound and use
the existing fallback even if a mode selector was configured. Tests using
synthetic messages establish contracts only.

## Rollout gates (separate explicit decisions)

1. Run targeted offline tests in the isolated test base; inspect changes and
   independent review. Then follow `AGENTS.md` clean-commit Linux full gates.
   No offline pass is a live-serving claim.
2. Before deploy, record exact clean SHA, immutable release ref and a new
   rollback ref/archive. Keep capture and serving disabled on first deployment.
   Verify service/process SHA, configuration and normal tool-safe fallback.
3. Provision an explicit capture policy and current authorization for **one**
   owner stream. Capture can be enabled while serving remains off. Do not
   bootstrap historical grants for existing rows; old rows remain ineligible.
4. Only after capture receipts and restart identity preservation are verified,
   separately opt that exact owner stream into serving. Require the probes below
   and an observer-approved receipt before proceeding.
5. Group, wake and window are independent later stages, one exact stream at a
   time. Owner success does not authorize group transfer or background/window
   histories. Unsupported ingress/history must report fallback rather than be
   considered a successful stage. No global wildcard activation.

## Mandatory post-deploy probes and receipt contents

Run only in an explicitly permitted live observation window. Record sanitized
run/call identifiers, exact release SHA and policy revision; never copy prompt,
private media, tool arguments, credentials or raw capture payloads into reports.
For **each** mode/stream record:

1. New ingress followed by a normal response: match original occurrence ID to
   ordered projection and committed checkpoint. Match model-input receipt to
   actual eligibility, served/fallback status and reason. Check that provider
   roles, structured media and tool schemas are unchanged.
2. A second turn with the same text but a different occurrence: ensure identity
   is distinct and the historical prefix remains ordered. A legacy-history
   turn must be visibly ineligible rather than retroactively granted.
3. Perform a read-only tool call, interrupt at a durable pending-tool boundary,
   restart, and verify existing executor pairing and idempotency evidence.
   Validate KEAT reopen against current authorization or record exact live
   fallback. Do not use an irreversible side effect merely to test resume.
4. Narrow/revoke authorization through the independent control writer between
   checkpoint and resume; ensure stale checkpoint is not selected. Restore only
   through a separately recorded explicit authorization action; restoring an
   environment variable must not resurrect grants.
5. Disable the selected serving stream during an active loop; the next model
   boundary must use exact existing live fallback. Verify stored live-system
   fallback, roles/media/tool IDs and no duplicated outbox operation.
6. Restart again and verify canonical ledger/checkpoint durability, current
   revision, no orphan promoted as committed, and truthful receipt counts.

The activation receipt must distinguish `eligible`, `served`, and `fallback`;
count each independently. Include probe outcomes, failure reasons as enums,
rollback command/config diff and who authorized this stage. A zero-call window
or a fallback-only run is **not** live KEAT success.

## Explicit configuration (default remains disabled)

Capture requires both `PRAXIS_KEAT_CAPTURE=on` and an absolute
`PRAXIS_KEAT_CAPTURE_POLICY` pointing to an administrator-authored JSON object:

```json
{
  "schema": "keat.capture-policy.v1",
  "namespace": "installation-specific-stable-namespace",
  "root": "/absolute/private/keat-state",
  "enrollments": [{
    "mode": "owner",
    "stream": "EXACT_STREAM_IDENTIFIER",
    "capture": {
      "issuer": "explicit-capture-issuer",
      "policy_revision": "approved-policy-revision",
      "audience": ["EXPLICIT_DESTINATION"],
      "transfer": "none",
      "presence_hidden": false
    }
  }]
}
```

These placeholders are not deployable grants. No real policy is provisioned by
this proposal. `keat_readiness.py` reports capture-only soak configuration as
`state=capture_only_ready`, with `capture_ready=true` and `serve_ready=false`.
Its compatibility field `ready` means serve readiness and equals `serve_ready`;
none of these configuration receipts proves live capture or deployment. Staging
additionally requires `PRAXIS_KEAT=serve` and `PRAXIS_KEAT_PAIRS`, a canonical
compact JSON list of lexicographically sorted, unique `[mode, stream]` string
pairs, for example `[["dm","CHAT_A"],["group","CHAT_B"]]`. Whitespace,
duplicates, unknown modes, empty streams, and unsorted pairs fail closed. The
selector is sparse: this example does **not** select `(dm, CHAT_B)` or
`(group, CHAT_A)`. Supported selector mode names are `owner`, `dm`, `group`,
`wake`, and `window`; every exact selected pair must match a policy enrollment.
`dm` is default-off like every other mode. Existing single-mode deployments may
temporarily retain one of the historical modes (`owner`, `group`, `wake`, or
`window`) in `PRAXIS_KEAT_MODES` plus comma-separated `PRAXIS_KEAT_STREAMS`;
that unambiguous compatibility path cannot enroll `dm` or combine modes, and
`PRAXIS_KEAT_PAIRS` takes precedence whenever present (including when it is
malformed, so it cannot silently fall back). Configuration is enrollment, not
retroactive authority. Root must be private POSIX local storage; canonical JSONL
partial/corrupt tails fail closed and are not automatically repaired or truncated.

An approved operator can explicitly revoke one already-issued grant locally:

```sh
python -m keat_control --directory "$CAPTURE_LEDGER_DIRECTORY" \\
  --namespace "$CAPTURE_NAMESPACE" --grant "$CAPTURE_GRANT" --revoke
```

Use the capture ledger subdirectory, not the epoch directory. This CLI cannot
issue or widen grants. A `committed` receipt means durable narrowing was
acknowledged; it does not prove a live model boundary observed it. The CLI's
sanitized output omits source payload and exception details. The canonical
ledger itself retains the control operation for audit.

## Rollback

Remove the exact stream from staged serving configuration (or disable all KEAT
serving), preserving capture/control/checkpoint canon. Recreate services if
changing environment, rather than assuming restart reloads it. Verify next-call
and durable-resume fallback before closing the rollback receipt. If reverting
code, use the pre-created immutable rollback release and the repository's
normal deployment reconciliation procedure. Do not delete or rewrite receipts,
issue replacement grants to make an old checkpoint pass, or turn revocation
failure into an automatic authorization refresh.

## Remaining blockers before claiming complete production KEAT

Native integration currently covers newly admitted owner-DM **text**, explicit
raw `respond`, model calls, pending-tool response continuation, and scheduled-task
wake with a persisted task/occurrence coordinate. The wake adapter remains
configuration-default-off and does not cover direct/ad-hoc wake. Group/window
have mode-gating and offline contract tests, but their original ingress, transfer
and frozen-window source adapters are not complete. They must remain unstaged.
Media and legacy/first-turn flattened histories remain unsupported.
Native edit/delete projection calls conservatively revoke the original grant;
they do not issue replacement authority. Capture/control must remain enabled
through edits: changes missed while capture is disabled require explicit
invalidation before reactivation, not blind reuse of old epochs.

The runtime object atomically binds pins/A/request checkpoint, while RunManager
model-input publication remains a subsequent durable write. A crash between
them leaves an unreferenced KEAT object, not a provider call. This is not a
single cross-store transaction and must be independently reviewed. Generic
`continue_checkpoint` resumes retain exact live fallback; only pending-tool
response continuation currently reopens the KEAT envelope. No provider cache
optimization or all-mode full production readiness is claimed.

## Reviewer repair slice (not activation)

Native invalidation now matches the exact `telegram:<peer>:message:<id>:`
coordinate across **all directions**. Incoming and outgoing edit notifications
reach invalidation before self/duplicate short circuits and before revision side
effects. Direct life revision writers invalidate before canonical append too.
The capture JSONL now records an `invalidate` pending barrier before canonical
revokes. The independent reader rejects **all namespace selection and reopen**
while any barrier is pending. Duplicate replay retries narrowing, syncs existing
records and only records completion after every matching grant is canonically
revoked. Completed prefixes permanently reject delayed original capture.

Actual peerless `on_deleted` events cannot authoritatively name a DM. They now
conservatively retire the **entire capture namespace**, including derived runtime
occurrences, without inventing any peer or readable tombstone. This intentionally
sacrifices availability; fresh capture needs explicit fresh-namespace enrollment,
not removal of old canonical invalidations. This is not precise DM deletion
projection or automatic recovery. Failures after pending persistence remain
blocked across restart until duplicate replay completes canonical revocation.
If storage fails before the write-ahead record can persist, the handler raises
before revision effects; durable transport replay/reconciliation is still required
before activation after such a storage outage. No implementation can persist a
barrier on unavailable storage. Keep capture/serve OFF pending operational proof.

The earlier failed matrix `verify-c950484b` is retained only as pre-repair
history. Its group-call regression was repaired by limiting `capture_live=True`
to the DM path. The proposal-caused rails witness drift was then repaired in
`agent.py` by keeping the new pre-guard source layout line-neutral and moving
the new KEAT ContextVars below the witnessed boundary. `soul/rails.md` was not
edited and `rails.manifest_drift()` now reports no drift. The other transient
source-extraction failures were re-run against the settled concurrent tree and
passed.

Post-repair authoritative matrix `verify-82b92f2a` passed on 2026-09-11:
`python praxis_test.py discover -q` ran **5162 tests, 5 skipped**;
`python -m pytest -q` ran **5295 passed, 5 skipped**; and
`python -m compileall -q .` passed. Focused KEAT/readiness checks and
`git diff --check` remain required after any subsequent edit. These are
candidate verification facts only: no deployment, capture, serving, provider
cache hit or live rollback receipt is claimed, and default-off serving is
unchanged.
