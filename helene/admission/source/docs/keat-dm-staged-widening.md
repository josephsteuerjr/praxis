# Ordinary-DM staged adapter (proposal, not activation)

Baseline: `2b792c96`. No live settings, services, hardbot, or private runtime data
are changed by this slice. This document is not a live rollout receipt.

## Why other chats used legacy

The baseline excluded non-owner chats at selection/capture, not at admission:
`frame_serve.enabled` required the exact owner DM, `keat_live` native capture and
adoption selected owner, and ordinary direct capture had no supported mode.
The runner still processed ordinary DMs and groups. Old-frame/high-cost processing
therefore is not evidence of non-delivery. No live delivery diagnosis is claimed.

## Contract

Ordinary DM is a separate default-off `dm` mode. Capture policy names one exact
positive Telegram peer and audience `["dm:<peer>"]`, with `transfer="none"` and
`presence_hidden=false`. Known, unknown, trusted, and family remain distinct live
channel facts, not historical grants or aliases for owner. Actual ChannelContext
and original tool schemas remain authoritative; the adapter does not issue trust.
Native admission chooses a unique owner-or-dm enrollment for the exact peer.
Native adoption checks the actual ordinary-DM context and positional references;
uncaptured history is never retroactively authorized. Edits/deletions revoke
native receipts for both supported DM modes, including peerless candidate barriers.

Serving additionally requires an explicit sparse `(mode, stream)` selection.
`PRAXIS_KEAT_PAIRS` is canonical compact JSON, e.g.
`[["dm","42"],["owner","1"]]`, sorted, unique, with no wildcard or Cartesian
expansion. These are illustrative test peers, not enrollment instructions.
Malformed or absent selection cannot widen ordinary DMs. Frame-v6 ordinary-DM
system replacement requires successful KEAT verification at the actual provider
boundary; failed verification restores the original live system and role tape.
Owner fresh-E behavior remains independent as before.

Media in direct structured ingress retains original block objects/bytes through
capture/projection. Native Telegram media has no proven source projection in this
slice and remains legacy. Tool uses/results stay on the authoritative role tape,
not converted into prose. Resume must independently verify original request,
current narrowing, exact policy and binding; it cannot invent missing source.

## Still not proven / do not enroll live

- Groups/topics: require root-room authority plus topic-scoped stream identity,
  participants, immutable addressing/reply targets, actor attribution, native
  admission/projection and accepted outgoing message receipts. `group` remains a
  reserved independently selectable mode, but its live adapter refuses capture.
- Native media: attachment receipt identity, caption/render mapping, accepted
  outbound media and deletion/edit evidence need a dedicated connected adapter.
- Wakes: the existing scheduled task/occurrence adapter is separate. Ad-hoc and
  non-owner/group wake widening, creation authority, destination/room revocation,
  restart/outbox delivery and late firing require connected proof.
- Windows: no window capture adapter is introduced. Durable window replay must
  bind its authoritative snapshot, room/topic/audience and pending tool boundary;
  missing or stale evidence must preserve legacy rather than fabricate grants.
- Live: clean-commit full gates twice, independent review resolution, deployment
  authorization, rollback receipts and actual Telegram post-restart round trips
  are still required. Offline provider fakes are not live delivery receipts.

## Rollback receipt required before any future activation

Record immutable clean SHA, release and rollback refs/archive, exact prior capture
policy digest/namespace and selector values, affected peers/modes, author/time,
reason and visible one-time notice. Record new policy digest and sparse pairs,
service/container identity, then accepted incoming/outgoing Telegram IDs and the
matching model-call served receipt. Do not copy private messages into this doc.
Rollback ordinary-DM serving by removing its exact pair (or disabling KEAT/frame
serving), leaving owner pairs unchanged. Revalidate the next provider call uses
legacy bytes and normal delivery; retain capture and revocation evidence for
safe reconciliation. Capture-only and serving are separate decisions.
