# KEAT input boundaries and preparation timing

Source audit: proposal based on `c2d4108a`, September 2026. This is code-path
mapping, **not live latency evidence**; no serving-policy change is implied.

## Preparation receipt

`agent._voice_impl` measures monotonic, non-overlapping phases:

- `old_context`: context normalization, recall query selection, and the complete
  `_build_prompt_parts` (persona/state/dynamic/evidence, including retrieval).
- `message_tools`: evidence wrapping, role-tape window/render, situation, system
  sealing, and offered tool selection.
- `shadow`: optional shadow capture, including assembly and disk writes.

`agent._model_call` adds `call_setup` (secret detection and live trace metadata),
`measure` (measure-only candidate), `canary` (serve selection/reassembly), and
`model_input_artifact` (rollback scrubbing, status gate, JSON serialization,
model-input store and model-started receipt). The artifact phase is near-zero
without a durable run. Stages include disabled-branch overhead; a present stage
is not evidence that the respective mode was enabled.

A separate best-effort `model_preparation_timing` event joins by opaque `call_id`.
Its `praxis.pre-model-timing.v1` payload contains only closed stage names,
numeric milliseconds, a sum, and `preparation_observed`. No address, prompt,
exception text, secret, or wall-clock value is copied. Timing never enters
idempotent model-input metadata. First call consumes turn preparation; later
iterations/resumes report call-local phases with `preparation_observed=false`.
A context-local binding restores correctly on exit and nested voice turns.

`measured_total_ms` is **not** ingress-to-provider latency: it excludes upstream
routing/queue/voice-wrapper work, tool-loop work between preparation and call,
this telemetry event's own persistence, and `llm.chat` transport conversion,
provider retries/fallbacks/network. Existing model duration spans additional
work and is not interchangeable. Aggregate phases by joined durable run channel
and serving receipts (owner/group/window/wake); do not guess audience from time
or count disabled stages as a served candidate. No live aggregate was available
in this checkout.

## Concrete input reductions (not output-token limits)

Main voice path:

- `agent.HISTORY_TURNS`: `PRAXIS_HISTORY_TURNS`, default 100, clamped 20..500;
  `_voice_impl` uses `history[-HISTORY_TURNS:]`, reports available/delivered.
- `_build_prompt_parts`: summary tail `SUMMARY_FRAME_CHARS=4000`; explicit
  `PRAXIS_CONTEXT_BUDGET` defaults to 0 (no aggregate evidence omission).
  Positive budget skips whole evidence tiers that exceed persona+dynamic+tiers
  budget; it does not cap the final message, tools, extra runtime evidence or
  extra system suffix. Trace names omitted tiers.
- `_recall_block`: configured automatic recall top-k and canonical/provenance/
  journal exclusions, then `filtered[:recall_k]`. This is selection, not a
  guarantee of full matching memory. Empty query or zero recall disables it.
- Local builders: mention scan last 100 lines, participant pointers first 12,
  loop-state limit with `next_move[:500]`, visit-card fallback `soul[:1200]`,
  channel speaker/title first 500 characters. These are narrower field caps,
  distinct from the voice role-tape window.
- `group_context._format_message`: per-message `max_text` (minimum 80), explicit
  omitted-count/full-message pointer. `context_rows` clamps row limit to
  `MAX_HOT=500` and character budget to `MAX_CONTEXT_CHARS=200000` (minimum 500;
  defaults 80 rows / 20000 characters). It reserves the own branch before
  neighboring branches, uses wide rendering for recent rows then 1200-character
  fallback, and reports dropped rows; this is not a naive single tail slice.
  Group context arrives upstream of `_voice_impl`, so its omissions cannot be
  recovered by increasing HISTORY_TURNS alone. Archive metadata also caps
  topic/sender names at 500 and media description at 1000.

Shadow/candidate (`frame_shadow.py`, inspect constants/functions, not live defaults):

- A emergency window: 200 messages / 120000 rendered characters; oldest rows
  dropped first. Per-message render above 30000 keeps head 20000 + tail 10000
  with explicit cut marker. Fold thresholds 120 messages / 70000 characters;
  tail 50 with minimum 12 and anchored fold receipts.
- T input first 3500 characters with cut marker.
- E address book 4000; recent 5000; lifted 16000; total target 37650,
  total maximum 40000. Degradation order recent → hands → address_book → lifted;
  floors recent/book 1000, lifted 2000. Protected blocks can leave a named
  over-budget condition; HANDS_MAX=6000 is also observed, not a license to drop
  tool schemas. Read `_apply_e_ceiling` and assembly diagnostics for actual degradation.
- Owner fresh-E canary is not full A serving: `frame_serve.select` keeps original
  role messages and tools, builds replacement system, and appends original
  dynamic tail. Shadow A/T bounds must not be described as live message cuts.

Other model paths are distinct: outbound advisor slices privacy frame 1200,
conversation tail 6000, tool trace 3500, prior turns 900, outbound context 12000
in `agent.py`; peripheral pulse portrait/schedule use 1500/600. These are not
main-owner voice truncations. Tool-result rendering and upstream ingest have
additional domain-specific bounds; this audit does not claim every substring
operation in the repository is a model-input truncation. Artifact inline preview
(`inline_chars=512`) does not truncate stored JSON or provider input.

## Cache address is affinity, not access control

`llm.cache_address(model, system_text)` returns
`praxis:<model>:<mark-or->:<room-id-or->`, adding `:<scope>` **only** for technical
Forge marks (`forge_scout`, `forge_worker`, `forge_reviewer`). Recognized structural
`audience_key` wins over prose fallback (`owner`, `room`, `run`, `guest`). First
`room_id=(-?digits)` is used. Technical scope matches `[a-z0-9_-]{1,32}`; ordinary
conversation ignores `cache_scope`. Empty system/no recognized mark or room,
or PRAXIS_CACHE_KEY off/0/no/false yields no explicit key. Different ordinary
owner DMs without a room marker can share an affinity key; it is not keyed by
user ID, full prompt, run ID or epoch. Known non-family group can fall back to
`-:<room>`. First-match scanning of system means embedded markers can influence
affinity; this is not a data-authority boundary.

OpenAI adapter passes the address in `extra_body.prompt_cache_key`; actual
provider prefix hits still depend on input bytes. Anthropic uses ephemeral
cache-control breakpoints on system/persona and final tool schema, not this
explicit address. Moving dynamic suffix does not intentionally change affinity,
but changing candidate/system prefix may still cause a cache miss. Fresh E is
rebuilt by canary each call; a stable affinity key proves neither stale nor fresh
E. Keep address/freshness/content-byte checks separate in rollout evidence.
