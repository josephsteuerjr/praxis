# Edition compatibility and retained test scenarios (2.3)

This file describes the composed `praxis/` + `helene/core/` contract. It does not
claim current installation, full-suite acceptance, relay-binary qualification or
provider delivery. Tests use disposable synthetic profiles and offline SDKs.

## Explicit compaction deployment

`compaction_migration.apply` remains an **explicit** deployment CLI. Profile
parsing never invokes it. `rooms` recognizes `compaction_policy` and
`compaction_set_by` as provenance headers and retains them on normal writes;
mode, authored body and other recognized headers keep their existing semantics.
The append-only deployment ledger writes bytes in binary mode on Windows too,
including recovery of a complete foreign record missing just its final LF.

`build_state_evidence_block` and `_build_prompt_parts` default
`include_deployment_notice=False`. Probe/system-only/frozen-epoch preview builds
therefore leave the pending deployment notice untouched. The intended voice
input opts in. `context_once` serializes first and then acknowledges
`context_constructed_not_delivered`. This is not a receipt for inference,
post-budget inclusion, Telegram delivery or reading. It never runs a migration.
The canonical explanation/rollback remains in the ledger after construction.

Inherited `test_compaction_migration` retains the real CLI's profile grammar,
topic route, provenance, idempotence/resume, short/torn/foreign/corrupt append,
quarantine failure, zero-progress, serialization-failure and invalid-metadata
cases. `test_migration_prefreeze_2210` exercises the real frozen wake and voice
input rather than a disconnected preview helper.

## Canonical effort versus explicit relay policy

`REASONING_EFFORTS` is canonical: none, minimal, low, medium, high, xhigh, max.
GLM uses max; direct Responses preserves max. Explicit thinking still overrides
the role setting. Account configuration remains round-trippable, and changing
reasoning through the authored brain hand still needs its reason and principal.

Only an account explicitly configured with `provider=relay` and OpenAI wire
protocol receives the local six-tier integration policy
`embedded-relay-local-six-tier-v1`. `max` there raises
`UnsupportedReasoningEffort` **before client preparation and SDK sending**;
an attempted unsupported relay request is not silently downgraded, stripped or
retried through automatic fallback. This is an endpoint-attempt policy, not a
global ban on canonical max: if the primary is already held and not attempted,
the explicitly configured distinct direct/GLM fallback may send canonical max.
A real-SDK regression proves the held-primary path sends only to the configured
fallback, preserves max and records its effective account. A critic proposed an
early logical-call ban; that overbroad interpretation was set aside because it
would suppress a supported, authorized fallback without any relay attempt.
An unknown compatible endpoint retains its canonical behavior even with exactly
the same loopback URL. URLs, model slugs and wire protocols do not identify a
bundled relay. The six-tier policy agrees with the inspected Rust source; it is
not measured capability evidence for a retained donor binary. Actual binary
qualification remains separate.

`test_endpoint_policy_230` uses actual installed OpenAI/Anthropic SDK request
serialization and SSE parsers through `httpx.MockTransport`: six relay sends,
max refusal before SDK, generic-compatible max, direct Responses max, GLM max,
and explicit-thinking precedence. It uses no socket, live service or model.
Existing desk SDK tests remain applicable; none is replaced by metadata alone.

## Exact-account fallback

`brain.switch` and `apply_profile` retain the previous exact account ID as
fallback when accounts change. This is essential when two accounts share the
same protocol. The old assertion that `fallback_framework` must disappear was
a historical representation oracle, not the routing invariant. Edition tests
keep actual old-account/model resolution, same-account preservation, complete
handshake rollback and no empty-string pin; new tests also exercise same-wire
cross-account fallback dispatch. Owner capability text names primary and
fallback account/protocol/model without keys. Public capability text does not
acquire this owner-only route description.

`LLMResponse.framework` retains its wire-protocol meaning. Optional appended
`account_route` is the LOCAL actually selected configured connection identifier,
not provider-reported physical credential/account identity. `chat` attaches it
from the successful primary/fallback attempt's local variable; it never rereads
current configuration after a response. FIRST observation uses this fact with
the returned model, falling back to the old protocol field for legacy response
fakes lacking it. A real named-alias SDK→voice→dispatch/observe regression
previously reset every second turn (`provider_route_changed`); it now preserves
the exact FIRST prefix. A concurrent role switch during actual SDK handling
cannot change the response's recorded connection. Fallback and returned-model
mismatches remain truthful and can start a new FIRST seed.

## FIRST test ports (not server-signature compatibility)

`test_frame_continuation` now constructs real source occurrences and binds them
with `bind_source`. It uses `dialogue`, `prepare`, `dispatch`, `observe` and the
actual voice/model boundary. No ignored `selection`, `kind` or fake epoch kwargs
were added to the product.

