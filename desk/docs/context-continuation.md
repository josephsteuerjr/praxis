# Local conversation continuation and exact reply evidence

`runner._run_turn` binds one actual local history selection to the composed agent.
`agent._voice_impl` prepares/binds `frame_continuation`; `_model_call_impl` freezes
its first actual request and observes it after the provider returns, before any
tool iteration mutates the list. The checkpoint is private derived evidence, not
the conversation or a replacement for the run's complete model-input artifacts.

## FIRST input

The real runner → envelope → voice → model-call chain is wired, including a
first-ever conversation without an earlier assistant reply. Raw occurrence
admissions are persisted by exact source ID before prompt or durable-origin
assembly; transport archives also retain publication audience, so a later public
resume cannot re-import earlier private raw words or outgoing locators.

The complete previous successful FIRST input remains an exact prefix, followed
by newly accepted canonical conversation, admitted dossier versions and the new
current evidence packet. Tool iterations do not replace this anchor. A silent or
failed delivery does not invent an assistant turn and does not discard the old
anchor: only new source input is appended. Recognized audio is ordinary labelled
text; unrecognized media is not text. Mutable owner STATE travels in the current
packet on this source-bound path rather than changing the system head each turn.

There is no 2x overhead reset and no guessed model-capacity limit. The source
adapter supplies all currently unfolded hot records; a renderer window is not an
authored fold. The durable memory frontier is an explicit new generation. Archive
recovery does not resurrect already folded source rows. Actual source revisions,
deletes/reordering, fold/policy generation, system, tools, configured model/account,
actual fallback route, rendering and audience/rights changes invalidate reuse with
an explicit metadata reason. Current source proof is checked again after success;
a detached older turn cannot overwrite a newer source-compatible checkpoint.

`PRAXIS_FRAME_CONTINUATION=off` keeps the existing renderer path and does not
move state/admit dossiers behind the disabled switch. Current image/multimodal
and KEAT-specific adapters retain their existing path; per-call metadata says
`not_applied:media_adapter_not_qualified` or `not_applied:keat_adapter`. This does
**not** claim frozen provider-native pixels, image-budget stability, or KEAT
inter-turn prefix qualification. Their original input remains available through
the ordinary run artifacts. No live-provider cache percentage follows from the
offline tests.

## Dossier and privacy admission

The original transport/person selection still decides which dossiers enter.
`frame_dossiers` retains each admitted rendered version once in admission order,
including when that person leaves the recent roster and after process restart.
A new version appends. It does not load every dossier in the archive or grant
any tool rights. An actually deleted source revokes its projected versions;
retired snapshots stay private in the accumulator as evidence.

Accumulator files are bound to source/fold generation, exact audience/principal,
policy and installation admission. A new audience reads a different boundary
file and re-admits only currently permitted source material, never the old
private prefix. Old snapshots remain addressable privately. Legacy group epoch
freezing also carries an admission key; revocation cannot reuse an old E block
merely because its conversation anchor stayed equal.

## Late input and accepted replies

The existing durable Telegram pending-input and local sealed inbox remain the
owners. There is no new scheduler or Telegram client. Telegram late addressed
text is offered FIFO at the next model boundary in the same tool cycle; later
actors remain untrusted speech and do not change the original run principal or
authority. The local inbox preserves its established ordered batch semantics.
Unknown/failed checkpoint ACK does not mean model observation.

`addressed_replies` privately associates exact occurrence IDs with exact model
blocks. Live object identity prevents equal text from impersonating another
occurrence; persisted position + block hashes permit exact checkpoint recovery.
Only a successful actual model return records observed membership. An accepted
complete `reply` then records that exact set against its real delivery key and
publication ID. Local text publications have stable `helene-…` row IDs and keys;
Telegram retains real message IDs. A larger outgoing ID is never a watermark.
Narration, silence, intent, failed/partial split send and text-only media labels
provide no reply coverage. The index is disposable scheduling evidence, not a
claim that every question was answered; raw archives and publication receipts
remain authoritative. Late Telegram media stays pending for a media-aware pass.

Same-conversation `turns.recent_work_note` is an authored handoff with exact
chat/topic + scope, run/time/delivery/truncation/full-source pointers. A newer
empty turn does not borrow an older nicer note. It is separate from the technical
lived-turn trace and from the stable-work completion join described in
[authored-work-notices.md](authored-work-notices.md).

## Verification surfaces

- `desk.tests.t_context_continuation_192`: exact source/binding and no-overhead-reset rules.
- `desk.tests.t_context_call_230`: composed agent voice/model/provider adapter with
  an offline SDK; fresh interpreter per turn, actual provider message prefix,
  production builder's volatile STATE placement, internal/Telegram DM/topic,
  tool-iteration anchor, source/fold/rights and private-to-public restart cases.
- `desk.tests.t_addressed_context_230`: real pending-input/checkpoint and local/
  Telegram publication consumers; exact membership, FIFO/media, complete/partial
  sends, unchanged original authority and checkpoint recovery.
- `desk.tests.t_authored_notice_audience_230`: stable-work note/notice producer and
  new-process public/paired projections, with private text **and locators** absent.

These tests do not contact models/Telegram or replace installed four-OS,
physical media, relay-wire/account/cache-usage acceptance.
