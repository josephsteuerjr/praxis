# Durable run cold-retention foundation

`run_retention.py` defines two versioned, JSON-safe contracts. It does **not** copy,
archive, truncate, or delete anything.

## Archive locator v1

A locator is a sidecar object; existing `praxis.result-ref.v1` events stay unchanged.
It binds one exact result body to one exact archive object:

```json
{
  "schema": "praxis.run.archive-locator.v1",
  "run_id": "run-…",
  "evidence_kind": "result",
  "evidence_id": "result-0001",
  "source_path": "results/0001-tool.log",
  "body_sha256": "64 lowercase hex characters",
  "body_size": 123,
  "object_key": "runs/sha256/…",
  "object_sha256": "64 lowercase hex characters",
  "object_size": 123,
  "archived_at": "2026-09-12T00:00:00Z",
  "format": "praxis.run-evidence-object.v1"
}
```

`object_key` is a normalized relative POSIX path beneath a caller-supplied archive
root. Absolute paths, backslashes and traversal are rejected. `source_path` must be a
strict `results/NNNN-name.log|bin` path. v1 stores exactly one body per object, so its
body and object digests/sizes must agree; keeping both identities explicit permits a
future container format without weakening ResultRef binding. `ArchiveLocator.verify()`
materializes bounded bytes and accepts them only when both object values match. A reader must
also match run id, result id, source path, body digest and body size against the
original ResultRef before serving bytes.

The contract deliberately does not treat a provider ETag as a cryptographic checksum
or claim that the hot source may be deleted. Unknown schemas, formats, missing/extra
fields, and checksum disagreement fail closed. A future copier must durably publish a
verified object and locator before any separately approved source reclamation.

### Authoritative result reader

`RunManager.read_result()` is the narrow archive-aware adapter used by
`read_run_result` and by the resume/provenance integrity readers. Hot result bodies keep
legacy behavior. If a hot body is absent, the adapter looks only for
`archive-locators/<canonical-result-id>.json` beside the retained run manifest/events,
then binds every locator identity field to the immutable ResultRef in `events.jsonl`
before reading an object. The archive root is configured explicitly with the
`archive_root=` constructor argument or `PRAXIS_RUN_ARCHIVE_ROOT`; it is never inferred
from an object key. Object bytes are checksum verified before cursor data is returned.

If a valid locator exists but the root/object is unavailable, the reader raises
`ArchivedResultUnavailable`, whose `locator` attribute contains the validated locator
(and whose message carries JSON for the historical text-only tool wrapper). It never
pretends that inline ResultRef text is the complete body. Invalid/conflicting locators,
ResultRefs, traversal, and archive symlinks fail closed. ResultRef v1 and its event remain
unchanged.

## Dry-run inventory v1

Run from the repository/runtime base:

```sh
python run_retention.py --base /opt/praxis --min-age-days 30
```

The command prints JSON and performs only reads. Its report includes a per-run
verdict and observed bytes split into `manifest`, `events`, `context`, `recap`,
`results`, `artifacts`, and `other`. Only directories with manifests at
`memory/runs/*/*/manifest.json` are considered.

A run is a candidate only when all conditions hold:

1. manifest status is `done`, `cancelled`, or `failed` (never `in_doubt`);
2. `terminal_at` is valid, timezone-aware, and at least the requested age;
3. no current non-terminal `memory/work/tasks/*/TASK.md` or active/latent/blocked
   desire state refers to its run id or run evidence;
4. it was not supplied with `--open-run-id` by another authoritative evidence owner;
5. its tree is readable and contains no symlink.

Malformed manifests and unknown terminal times are excluded. Conflicting ResultRefs for the
same result id or source path exclude the run, and open-evidence run ids are recognized as
standalone mentions in ordinary prose as well as structured paths. If open-evidence state
exists but cannot be parsed, all otherwise eligible runs are excluded: unreadable
state is not evidence that references are closed. Inventory results are advisory and
must be regenerated against a quiescent/locked source by any future archiver; this
read-only scan intentionally does not take the shared run writer lock.

## Compatibility proof scope

`test_run_retention.py` copies synthetic bytes representing one result from an old
terminal run into a cold object, verifies a round-tripped v1 locator, and asserts the
old source remains. It also snapshots all fixture files around inventory and proves
byte-for-byte that the scan neither writes nor deletes. No live/private run is copied
by this repository test.

