# KEAT production ingress/history integration gaps

Scoped inspection: 2026-09-10, proposal `f8a5f4b3`, base `64f1588c`.
This started as a source-path report. The proposal now includes native owner-DM
text admission, canonical metadata/hot-rebuild preservation, positional many-to-one
projection validation and model-call/tool-response resume integration. A default-off
scheduled-task wake adapter now issues at concrete-occurrence creation, persists
its receipt with the task, and only verifies/adopts it at firing and tool-safe
resume. Targeted synthetic native tests
exercise those chains. This is **not deployment evidence**.

Implemented owner text seam: `_record_life_message(capture_live=True)` calls
`capture_ingress`; raw line/actor and stable Telegram coordinates are committed
under an explicit enrollment. `_dm_dialogue` emits ordered sidecars, never text
matches. `voice_turn_envelope` passes them only for text-only role-mode turns;
`adopt_projection` independently checks original payloads and exact prefix
stripping/coalescing. Unknown historical rows, media, first-turn flattened input,
edits/deletes without fresh receipts and all unintegrated modes fall back.
Group/window native authority integration and ad-hoc wake below remain outstanding.

## Critical boundary

`agent._voice_impl(user_msg, history, ...)` is not generally original source
capture. `voice_turn_envelope` passes a selected/aggregated conversation as the
current user message; an empty `history` does not prove that this message is new.
Issuing an original occurrence there for arbitrary callers would manufacture
historical capture authority. A raw direct `respond` invocation and a Telegram
aggregate must be distinguished explicitly by the caller, not by content or by
`ChannelContext.owner`.

Existing live text/render behavior must remain unchanged on missing provenance.
Capture enrollment must be explicit and independent of serving staging. Existing
archive IDs identify records; they are not KEAT capture grants.

## Owner DM

* `mtproto_runner._buf_push` / `_record_life_message` record admitted Telegram
  events through `memory_life.record_message`. The canonical event has `id`,
  `source_id`, revision metadata and place identity.
* `memory_life.hot_records` drops canonical event `id` and metadata. Its legacy
  direction inference is useful for existing display but is not original source
  authority.
* `_dm_dialogue` reduces each hot record to `(is_self, stripped_line)`.
  `_turns_to_dialogue` coalesces adjacent roles and joins *all* events after the
  last assistant into `current`. No previous assistant means the legacy full
  conversation path, not a new single user occurrence.
* `agent.voice_turn_envelope` chooses `current_text` or full `convo_text` and may
  run `media.prepare_turn` before calling `_voice`.

Required integration: capture original admitted update/output at creation under
explicit policy; persist its issuer reference in canonical life-event metadata;
preserve reference through state rebuild and hot projection; carry ordered lists
of origins through role coalescing and current-message aggregation. Media
projection must bind the same admitted media occurrence, not look it up by text.
Cold-start Telegram fetch and legacy buffers stay unsupported, without recapture.

## Groups

* `group_context.observe_message` records originals **and** edits and is also
  called by backfill/scan paths. Adding unconditional capture inside this common
  function would wrongly enroll selected historical data. Live admission needs an
  explicit capture policy/caller distinction.
* Canonical rows retain peer/topic/message/revision fields. `context_rows`
  renders/clips them into `self`, `line`, `role_line`, dropping original identity.
* `_fold_service_rows` folds synthetic root/truncation rows into adjacent role
  text and returns triples. `_group_context_frozen` stores these in wake snapshots.
  Ordered occurrence and runtime-service origins need an additional projection
  channel, not provider-visible metadata.
* The continuation path compares `known_lines` as a set of rendered strings.
  This is existing display behavior, **not valid occurrence matching**. Repeated
  identical messages, edits and clipped messages cannot gain KEAT identity this
  way. A separate exact ordered projection must be derived from canonical refs.
* Deletes and out-of-order originals already have archive current-state rules.
  Capture heads must follow those rules with fresh revision-specific grants;
  delayed originals must not resurrect an edited/deleted head.

Required integration spans the live ingress handler, archive persistence,
`context_rows`, frozen wake snapshot representation, coalescing and the agent
boundary. Merely adding an optional key to archive rows would not complete it.

## Wake and window

The scheduled-task wake path is implemented but configuration-default-off and
fail closed. Its authority identity is strictly the scheduler task `id` (`task_id`)
plus one concrete occurrence coordinate; neither field alone, goal-text equality,
nor scheduler routing can identify an authorized source. A new concrete occurrence
is captured where it is created: `tasks.add` for one-shot tasks, `tasks.due` for a
recurring task's first deadline, and `tasks.mark_fired` when advancing later
occurrences. The opaque receipt reference is persisted in the same
`memory/tasks.json` row and therefore survives restart/handoff. Ledger issue and
task-file replacement are separate fail-closed writes, not an atomic authority
transaction: an orphan ledger receipt is not discoverable from a task and cannot
be adopted. A task persisted without the opaque reference stays legacy; neither
`due`, scheduler routing, nor firing reconstructs authority from its id, time, or
goal. The scheduler passes the exact persisted row to `agent.wake_turn`; the firing
adapter verifies the canonical creation payload, exact `task_id` + occurrence
coordinate, original policy authority fields, and current narrowing before
adoption, but never mints one. Missing legacy references, changed intent, changed
coordinates, storage faults, and disabled or invalid policy all fail closed to the
unchanged live path. Issuer, audience, and `scheduler:wake` enrollment still come
only from administrator policy. These are offline implementation facts, not a live
capture/serve receipt.

Direct/ad-hoc `wake_turn` calls have no scheduler `task_id` + occurrence source and
deliberately fall back; this adapter does not support them. `task_window` renders
coding/rest/self goals and mailbox/runtime evidence and has no native source
adapter. Group ingress and media (including wake-derived media) likewise remain
unsupported. Runtime status and envelope fragments still need typed runtime
origins; neither empty history nor the owner scope override grants authority to
historical goal content. A configured `wake`, `window`, or `group` selector does
not fill any of these source-authority gaps.

## Safe current boundary and next step

The capture facade must reject production aggregates unless the caller supplies
verified source references and exact ordered projection. Do not stage any mode
on the basis of a successful synthetic `_voice` fixture. Keep tool-safe exact
fallback, including role/media/tool structures, without provenance reconstruction.

Next useful implementation slice: extend the implemented owner text seam to
explicit edit/delete current-head updates and media admission, then add group and
window typed projections. Keep the scheduled-task wake adapter narrowly bound to
its persisted coordinate; do not treat it as support for ad-hoc wake, window, group
or media. Do not use mode flags as substitutes for missing source adapters.
Owner, group, scheduled wake and window each require their own production-path
receipt. No live deployment was attempted in this investigation.

### Invalidation repair boundary

Exact native coordinates now revoke both incoming and outgoing grants, with a
canonical pending barrier enforced by selection and checkpoint reopen. Real
outgoing edit ingress is subscribed; it invalidates before the self-return.
Real peerless deletion ingress conservatively retires the entire enrolled capture
namespace (including runtime-derived evidence). No peer identity or conversation
tombstone is guessed. Precise DM delete projection, availability-preserving peer
resolution and automatic namespace recovery remain unsupported. Revisions are
not reissued as fresh historical grants. Interrupted canonical revoke is retried
by duplicate ingress; pending state survives restart and blocks serving meanwhile.
Storage failure before initial barrier persistence requires transport replay or
operator reconciliation before reactivation. Group/window/media and ad-hoc wake
adapters remain unsupported. The scheduled-task wake adapter is implemented but
default-off and has only synthetic test evidence. There are still no live
deployment/capture/serve/restart/rollback receipts.
