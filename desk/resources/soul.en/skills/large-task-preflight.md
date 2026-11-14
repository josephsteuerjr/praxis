# Preflight for a substantial task

Use before changes to durable state, public contracts, external boundaries, several components/trust domains, costly migrations or hard-to-reverse actions. If no implementation is selected yet, start bounded research. Do not burden a small reversible edit with ritual. Existing agreements and authority persist; this check creates no new authority.

## One current record

Maintain one updated state source in Forge checkpoints/notes or an explicitly chosen artifact. Handoffs and continuations reference it instead of producing divergent copies.

Initially record goal, completion criterion, material `out of scope`, current priorities and authority, plan, observed state, limits and reason for preflight. Add evidence, decisions and reasons, current/next step, unknowns and rejected options. Finish with the actual outcome.

Update before an expensive or branching step. After interruption, read handoff and instructions and compare with actual worktree, diff and results. Memory aids navigation, not verification. A chat-only artifact does not complete a handoff when a canonical destination was designated.

## Core questions

1. Cost and priority: safety, integrity, observability, functionality, speed, compatibility and migration. Assess severity and irreversibility separately from frequency. Identify the existing priority that resolves a conflict.
2. Causal scale: a class or one case? What would a local patch hide, and what would a general layer inflate? Record the class, selected scale and specific failure mode of the alternative.
3. Existing solution: find it inside the project and check code, state, documentation or experiment. Similar names do not prove suitability.
4. First miss and recovery: how is harm detected, stopped and recovered? Before migration or a hard-to-reverse action, name backup or its infeasibility, trial/staged rollout, abort signal, last cancellation point and recovery route. Prefer verified rollback; otherwise require containment, bounded blast radius and verifiable forward recovery. If neither recovery nor containment is possible, choose `NO-GO` or an exceptional authorised decision with explicit consequences.
5. Observability: distinguish success, acceptable no-op and mechanism failure without reading source. Which cheap check separates hypotheses? What checks precede completion? Reading an existing artifact often suffices.

## Only relevant modules

- Inferred behaviour: observable signal, dangerous error and discriminating test; assess false positives and negatives separately.
- Declared boundary: authoritative source, scope and executable check. Do not merge authority, data, resources and safety into one nameless flag.
- Merging/separating entities: identify the causal distinction lost or duplication created.
- Capability boundary: known limits and a negative test/expected silence when unintended expansion is dangerous.

## Decision

- `GO`: decisive assumptions are sufficiently supported; residual uncertainty does not change scale, acceptable failure or recovery.
- `GO WITH RESIDUAL RISK`: risk is named, bounded and observable where possible; the current project authority accepts consequences and knowledge limits.
- `NO-GO`: reject the method, recording reason, evidence and reconsideration condition.
- `BLOCKED`: identify the missing fact/capability and unblocking condition.
- `ESCALATE`: identify the question, authorised decision-maker and insufficiency of current authority/evidence.

Admission permits only the reviewed plan; it does not prove success. Revisit after changed diff/scope, a new boundary, failed assumption, expanded blast radius or changed recoverability. Naming uncertainty alone does not authorise an irreversible action. An unchanged plan supported by evidence is a valid result.

Per iteration choose the nearest cheap discriminating check and specify beforehand how its outcome changes the decision. Then update the record. Several inconclusive rounds require bounding research, identifying a missing fact or escalating; three is a heuristic, not grounds to abandon authorised work.

An independent second pass helps with migration, irreversibility and multiple trust domains when current instructions and budget allow. Supply a neutral package without imposing a hypothesis; compare concrete differences, not generic verdicts. Never invoke a subagent against a prohibition. State unavailable review honestly: bounded harm with verified recovery may leave residual risk; authority conflict or unobservable irreversible harm calls for blocking/escalation.

The record retains the initial snapshot, decision, plan delta, evidence, preselected check outcomes, recovery and unknowns. Establish recurring failure classes through independent comparable cases, not a magic count.

Method provenance: Ivan's `CHECKLIST.md` in `konradkarso-droid/agent-mobile`. This is an adapted portable edition, not an automatically synchronised copy.
