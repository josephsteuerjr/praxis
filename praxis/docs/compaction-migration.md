# PR10: explicit authored-only compaction migration

This is a deliberate **global** removal of helper compaction, not a group-only
fallback fix. The release keeps the owner-authorized tool schema budget **32000**.
The policy is `authored-only`: pressure offers work; only Praxis's authored
`memory_compact prepare → fold(selection, text)` writes a compact.

## Deploy (not run by this patch's tests)

1. Finish verification/review of the exact release commit. Stop/quiesce the runner
   and all room-profile writers. Back up the pre-deploy release and canonical
   `memory/rooms/*.md`, `memory/policies/compaction.md` (including absence), and
   `memory/deployments/`. Do not overwrite soul or personal memory.
2. From the new release run:
   ```sh
   python compaction_migration.py --base /opt/praxis --sha FULL_40_CHAR_RELEASE_SHA \
     --reason 'PR10 deliberately removes helper compaction globally; authored-only folds'
   ```
   This explicitly stamps all existing numeric root and canonical `__topic__<positive-id>` room profiles and the global
   `memory/policies/compaction.md` (DMs, background, future rooms). Neither SQL nor
   parse-time default delivers the policy. The global Markdown is canonical policy
   documentation, not a toggle that can re-enable removed code.
3. Check `memory/deployments/compaction.jsonl`: one `planned`, one `applied`, honest
   `deploy:<sha>` authorship and reason. Interrupted runs resume their original
   plan; completed runs do not overwrite later authored changes or repeat receipts.
4. The real prompt path opts in with
   `build_state_evidence_block(include_deployment_notice=True)`, which calls
   `compaction_migration.context_once(BASE, render=...)`. Ordinary builder probes
   default to no consumption. The renderer serializes the complete state JSONL
   **before** recording `context_shown`; serialization failure leaves it pending.
   Construction and acknowledgement are serialized under the migration lock.
   The line includes the change, reason and rollback path, once across restarts.
   `context_shown` explicitly records `context_constructed_not_delivered`.

   This acknowledges **state evidence construction only**, not model inference or
   Telegram delivery. Unlike the earlier claim-before-render version it does not
   lose the notice on builder serialization failure; like existing `core.notices`
   it is not a transport acknowledgement. Subsequent prompt-building errors, context
   budget pruning of the continuity tier, request failures, or a crash after receipt
   can still lose the insertion. The full explanation remains canonical in `applied`
   for explicit recall. Do not claim exactly-once delivery or that Praxis saw it.
   A deployment requiring proof of actual visibility must inspect the final model
   request/turn evidence separately; fixture tests cannot prove live delivery.

## Rollback

Stop/quiesce the runner; restore the **pre-deploy release** and the affected
profiles from that deployment's backup, preserving the append-only deployment
ledger as history. Merely setting a label to `helper` cannot restore deleted code.
Retain raw archives and authored compacts; no soul rewrite, SQL reconstruction,
or bulk memory replacement is required. Reapplying this same migration after a
rollback intentionally does not re-deliver or overwrite profiles: a new behavior
transition needs a new explicit migration, not deletion of its receipt.

## Scope and verification

Fixtures only: `python praxis_test.py test_compaction_migration -v`.
Migration is explicit CLI-only; runtime reads never perform writes to profiles.
Deployment must run the CLI against its real release SHA before starting. The
patch itself does not apply live migration, restart services or prove live delivery.

## Ledger crash recovery and limits

`apply` and `context_once` share the in-process lock and inter-process
`compaction.lock`. Recovery occurs under those same locks, before interpreting
receipts or modifying profiles. All ledger writers must honor this lock; deployment
still requires quiescing profile writers. This is not protection against external
writers ignoring the lock, arbitrary disk corruption, or an unreliable filesystem.

- Appends retry interrupted and short `os.write` calls until the complete UTF-8
  object plus newline is written; zero progress fails rather than looping forever.
  Successful append fsyncs the file and, on POSIX, its parent directory.
- Every newline-terminated record must decode as a UTF-8 JSON object. **Any**
  malformed complete record, including the final complete line, fails closed;
  no receipts or profiles are changed. Foreign-migration records are validated
  too, never filtered out before validation or rewritten.
- A valid final object without a newline is retained byte-for-byte and receives
  its missing delimiter. An invalid unterminated final suffix is treated as an
  interrupted append, not committed history. Before truncating **only that suffix**,
  recovery durably saves its exact bytes beside the ledger as
  `compaction.jsonl.torn-<byte-offset>-<sha256>`. Recovery reuses a matching quarantine
  after interruption; backup failure stops recovery without truncating the ledger.
  Quarantine files may contain incomplete canonical record data: keep them with
  the deployment backup under the same privacy/access restrictions, not in source.
- All complete committed records, including foreign migrations, remain byte-for-byte
  in place. The authoritative ledger is never regenerated from filtered rows or SQL.
  A torn `planned` record has no committed attribution and can be planned on retry;
  a committed plan retains its original actor/reason through a torn `applied` append.
  A torn `context_shown` append leaves the notice available for reconstruction.

The format cannot distinguish a torn final suffix from arbitrary corruption confined
exactly to that suffix. It deliberately quarantines rather than silently drops that
uncertainty. Complete-line corruption requires explicit operator investigation;
never delete valid receipts to make migration run again. POSIX directory fsync is
used; Windows has no directory fsync here. Fixtures simulate write interruption and
retry, not machine power loss or filesystem durability guarantees.

Focused repair evidence (temporary fixtures only): the new regression tests failed
against the old single-write/parser implementation (short write and all three torn
append stages); after repair `python praxis_test.py test_compaction_migration -q`
runs **18 tests OK**, with zero external network attempts. Cases include preserving
foreign history and original attribution, restart/reload, valid missing delimiter,
malformed complete/middle lines, partial UTF-8 suffix, quarantine failure/retry,
short writes, EINTR and zero progress. These tests do not prove live deployment or
actual model visibility; the construction-only notice limitation above is unchanged.