| Historical scenario | Retained current assertion / deliberate changed oracle |
|---|---|
| next turn + multiple pending lines | Exact successful FIRST message prefix survives LF-vs-LF/LF historical regrouping; new evidence appends. |
| arrivals before actual reply | New accepted source occurrences append in order; actual accepted reply is present. |
| silent or failed assistant turn | Newly admitted human input may append to FIRST. No assistant/draft is invented; old `reused==0` oracle is retired. |
| source edit, delete, reorder, equal-text different ID | Changed exact source identity resets with explicit reason, not text deduplication. |
| fold, flip, scope, summary policy/text, source installation | Generation identity resets; old frozen payload is not imported. |
| system, tools, model/account, hands, render policy, epoch document | Binding reset; no cached incompatible prefix. |
| room, audience, actor, installation | No cross-room import; changed admission/audience/principal starts fresh. |
| DM and internal worker | Proven source-bound DM is supported. Unbound worker/background cannot borrow an outer chat; old all-DM-decline oracle is retired. |
| KEAT, image/non-text, mismatching projection | Explicitly declined current continuation path; not promoted to qualified image/KEAT coverage. |
| default/off | Unset-variable default on is exercised; explicitly off performs no cache read/write. |
| malformed cache + failed atomic write | Fresh canonical input retained, no temporary artifact remains. |
| substituted/mutated/critical/system-changed input | No checkpoint for unissued input. |
| process restart / private cache | Reopened exact checksum/messages; POSIX mode checked only on POSIX (Windows has no corresponding mode). |
| old wake / new fold | Older checkpoints cannot replace later compatible ones or cross a new generation. |
| nested background binding | `bind(None)` masks the outer plan; no borrowed observation. |
| real model/tool loop | Checkpoint holds actual first input, not later private tool trace or undelivered draft; real accepted next reply appears. |
| provider failure | No checkpoint on failure. |
| voice builder | Two actual voice-to-model invocations preserve exact FIRST under matching effective provider identity. |

The broader epoch/frozen-summary/source-selection repair has a separate owner.
These ports do not qualify all Telegram intake, images, KEAT or a paid provider.

## Retired source skips, per-scenario verdict

No new platform or blanket skip list was introduced. The old source is retained
in Git and in this task's code-only preimages for independent comparison.

### `test_compact_refresh`: skipped V1 automatic-generator hierarchy

Removed `Base` and its five skipped derived classes, along with `REFRESH_QUIET_SEC`,
`dry_run`, model-call price planning and blocked-automatic-retry assertions. That
API would resurrect the retired helper generator. The current authoring path
uses `prepare_authored_fold` then `apply_authored_fold(selection, text)`.

| Old test(s) | Current executable replacement / verdict |
|---|---|
| stale leaf debt closes | `test_stale_debt_closes_only_after_authored_text`; preparation alone leaves debt. |
| old audit file + distinct successor | `test_old_bytes_survive_and_successor_is_distinct`; exact old bytes remain. |
| frame receives new recap | `test_only_new_recap_reaches_frame`; stale recap stays out. |
| no event rehot/disappears | `test_no_current_event_disappears_or_becomes_hot_twice`; exact canonical coverage/hot disjointness. |
| superseded body never reaches model | `test_current_sources_never_reintroduce_superseded_body`; current source packet only, no helper model call. |
| deletion bounded equally | `test_deleted_body_is_absent_from_prepare_and_frame_but_audit_remains`. |
| default dry-run, price before paying | Generator/price oracle retired; ordinary prepare persists an exact selection, creates no new compact/helper generation and lists complete selected sources plus remaining count. Existing orphan reconciliation may append a completion receipt. Legacy refresh explicitly refuses without generation/writes. |
| silent helper + blocked retry | Generator oracle retired; empty authored text refuses without replacement/receipt/debt change; an explicit authored retry works. No automatic retry exists. |
| huge leaf, several successors | Row-quota test retains actual 4→2→0 progression. Separate 60×900-character leaf crosses the real 48,000-character selection bound with permissive row quota; all exact sources are covered once across distinct successors. |
| parent costs no model call | `test_parent_is_repaired_from_current_sources_not_generated_from_old_recap`; author receives current sources, no old recap/helper generation. |
| second run does nothing | `test_consumed_selection_cannot_rewrite_or_duplicate_work`; no file/receipt change. |
| unknown versus clean + leaf/parent/file counters | V1 known/tree/parsed/broken certificate retired. Current exact covered/presentable counts and unresolved group/sample identity distinguish evidence present from absent; absence does not certify known-clean. |

### `test_frame_shadow`: unshipped switch placeholder

Removed only the unconditional skipped `test_the_live_frame_equals_the_shadow_byte_for_byte`.
It referenced unshipped `agent.frame_for` and demanded future `GOLDEN=live==shadow`.
The current product explicitly retains `shadow-only`. The separate new guard
`test_live_voice_keeps_shadow_capture_observational` invokes the actual voice
builder with synthetic persona and verifies one shadow capture cannot replace
provider-bound live system/messages. Other shadow tests remain, including
serializer byte identity, lawful build, audience and no-model-delivery negatives.
This is retirement of a future switch proposal, not proof of that switch.

### `test_say_hand`: private persona assertions

Removed `_sanitized_tree`, its conditional skip and fixed-private-profile class
`TheNormLivesInASkillSheOwns`. A user's mutable soul is not a package test input.
Replacement `TheNormIsAuthoredNotARequiredPrivateProfile` uses a temporary
synthetic skills directory and the real `tool_write_skill`: file and index,
explicit rewrite with no duplicate index row, and retainable honest noncheck
text. No owner soul is read or copied into a verifier or public artifact. The
public application starter skill is a distinct resource/seed contract, not an
assertion that a live agent must retain its original words. The added actual
`desk/tests/t_base_skills_1510` case seeds that public resource into a temporary
profile, checks stock bytes/index and both phrases, then proves a later authored
replacement survives seeding/refresh. No private source persona is copied.
