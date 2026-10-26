# Source admission for the 2.1.1 transition

The old 2.1.1 updater calls its own source planner before it can launch an incoming helper. On a conflict it discards the extraction and returns. Shipping a new helper alone cannot change that gate.

This release declares a compatibility view in `source-admission.json`. Its packaged `tree/` is the exact official 2.1.1 baseline for the target platform, with byte and mode inventories taken from the verified public source snapshot. The real incoming source **B** remains in `code-upstream.zip` and `code-upstream.json`, bound by SHA256. The compatibility view is an admission input, never the accepted merge result and never the next official baseline.

The old planner sees identical baseline/incoming views and carries authored **L** unchanged. Its successful return admits custody only. The incoming helper then verifies and materializes real B before preparing the real A/L/B/M source plan, restricted merger, independent frozen review and recoverable overlay. Original conflicts and semantic repairs still require canonical review; the compatibility result cannot satisfy that review.

`source_admission.activate` records durable intent before directory replacement. It verifies every upstream member and the complete declared view; unexpected authored or linked paths are refused. Interrupted directory promotion can resume from its verified previous tree. Program passports are derived from verified B and extraction proofs for changed paths are invalidated. A completed marker prevents a later startup from overwriting authored edits. Temporary view/recovery trees stay outside the program overlay.

The new updater materializes declared source before preflight. A fresh application does so before controller/core imports. The old 2.1.1 path transfers custody and closes its old owner through its existing protocol; the incoming observer remains visible during preparation. This does not claim that every historical updater/baseline combination has been qualified. The supported 2.1.1 baseline and actual one-invocation transition are explicit release gates.

Release construction must keep both inventories truthful: the package manifest describes the shipped admission view; `code-upstream` describes real pristine B. Platform source tags bind real core source separately from the admission view. Do not substitute authored code for either published snapshot or manufacture a ready/review receipt to admit an upgrade.
