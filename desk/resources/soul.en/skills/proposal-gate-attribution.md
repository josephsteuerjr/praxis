# Attribute a red gate causally

A failing check may come from the diff, baseline, environment or worktree location. Before any authorised override, establish each failing family's cause and retain the red evidence.

Diagnostic examples, not claims of a current defect:

- `_SKIP_DIRS` excludes `.proposals` while the worktree itself is inside that directory. Then `_corpus()` may be empty. Inspect the actual file set; if needed, compare the same frozen commit in an isolated path outside the exclusion.
- `rails.manifest_drift()` compares a manifest with live values. A red clean checkout may inherit an inconsistent baseline. Check HEAD, contents and initialisation; do not regenerate documents from a partial process just to obtain green.

Procedure:

1. Retain command, HEAD, roster, original log and non-secret environment.
2. Compare the same test on the exact baseline in a separate test environment with the same runner.
3. If unclear, test one discriminating hypothesis: path, discovery, fixture state or environment.
4. Retain exact ids and the causal result difference. Baseline RED does not become an overall PASS.

`praxis_test.py` supplies its test contract; bare unittest or pytest may not be equivalent. An official runner still requires reading the outcome.

Override requires the current procedure and authority, stated reasons and risks; matching the baseline alone does not authorise merging. Do not modify a live project for comparative testing.
