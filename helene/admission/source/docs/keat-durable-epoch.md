# Offline original receipts and durable A epochs

`keat_epoch.py` is the next bounded offline step after `e1dfc991` and the
occurrence audit. It is **not full KEAT**, a serving integration, permission
issuer, provider protocol validator, cache implementation or live resume runner.
Only stdlib and the existing offline contracts are imported. Existing legacy
`keat_source.DurableSnapshot.require_candidate_authority()` still always rejects.
No live module, hook, flag, cache behavior or provider call is changed.

## Trust boundary / inputs

`validate(source_bytes=..., authority_bytes=..., pins=EvidencePins(...),
audience=(...), now=...)` consumes two strictly parsed versioned JSON artifacts.
It accepts no run scope in lieu of historical provenance. The independently
trusted reader supplies namespace, source SHA-256, **current** authorization
SHA-256/revision, exact destination audience, and current time. Pins are **not
signatures**. Computing pins from arbitrary input does not authenticate it.
This module cannot establish that an issuer told the truth, a projection was
semantically faithful, the supplied current state really is current, or a
complete source selection was actually complete. Those are explicit upstream
reader/issuer responsibilities; there is no self-pinning or auto-rebinding API.

The source schema `keat.original.v1` has:

- `namespace`: stable original-source namespace, never a mutable place alias.
- `receipts`: ordered ledger of original events, each with `event`, stable
  source-message `key` (must include channel/chat/topic coordinates upstream),
  typed `kind` (`message`, `synthetic`, `tool`, `runtime`), integer `revision`,
  `parent` receipt digest, boolean `deleted`, `payload_digest`, and `capture`.
  Revisions start at zero; subsequent revisions increment exactly, reference
  their immediate parent, and cannot revive tombstones or change origin kind.
- `capture`: explicit `issuer`, `policy_revision`, `grant`, ordered `audience`,
  `transfer`, and boolean `presence_hidden`. No value is inferred from run owner,
  current room state, or matching content. The original payload digest is an
  opaque commitment; payload verification belongs to the trusted source reader.
- Exact structured `messages`, ordered full `tools` (including `None` vs `[]`),
  caller-authoritative `historical_count`, and one `projection` entry per message.
  Entries contain exact `index`, full `message_digest`, ordered original event
  `origins`, explicit `transform` and `issuer`. Coalescing can map multiple
  originals to one message. Issuer attests prefix removal/block transformations;
  validator never reconstructs or normalizes them. Latest nondeleted receipts,
  in ledger order, must occur **exactly once** in flattened projection order.
  One-to-many source splitting/reuse is deliberately unsupported in v1: issue
  typed, authoritative derived occurrences upstream or design a later contract.

The current `keat.authorization.v1` contains namespace, revision, exact audience,
`valid_from <= now < valid_until`, `heads` mapping source keys to current receipt
hashes, and `grants` mapping grant IDs to exact capture receipts. Every selected
source head, including tombstones, must match; every live capture must exactly
match its current grant and destination audience. Selected current grants undergo
the same exact-key/type validation as capture receipts before comparison; integer
`0`/`1` cannot substitute for boolean `presence_hidden`. Unselected grants are not
validated as a complete authorization-ledger audit. Revocation, transfer/presence
change, narrowing/widening, missing historical receipt and edits/deletes all
reject old evidence. This restrictive v1 does **not** implement cross-audience
transfer or infer whether a policy label authorizes it. A trusted issuer must
provide only authorized grants; labels have no ambient permission semantics.
Newly issued source/projection can represent a valid revision/tombstone ledger.

JSON rejects duplicate keys, nonfinite values, lossy types and invalid UTF-8.
Exact bytes are pinned separately from canonical structured digests. Message
roles, extra fields, nested media, tool IDs/results, schemas and ordering survive
unchanged. Equal text from distinct events stays distinct; model-call IDs and
filesystem paths are not original identity.

## Durable API and crash model

`EpochStore(directory).checkpoint(expected_parent=..., epoch=..., policy=...,
reason=..., **validation_arguments)` validates authority before writing an
immutable content-addressed JSON object, then atomically publishes `HEAD`.
It retains parent hash, exact source bytes/pin (including receipts, projection,
cut, historical and active tapes), explicit epoch/policy/reason, full schema
digest, namespace/audience and issuance authorization pin/revision. Authorization
may be revalidated with a newer pin on reopen; source pin remains fixed.