## Direct-reader compatibility inventory and explicit gaps

Only ResultRef-addressed result bodies are candidates in v1. Per-run
`bytes_by_class` counts observed hot bytes; summary `reclaimable_bytes_by_class`
counts **only strict, checksum-verified, reader-size-bounded, WAL-addressed result
bodies**. Unaddressed files under results are retained too. Candidate bytes are not proof that a cold
copy exists or permission to delete. Manifest schema must be `praxis.run.v1` and
`context.run_id` must equal the directory name. Symlink run directories/ancestors
are rejected. WAL sequences must be exactly one-based, unique, contiguous, and
complete through `manifest.event_seq`; malformed, missing, duplicate, gapped, unterminated, or
unpublished-tail rows exclude the run rather than trusting stale terminal status
after a crash. The complete WAL is also reduced for status-bearing events; a terminal
manifest whose WAL resolves to a different or non-terminal state is excluded.

The no-follow safety boundary requires POSIX `openat`/`dir_fd` semantics plus
`O_DIRECTORY`, `O_NOFOLLOW`, and `O_NONBLOCK`. Platforms lacking those capabilities
fail closed with an explicit `ContractError` (inventory/object verification) or
`RunError` (archive result lookup); there is no check-then-open compatibility fallback.

| Reader / path | Evidence consumed | v1 compatibility / gap |
| --- | --- | --- |
| `RunManager.read_result`, `agent.tool_read_run_result` | ResultRef body and byte/line cursors | Archive-aware, immutable event binding; unavailable objects honestly return locator via exception |
| `run_resume.RunManagerReader`, `read_full_result_bytes`, `read_full_json_result`, resume planning | Result/provenance replay | Uses authoritative reader; final assembled hash/size verification remains |
| `praxis_app` server receipt replay | Durable result body | Uses authoritative reader |
| `keat_source.adapt_model_input` | Externally pinned manifest/events/model-input bytes | Offline adapter does not fetch archives or self-pin. Upstream must supply exact bytes through authoritative reader; no automatic direct-path fallback claimed |
| `RunManager` discovery/live listing/recovery, `_manifest_locked`, event replay | Manifest, events, result/artifact numbering | Retain hot manifest/events and sequence metadata; no archived-run-directory discovery |
| `RunManager` artifact readers/writers and RECAP | Artifacts, RECAP | No archive adapter; retain |
| `canary._manifests` | Manifest-only health/status | Retain hot manifests |
| `frame_stats.collect/calls_from_run` | Events | Retain hot events |
| `memory_catalog._render_runs` | Manifest/run/events/RECAP links | Retain link targets |
| `memory_fts.iter_sources`, `_audit_transport_hits` | Context/artifacts/events and recall/audit evidence; result subtree pruned | No cold recall indexing; retain direct-read classes |
| `praxis_app.revision_hint`, run detail, artifact download | Manifest stats, RECAP, artifacts | No cold adapter; retain |
| `agent._load_exact_run_channel` | Hash-bound context.md | No cold adapter; retain exact context |
| `agent` recap promotion/finalization and artifact staging/delivery | RECAP/artifacts | No cold adapter; retain |
| `computer_memory._forge_task_dir` | Run-local computer receipts/evidence directory | Preserve hot run location and computer evidence |

All address sidecars (`archive-locators`), context, manifest, events, RECAP,
artifacts, computer receipts, locks and other metadata remain non-reclaimable.
This is a source-reader inventory, not a claim that every UI/offline consumer has
been tested with cold bodies. No general filesystem redirect or bulk archival is
implemented.

### Verification/materialization safety

`ArchiveLocator.verify()` now returns bounded verified **bytes**, not a pathname.
It traverses components with descriptor-relative no-follow opens, opens the object
once, requires a regular file, checks declared size before reading, and enforces
both declared size and a default 64 MiB maximum while streaming. Readers cursor over
those exact verified bytes, including when `verify_sha256=False`; they never reopen
an object after verification. Larger objects are honestly unavailable through this
foundation, pending a separately bounded streaming-reader design. Canonical ids
are `result-0001` (minimum four digits), and source filename numbers must match.
