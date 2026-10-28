# KEAT candidate v1: exact role-tape partition, offline only

This is a small validated data contract, **not full A, a durable epoch, or a
servable request**. `keat_candidate.py` imports only stdlib. Nothing imports it
in the live path. No provider call, filesystem access, persistence, flags,
rollback settings, frame assembly or existing epoch policy changes.

## Representation and rationale

The authoritative model boundary uses message dictionaries, including nested
`tool_use`, `tool_result` and image blocks. Existing `frame_serve.select` leaves
the messages/tools objects intact. Conversely, shadow `_a_message` renders text
with caps; `_turn_sha` hashes role plus extracted text; `_resolve_fold` and
`_window_merge` resolve historical content overlap. Those are not safe sources
for reconstructing exact active roles. The scout also distinguishes full-schema
assembly identity (`frame_shadow._tools_digest`) from the intentional names-only
`tool_offerings.fingerprint` epoch policy. This module replaces neither.

`bind_boundary(messages, historical_count=..., audience=(...), source_scope=...)`
records a caller-chosen cut in **one exact complete tape**. It does not discover
where historical A ends. `candidate` revalidates that boundary against the tape
and independently supplied current scope, returning two tuples of the original
message objects. Concatenating those tuples reproduces the entire sequence,
without truncation, deduplication, relabeling, text extraction, image conversion,
or tool ID/result reconstruction. It does not validate provider tool protocol or
make a cut safe to serve as system text: both views remain roles, including if a
tool exchange straddles the cut. No rendering is provided.

Anchors contain occurrence index plus full-message SHA-256. The boundary binds
full-tape SHA-256 and count, contract version, exact audience tuple and source
scope. Neighbor anchors must be adjacent. `None` means only the empty side at a
tape edge. Missing interior anchors, overlapping/nonadjacent anchors, invalid
indices, old versions and changed tapes are rejected with `ContractError`.
Repeated identical messages are distinct occurrences by index **within the bound
snapshot**. No content-only search, first/last-match tie break or legacy count
fallback exists. Appending, editing or sliding the tape requires a newly
established boundary, even if an anchor's text still matches. Byte-identical
replacements cannot be distinguished as separate events by this representation;
it makes no cross-snapshot event identity claim.

Audience is an explicit nonempty tuple of unique opaque principal strings; source
scope is a nonempty opaque string supplied by the authoritative caller. Exact
matching rejects widening, narrowing, reordered principals, and different
source/run scopes. This is consistency checking, **not authorization or proof of
provenance**. A caller must establish trustworthy scope before binding; never
bind a new boundary merely to bypass a mismatch. The module does not infer an
owner from a label, read grants, or sanitize cross-audience history.

Full offered-schema identity hashes strict JSON with deterministic object-key
order, preserving array order and every schema field, descriptions included.
`None` and `[]` are distinct. Unsupported values/non-string object keys/nonfinite
numbers are rejected, not stringified. This digest is neither the provider's
cache key nor a decision to flip semantic epochs. Canonical JSON identity is not
raw provider wire-byte identity (object key order is normalized).

## Ownership and measurement limits

Candidates borrow message dictionaries and nested payloads by identity; frozen
dataclasses freeze only the descriptor, not the message objects. The caller must
not mutate them during validation/use. A candidate is not a durable snapshot,
thread-safe handoff, or authority token. Revalidate before each offline use after
any potential mutation. No sizes, tokens, latency or cache savings are claimed;
no full role tape or hashes are logged automatically. A future observer must
retain private artifact policy and correlate actual/candidate at the same call.
Rejecting this offline candidate does not block live speech: there is no live
hook to block it.

## Remaining durable epoch/resume gaps (explicitly not solved)

- Stable source event IDs/sequence and edit revisions surviving hot-record
  coalescing (`_turns_to_dialogue` currently loses IDs); trustworthy cut issuance.
- Cross-window append/edit/delete reconciliation without guessing overlaps;
  completeness when upstream history is compacted or absent from role tape.
- An A store preserving structured roles/media and source provenance, with
  audience revalidation, retention, encryption/redaction and atomic receipts.
- Epoch transactions tying K/E/A/T, audience/grants and offered schemas to durable
  revision IDs; reviewed separation of semantic epoch flips from provider cache
  identity (including model/provider/serialization options).
- Restart/resume and concurrent-run consistency: store versioning, migration,
  idempotency, corrupted/missing anchors, reordered/replayed tool results and
  in-flight tool exchanges; no content-based reconstruction fallback.
- Reviewed role-preserving assembly and paired live/shadow measurements for the
  same release across owner/group/window/wake. Mixed-schema historical artifacts
  are not equivalent pairs. No serve expansion is justified by these unit tests.
- Explicit live rollback and durable resume receipts. Existing fresh-E serving
  retains its existing live-system resume behavior; this contract adds no path.

Next useful step: an offline adapter with authoritative source occurrence IDs and
explicit scope provenance, tested against durable resume fixtures and real
same-call captures; independent review before any integration.

## Verification

`python praxis_test.py test_keat_candidate -q`: 7 tests, PASS, zero network
attempts. Cases cover identical occurrences, edited/appended/trimmed tapes,
audience narrowing/source mismatch, full-schema changes with unchanged tool
name, tool/result/image object preservation, malformed anchors and edge cuts.
