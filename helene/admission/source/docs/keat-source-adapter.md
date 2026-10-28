# Offline durable source-occurrence inspection

`keat_source.py` is an offline-only adapter for **one persisted model_input
snapshot**, not full KEAT, an A store, a resume executor, or a servable candidate.
It imports only stdlib and the offline `keat_candidate` contract. No live imports,
wiring, flags, serving, epoch/cache policy changes, restart or provider calls.

## Evidence inspected

The implementation was grounded in checked-out source, not private captures:

- `agent.py:_call_model` persists `{system, messages, tools}` using
  `RunManager.store_result`, `event_kind="model_input"`, `name="model-input"`,
  `call_id`, UTF-8 JSON media type. It may additionally retain
  `frame_v6_live_system`; the adapter preserves every parsed field.
- `agent.py:_durable_model_messages` and `_durable_model_blocks` scrub critical
  values and replace incomplete tool fragments with opaque commitments. Thus
  exact persisted JSON **cannot certify exact original provider input**. No
  redaction reversal, image reconstruction or text matching is attempted.
- `run_manager.py:create` writes `manifest.json` with `praxis.run.v1`, immutable
  RunContext fields and event counters; `_event_locked` assigns run-scoped
  monotonically increasing `seq` and `run_id:evt:NNNNNNNN` IDs to `events.jsonl`.
  `store_result` writes `results/NNNN-model-input.log`, a
  `praxis.result-ref.v1` with result ID, exact-byte SHA-256 and size, then the
  referencing event. None of these paths is changed or imported by the adapter.
- `run_context.py:RunContext` supplies principal, scope and origin/delivery chat
  IDs. These describe a run, **not each message's audience**.
- `run_resume.py:_validate_events`, `_validate_model_input` and `_model_pair`
  check ordered events and select a model input by call ID. `agent.py`'s
  `continue_tool_response` resume branch copies persisted structured messages
  and tools. The offline adapter does not execute or replace that recovery.

## Authority and identity

A caller supplies exact artifact bytes, independently retained `SourcePins`
(namespace plus SHA-256 for manifest/event stream/input), selected call ID, and
independently revalidated `RunProvenance`. A namespace denotes the authoritative
store, not a file location or a content digest. Pins are **not signatures** and
cannot authenticate a malicious issuer. Hashing newly supplied untrusted bytes
and calling those hashes pins would void the trust contract. There is deliberately
no self-pinning helper, directory reader, automatic retry/rebinding or pin store.
A stale manifest or incomplete event stream is rejected rather than repaired.

Occurrence identity is the typed tuple:

```
(namespace, run_id, event_id, call_id, result_id, message_index)
```

Content digests validate integrity, never identify occurrences by matching text.
Identical messages at two indices are distinct. Identity survives re-reading the
same pinned durable snapshot at another local path; it does **not** claim that
messages repeated in another model call are the same originating chat events.
Different source events with byte-identical content are not reconstructed.
Reordering messages against the retained input/ref pins, event reorder, duplicate
call/result identity, missing source identity and mixed run/ref scope fail closed.
Changing all trusted pins is new issuance, not validated continuity.

`DurableSnapshot` owns parsed JSON and preserves exact persisted roles, nested
media/tool exchanges, system and offered schemas. It does not enforce provider
protocol or synthesize tool results. JSON key order/whitespace is not a wire-byte
identity claim; exact raw bytes are separately pinned. Duplicate JSON keys,
nonfinite values and invalid UTF-8 are rejected. Nested objects are mutable:
re-adapt pinned bytes before further offline use after mutation.

## Explicit unsupported boundary

Native artifacts lack authoritative per-message originating event IDs and
per-occurrence audience provenance. Principal/scope labels cannot establish that
all historical messages belong to the current audience. Consequently
`audience_support` explicitly reports unsupported and
`require_candidate_authority()` **always raises `UnsupportedSource`**. There is
no audience parameter, positive audience fallback, guessed historical cut or
promotion into `keat_candidate.candidate`. Mixed audiences inside a native tape
cannot be detected from these schemas: all candidate promotion is rejected,
including seemingly homogeneous tapes. This is a material fail-closed result,
not a claim that durable artifacts solve scope authority.

A future positive adapter requires independently authoritative per-occurrence
provenance and cut issuance, with reviewed evidence storage and current audience
revalidation. Adding caller-authored audience labels to synthetic fixtures would
not solve that gap. No live behavior is blocked by an offline rejection.

## Fixtures and verification

`test_keat_source.py` writes synthetic fixtures to temporary disk with the actual
`RunManager.create/store_result/append_event` writers, then reads manifest,
events and result bytes just as offline recovery evidence. No private contents
are committed. Tests establish schema-grounded durable roundtrip and stable IDs,
exact roles/media/tool blocks, explicit unsupported audience, tamper, missing IDs,
reordered events/messages, mixed run provenance, duplicate source occurrences
and duplicate JSON keys. They do not prove live same-call capture equivalence,
cross-window reconciliation, authorization, full resume planning, concurrency,
retention/encryption, performance or provider-cache savings.

Verification: `python praxis_test.py test_keat_source test_keat_candidate -q`
passed 17 tests with zero network attempts in independent review agent-9d65df3b.
The owner run additionally verified source, resume and candidate suites: 60 passed.
Acceptance is limited to offline source/selection; no deployment is implied.

### Interrupted tool-response disk fixtures

`test_disk_resume_interrupted_read_only_tool` and
`test_disk_resume_completed_tool_before_checkpoint` write synthetic runs in a
`TemporaryDirectory` using actual `RunManager` create/result/event/tool/status
writers. Each reopens the directory and invokes only the offline `plan_resume`
selector: one interruption leaves a read-only tool outstanding; the other has a
completed durable result but no next checkpoint. Both select
`replay_model_tool_response`, retain the exact earlier input paired by call ID,
and distinguish replayable outstanding work from completed non-replayed work.

Each fixture then journals an explicit synthetic resume, persists the missing
result if needed, and writes a synthetic next model input/output (no model or
continuation executor runs). The next input retains the identical structured
message prefix and tools, appending the exact synthetic tool exchange. Selection
must return that second input, not the first. Adapter occurrences bind each
selected input event/result/call/index; identical prefix messages in the two
calls have disjoint occurrence IDs. Both snapshots still reject
`require_candidate_authority`. Evidence files remain byte-identical during
planning/adaptation (the RunManager read lock identity is excluded). This is
source/selection coverage, not live recovery execution or full KEAT acceptance.