Every operation takes an independently retained expected head/parent. Under a
cooperative POSIX `flock`, the entire existing parent chain is read and hash
checked. Stale parent, changed intent under the same old parent, missing objects,
corruption and malformed HEAD reject without repairing state. Same exact intent
at the current head returns the same digest after a lost acknowledgement (but
still validates current authorization and re-establishes durability before success). Same-epoch successors must preserve the
historical message/projection prefix and its receipt bytes and cannot shrink A
or change policy/namespace/audience. Historical projection entries pin each message's
canonical JSON digest: Python-equal numeric aliases (`true`/`1`/`1.0`, including
nested extras/tool inputs) therefore cannot bypass the same-epoch prefix check
when projection and authorization pins are reissued. Receipt revision and boolean
fields require exact JSON types before receipt comparison; numeric aliases reject.
Canonical identity is not raw JSON whitespace/key-order identity.
Changes require an explicit new epoch with
a reason. Full tool schema changes are recorded independently; they do not
silently redefine semantic epoch policy or claim provider cache identity.

Object and HEAD publication use same-directory temporary files, flush/fsync,
`os.replace`, and directory fsync. An interruption before HEAD publication may
leave an unreachable immutable object, which exact retry hash-checks, file-syncs
and directory-syncs before reuse. A crash
around HEAD publication has the usual old-or-new-head outcome: independently
retained candidate digest and retry allow reconciliation. I/O/fsync failures are
reported, not interpreted as success. Each acknowledgement (including exact retry
with an already-visible HEAD) syncs object and HEAD files, the store directory,
and every containing directory through the filesystem root. This establishes
nested directory entries even when they were created by an earlier interrupted
process. Constructor mkdir alone is not a durability acknowledgement. Continued
file/directory sync failure continues to raise; recovery permits exact retry.
This conservative POSIX backend requires readable, fsync-capable ancestors; it
fails rather than downgrading durability on unsupported filesystems.
No GC, repair, automatic rollback or
retention policy is included. This backend is **POSIX local filesystem only**;
network-filesystem locking/durability, hostile writers/symlinks, broken storage,
and Windows are not supported. Directory contents and lock participants must be
trusted. Hashes and caller pins cannot defeat malicious local rewriting/rollback.

`reopen(expected_head=..., authority_bytes=..., pins=..., audience=..., now=...)`
checks the current head and every ancestor, revalidates the pinned source with
current authority, and returns owned `historical_a`, `active_roles`, full source,
epoch and policy. Repeated fresh-process-style reopen is deterministic; mutating
returned nested objects cannot alter disk state. A cut across tool-use/result
is preserved exactly, not declared safe for serving. No tools are executed.

## Verification and exact remaining blockers

`test_keat_epoch.py` uses synthetic trusted issuer fixtures, not live captures:
identical content/distinct source keys, coalesced/synthetic projection, exact
media/tool roles with a mid-tool cut, positive edit/delete lineage and stale
mapping refusal, grant revocation/change/expiry, audience/pin failures, malformed
projection, restart/idempotency, competing CAS writers, pre-HEAD and post-HEAD-replace fault injection, continued sync failure/recovery,
nested store ancestor syncing and interrupted-object reuse, strict selected-grant types,
corrupt checkpoint/head/ancestor, explicit epoch flip and schema-policy separation.

Targeted command: `python praxis_test.py test_keat_epoch test_keat_candidate
test_keat_source -q` — 31 tests passed, zero connections (repair verification, including four new
regression methods with malformed-grant subcases).
This is not a full gate, deployment, power-loss experiment or integration proof.

Remaining capture/live work is real and intentionally not papered over:

1. Capture writers (`memory_life.record_message` / MTProto) do not emit these
   historical authorization receipts; current `rooms.transfer_of` is not a
   historical grant. Build an authoritative owner-rooted receipt issuer and
   current revocation/head reader with independent persistent pins/freshness.
2. Preserve stable source coordinates, revisions and coverage through dialogue
   role coalescing/prefix removal; existing projections lose them. Strict source
   enumeration must not skip corrupt lines or silently apply mutable place aliases.
   Define compacted-source coverage and one-to-many projection semantics if needed.
3. Existing durable model-input capture scrubs material. It cannot certify exact
   original provider input and is **not promoted** by this module. Tools/runtime
   output and synthetic blocks need their own authentic typed origin issuance.
4. Integrate checkpoint intent/pin persistence with a real authoritative source
   transaction and crash reconciliation. This store receives pins; it does not
   durably issue them or solve authority revocation races across external stores.
5. Decide and ratify semantic epoch/cut policy, authorization revalidation cadence,
   tool protocol-safe assembly, secret handling/retention and platform backend.
   Only then consider separately reviewed live resume/serving and rollback tests.
   No local Claude-authored KEAT candidate was established by the audit; no
   attribution or unlocated implementation comparison is claimed here.
