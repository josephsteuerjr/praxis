"""Cold-retention contracts and a read-only inventory for durable runs.

This module deliberately has no archive/delete operation.  Its inventory only reads
metadata and file sizes; a candidate verdict is therefore advice to a later, separately
audited copier, never permission to unlink anything.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import hashlib
import json
import os
import stat
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

ARCHIVE_LOCATOR_SCHEMA = "praxis.run.archive-locator.v1"
MAX_ARCHIVE_OBJECT_SIZE = 64 * 1024 * 1024
INVENTORY_SCHEMA = "praxis.run.retention-inventory.v1"
ARCHIVE_FORMAT = "praxis.run-evidence-object.v1"
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})
DESIRE_TERMINAL_STATUSES = frozenset({"satisfied", "released"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_OPENAT_SUPPORTED = (
    os.name == "posix"
    and all(getattr(os, flag, None) is not None
            for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
)


class ContractError(ValueError):
    """An archive address is malformed or does not verify."""


class EvidenceNotFound(ContractError):
    """The final evidence name was absent beneath an opened safe parent."""


def _require_safe_openat(feature: str) -> None:
    """Fail closed when descriptor-relative no-follow reads are unavailable."""
    if not _SAFE_OPENAT_SUPPORTED:
        raise ContractError(f"{feature} requires POSIX openat/no-follow support")


@contextlib.contextmanager
def safe_evidence_stream(path: Path):
    """Open a regular evidence FD without following any path component.

    NONBLOCK precedes fstat so a replacement FIFO cannot stall the reader.
    Callers must bound reads; the descriptor pins the checked object.
    """
    _require_safe_openat("evidence read")
    directory = descriptor = None
    try:
        parts = Path(path).absolute().parts
        if ".." in parts:
            raise ContractError("evidence path contains parent traversal")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(parts[0], flags)
        for part in parts[1:-1]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        except FileNotFoundError as exc:
            # Only final-component absence may trigger archive fallback. Missing
            # or replaced ancestors are unsafe rather than evidence of a cold body.
            raise EvidenceNotFound(f"evidence unavailable: {exc}") from exc
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ContractError("evidence must be a single regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            yield stream
    except (OSError, NotImplementedError, TypeError) as exc:
        raise ContractError(f"evidence unavailable: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def read_evidence_bytes(path: Path, *, max_bytes: int = MAX_ARCHIVE_OBJECT_SIZE) -> bytes:
    """Bound memory even if a regular evidence file grows during the read."""
    with safe_evidence_stream(path) as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ContractError("evidence exceeds byte budget")
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _strict_json_loads(value: str) -> Any:
    """Decode RFC JSON rather than Python's permissive JSON extensions."""
    return json.loads(value, object_pairs_hook=_unique_json_object,
                      parse_constant=_reject_json_constant)


def _safe_relative(value: str, field: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or ".." in path.parts
            or "." in path.parts or "" in path.parts):
        raise ContractError(f"{field} must be a normalized relative POSIX path")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class ArchiveLocator:
    """Address of one byte-exact archived evidence body.

    v1 stores exactly one body per object. The explicit ResultRef-facing body
    identity leaves later container formats room for a distinct object checksum.
    """

    run_id: str
    evidence_kind: str
    evidence_id: str
    source_path: str
    object_key: str
    body_sha256: str
    body_size: int
    object_sha256: str
    object_size: int
    archived_at: str
    format: str = ARCHIVE_FORMAT
    schema: str = ARCHIVE_LOCATOR_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ARCHIVE_LOCATOR_SCHEMA:
            raise ContractError(f"unsupported archive locator schema: {self.schema!r}")
        if not _RUN_ID.fullmatch(self.run_id) or self.run_id in {".", ".."}:
            raise ContractError("invalid run_id")
        if self.evidence_kind != "result":
            raise ContractError("v1 supports only result evidence")
        match = re.fullmatch(r"result-([0-9]+)", self.evidence_id)
        if (match is None or int(match.group(1)) < 1
                or self.evidence_id != f"result-{int(match.group(1)):04d}"):
            raise ContractError("invalid evidence_id")
        object.__setattr__(self, "source_path", _safe_relative(self.source_path, "source_path"))
        if not re.fullmatch(r"results/[0-9]+-[A-Za-z0-9_.-]+\.(?:log|bin)", self.source_path):
            raise ContractError("source_path is not a strict result body path")
        number = int(self.source_path.split("/")[1].split("-")[0])
        if number != int(match.group(1)) or not self.source_path.startswith(f"results/{number:04d}-"):
            raise ContractError("source_path number must match canonical evidence_id")
        object.__setattr__(self, "object_key", _safe_relative(self.object_key, "object_key"))
        if not _SHA256.fullmatch(self.body_sha256) or not _SHA256.fullmatch(self.object_sha256):
            raise ContractError("checksums must be 64 lowercase hexadecimal characters")
        if (type(self.body_size) is not int or self.body_size < 0
                or type(self.object_size) is not int or self.object_size < 0):
            raise ContractError("sizes must be non-negative integers")
        if self.format != ARCHIVE_FORMAT:
            raise ContractError(f"unsupported archive format: {self.format!r}")
        if self.body_sha256 != self.object_sha256 or self.body_size != self.object_size:
            raise ContractError("v1 object must be the exact evidence body")
        _timestamp(self.archived_at)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchiveLocator":
        if not isinstance(value, Mapping):
            raise ContractError("archive locator must be an object")
        required = {"schema", "run_id", "evidence_kind", "evidence_id", "source_path",
                    "object_key", "body_sha256", "body_size", "object_sha256",
                    "object_size", "archived_at", "format"}
        if set(value) != required:
            raise ContractError("archive locator fields must be exactly: " + ", ".join(sorted(required)))
        return cls(**{key: value[key] for key in required})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "run_id": self.run_id,
            "evidence_kind": self.evidence_kind, "evidence_id": self.evidence_id,
            "source_path": self.source_path, "object_key": self.object_key,
            "body_sha256": self.body_sha256, "body_size": self.body_size,
            "object_sha256": self.object_sha256, "object_size": self.object_size,
            "archived_at": self.archived_at, "format": self.format,
        }

    def resolve(self, archive_root: Path) -> Path:
        return Path(archive_root).joinpath(*PurePosixPath(self.object_key).parts)

    def verify(self, archive_root: Path, *, chunk_size: int = 1024 * 1024,
               max_size: int = MAX_ARCHIVE_OBJECT_SIZE) -> bytes:
        """Materialize bounded verified bytes from a single no-follow regular FD.

        Never return a verified pathname: reopening it would lose the checksum
        guarantee. Directory traversal is descriptor-relative, including the root.
        """
        _require_safe_openat("archive verification")
        if chunk_size <= 0 or max_size < 0 or self.object_size > max_size:
            raise ContractError("archive object exceeds size budget")
        digest = hashlib.sha256()
        payload = bytearray()
        directory = None
        descriptor = None
        try:
            parts = Path(archive_root).absolute().parts
            directory = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in (*parts[1:], *PurePosixPath(self.object_key).parts[:-1]):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(PurePosixPath(self.object_key).name,
                                 os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ContractError("archive object must be a regular file")
            if info.st_size != self.object_size:
                raise ContractError("archive object size/checksum mismatch")
            while True:
                block = os.read(descriptor, min(chunk_size, self.object_size - len(payload) + 1))
                if not block:
                    break
                if len(payload) + len(block) > min(self.object_size, max_size):
                    raise ContractError("archive object exceeds declared size/budget")
                payload.extend(block)
                digest.update(block)
        except (OSError, NotImplementedError, TypeError) as exc:
            raise ContractError(f"archive object unavailable: {exc}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
        if len(payload) != self.object_size or digest.hexdigest() != self.object_sha256:
            raise ContractError("archive object size/checksum mismatch")
        return bytes(payload)


def _timestamp(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("timestamp is required")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ContractError("timestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def _refs_run_ids(value: Any) -> set[str]:
    """Conservatively extract standalone run ids from paths and ordinary prose."""
    found: set[str] = set()
    values = value if isinstance(value, (list, tuple, set)) else (value,)
    pattern = re.compile(
        r"(?<![A-Za-z0-9_.-])(run-[A-Za-z0-9_]+(?:[.-][A-Za-z0-9_]+)*)(?![A-Za-z0-9_.-])"
    )
    for item in values:
        found.update(match.group(1) for match in pattern.finditer(str(item or "")))
    return found


@contextlib.contextmanager
def _discovery_directory(path: Path):
    """Pin each discovery ancestor; never traverse a symlink by pathname."""
    _require_safe_openat("open-evidence discovery")
    descriptor = None
    try:
        parts = Path(path).absolute().parts
        if ".." in parts:
            raise ValueError("discovery path contains parent traversal")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(parts[0], flags)
        for part in parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    except (NotImplementedError, TypeError) as exc:
        raise ContractError(f"open-evidence discovery requires POSIX openat support: {exc}") from exc
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise ContractError(f"open-evidence discovery requires POSIX openat support: {exc}") from exc
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def discover_open_evidence_runs(base: Path) -> tuple[set[str], list[str]]:
    """Read current work/desire projections and return runs still needed by open work.

    Parse/read failures are reported to the caller.  Inventory then excludes every
    otherwise eligible run because absence of readable open-evidence state is not proof
    that evidence is closed.
    """
    base = Path(base)
    result: set[str] = set()
    errors: list[str] = []
    try:
        _require_safe_openat("open-evidence discovery")
    except ContractError as exc:
        return result, [f"compatibility: {exc}"]
    work_root = base / "memory" / "work" / "tasks"
    cards: list[Path] = []
    try:
        with _discovery_directory(work_root) as root_descriptor:
            with os.scandir(root_descriptor) as entries:
                for entry in entries:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        raise ValueError(f"unsafe work task entry: {entry.name}")
                    # Entry paths from scandir(fd) are names, not full paths.
                    # The bounded reader below revalidates every ancestor and
                    # the final opened FD, including replacements after this scan.
                    cards.append(work_root / entry.name / "TASK.md")
    except FileNotFoundError:
        pass
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        errors.append(f"{work_root.relative_to(base).as_posix()}: {type(exc).__name__}")
    for card in sorted(cards):
        try:
            fields: dict[str, Any] = {}
            text = read_evidence_bytes(card).decode("utf-8")
            # work_store writes flat `key: JSON` frontmatter, not general YAML.
            # Its UI parser is deliberately forgiving; retention must not inherit
            # fallback strings, ignored lines or last-key-wins ambiguity.
            from work_store import SCHEMA, STATUSES, TERMINAL

            lines = text.split("\n")
            if lines[0] != "---" or "---" not in lines[1:]:
                raise ValueError("missing complete frontmatter delimiters")
            closing = lines.index("---", 1)

            def unique_object(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("duplicate JSON key")
                    obj[key] = value
                return obj

            def invalid_constant(value):
                raise ValueError("non-JSON constant")

            for line in lines[1:closing]:
                key, sep, raw = line.partition(":")
                if (not sep or not re.fullmatch(r"[a-z_][a-z0-9_]*", key)
                        or key in fields):
                    raise ValueError("ambiguous frontmatter field")
                fields[key] = json.loads(raw.strip(), object_pairs_hook=unique_object,
                                         parse_constant=invalid_constant)
            if (fields.get("schema") != SCHEMA
                    or not isinstance(fields.get("status"), str)
                    or fields["status"] not in STATUSES):
                raise ValueError("unknown work schema/status")
            # Both fields are always emitted by create/_dump; evidence entries
            # are prose strings (not necessarily paths), just like attached IDs.
            for key in ("run_ids", "evidence"):
                if (not isinstance(fields.get(key), list)
                        or any(not isinstance(ref, str) for ref in fields[key])):
                    raise ValueError("invalid work evidence references")
            if fields["status"] not in TERMINAL:
                result.update(_refs_run_ids(fields.get("run_ids") or ()))
                result.update(_refs_run_ids(fields.get("evidence") or ()))
                result.update(_refs_run_ids(text))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"{card.relative_to(base).as_posix()}: {type(exc).__name__}")

    ledger = base / "memory" / "desires" / "events.jsonl"
    try:
        with _discovery_directory(ledger.parent) as ledger_directory:
            ledger_info = os.stat(ledger.name, dir_fd=ledger_directory, follow_symlinks=False)
    except FileNotFoundError:
        ledger_info = None
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        errors.append(f"{ledger.relative_to(base).as_posix()}: {type(exc).__name__}")
        ledger_info = None
    if ledger_info is not None:
        from desires import SCHEMA, STATUSES, _DESIRE_ID_RE

        latest: dict[str, dict[str, Any]] = {}
        try:
            if not stat.S_ISREG(ledger_info.st_mode):
                raise ValueError("desire ledger must be a regular non-symlink file")
            ledger_bytes = read_evidence_bytes(ledger)
            if ledger_bytes and not ledger_bytes.endswith(b"\n"):
                raise ValueError("unterminated desire row")
            for number, line in enumerate(ledger_bytes.decode("utf-8").splitlines(), 1):
                if not line.strip():
                    raise ValueError(f"empty desire row {number}")
                row = _strict_json_loads(line)
                if (not isinstance(row, dict) or row.get("schema") != SCHEMA
                        or not isinstance(row.get("desire_id"), str)
                        or not _DESIRE_ID_RE.fullmatch(row["desire_id"])
                        or not isinstance(row.get("state"), dict)):
                    raise ValueError(f"invalid desire row {number}")
                state = row["state"]
                if (state.get("status") not in STATUSES
                        or any(not isinstance(state.get(key, []), list)
                               or any(not isinstance(ref, str) for ref in state.get(key, []))
                               for key in ("run_ids", "evidence_refs"))):
                    raise ValueError(f"invalid desire state {number}")
                latest[row["desire_id"]] = state
            for state in latest.values():
                if str(state.get("status") or "") not in DESIRE_TERMINAL_STATUSES:
                    result.update(_refs_run_ids(state.get("run_ids") or ()))
                    result.update(_refs_run_ids(state.get("evidence_refs") or ()))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            # Preserve every last fully validated open projection seen before a
            # corrupt tail; the corrupt row may not close or replace it.
            for state in latest.values():
                if str(state.get("status") or "") not in DESIRE_TERMINAL_STATUSES:
                    result.update(_refs_run_ids(state.get("run_ids") or ()))
                    result.update(_refs_run_ids(state.get("evidence_refs") or ()))
            errors.append(f"{ledger.relative_to(base).as_posix()}: {type(exc).__name__}")
    return result, errors


def _file_class(relative: PurePosixPath) -> str:
    first = relative.parts[0]
    if first == "manifest.json":
        return "manifest"
    if first == "events.jsonl":
        return "events"
    if first == "context.md":
        return "context"
    if first == "RECAP.md":
        return "recap"
    if first == "results":
        return "results"
    if first == "artifacts":
        return "artifacts"
    return "other"


def _sizes(run_dir: Path) -> tuple[dict[str, int], str]:
    sizes: dict[str, int] = {}
    if any(p.is_symlink() for p in (run_dir, *run_dir.parents)):
        return {}, "unsafe_symlink"
    try:
        for path in run_dir.rglob("*"):
            if path.is_symlink():
                return {}, "unsafe_symlink"
            if path.is_file():
                kind = _file_class(PurePosixPath(path.relative_to(run_dir).as_posix()))
                sizes[kind] = sizes.get(kind, 0) + path.stat().st_size
    except OSError:
        return {}, "unreadable_body"
    return sizes, ""


def inventory(base: Path, *, min_age: dt.timedelta, now: dt.datetime | None = None,
              open_evidence_run_ids: Iterable[str] = (),
              discover_open_evidence: bool = True) -> dict[str, Any]:
    """Return a deterministic, non-mutating reclaimability report."""
    if min_age < dt.timedelta(0):
        raise ValueError("min_age must be non-negative")
    _require_safe_openat("retention inventory")
    base = Path(base)
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    current = current.astimezone(dt.timezone.utc)
    open_ids = {str(value) for value in open_evidence_run_ids}
    evidence_errors: list[str] = []
    if discover_open_evidence:
        discovered, evidence_errors = discover_open_evidence_runs(base)
        open_ids.update(discovered)
    fail_closed_evidence = bool(evidence_errors)
    runs: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    root = base / "memory" / "runs"
    manifests = sorted(root.glob("*/*/manifest.json")) if root.is_dir() else []
    for manifest_path in manifests:
        run_dir = manifest_path.parent
        run_id = run_dir.name
        reason = "unsafe_symlink" if any(p.is_symlink() for p in (manifest_path, *manifest_path.parents)) else ""
        manifest: dict[str, Any] = {}
        try:
            if reason:
                raise ValueError("unsafe path")
            raw = _strict_json_loads(read_evidence_bytes(manifest_path).decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("manifest is not an object")
            if (raw.get("schema") != "praxis.run.v1"
                    or not isinstance(raw.get("context"), dict)
                    or raw["context"].get("run_id") != run_id):
                raise ValueError("manifest schema/run identity mismatch")
            manifest = raw
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reason = reason or "invalid_manifest"
        status = str(manifest.get("status") or "")
        if not reason and status == "in_doubt":
            reason = "in_doubt"
        elif not reason and status not in TERMINAL_STATUSES:
            reason = "non_terminal"
        if not reason and (fail_closed_evidence or run_id in open_ids):
            reason = "open_evidence_state_unreadable" if fail_closed_evidence else "open_evidence"
        terminal_at = str(manifest.get("terminal_at") or "")
        if not reason:
            try:
                age = current - _timestamp(terminal_at)
                if age < min_age:
                    reason = "recent"
            except ContractError:
                reason = "terminal_time_unknown"
        sizes, unsafe = _sizes(run_dir)
        if not reason and unsafe:
            reason = unsafe
        reclaimable = {}
        if not reason:
            try:
                event_bytes = read_evidence_bytes(run_dir / "events.jsonl")
                # Every WAL row must be a newline-terminated, non-empty UTF-8
                # JSON object. splitlines() alone would bless a crash fragment.
                if event_bytes and not event_bytes.endswith(b"\n"):
                    raise ContractError("unterminated event WAL")
                event_text = event_bytes.decode("utf-8")
                lines = event_text.splitlines()
                if any(not line.strip() for line in lines):
                    raise ContractError("empty event WAL row")
                rows = [_strict_json_loads(line) for line in lines]
                known = manifest.get("event_seq", 0)
                # RunManager replay requires a one-based contiguous WAL. A
                # terminal manifest is not trustworthy unless the complete
                # sequence through its published cursor is present exactly
                # once (and there is no unpublished tail).
                if (type(known) is not int or known < 0
                        or len(rows) != known
                        or any(not isinstance(row, dict)
                               or type(row.get("seq")) is not int
                               or row["seq"] != expected
                               for expected, row in enumerate(rows, 1))):
                    reason = "unsettled_events"
                else:
                    # Reduce every status-bearing WAL event.  A complete WAL can
                    # be newer than its atomically published manifest after a
                    # crash; a terminal manifest must never bless a reopened or
                    # otherwise non-terminal durable state.
                    reduced_status: str | None = None
                    for row in rows:
                        kind = row.get("kind")
                        if kind == "run_created" and isinstance(row.get("status"), str):
                            reduced_status = row["status"]
                        elif kind == "status_changed" and isinstance(row.get("to_status"), str):
                            reduced_status = row["to_status"]
                        elif kind == "resume_stale_reopened":
                            reduced_status = "paused"
                        elif kind == "resume_stale_closed":
                            reduced_status = "cancelled"
                        elif kind == "resume_stale_attention":
                            previous = row.get("previous_status")
                            if isinstance(previous, str):
                                reduced_status = previous
                    if reduced_status not in TERMINAL_STATUSES or reduced_status != status:
                        reason = "inconsistent_terminal_state"

                    # Reject a run, rather than merely one row, when the WAL
                    # gives the reader conflicting identities for one result.
                    identities_by_id: dict[str, tuple[str, str, int]] = {}
                    identities_by_path: dict[str, tuple[str, str, int]] = {}
                    conflicting = False
                    valid_refs: list[dict[str, Any]] = []
                    for row in rows:
                        if "result" not in row:
                            continue  # unambiguously unrelated event type
                        ref = row["result"]
                        try:
                            if not isinstance(ref, dict):
                                raise ContractError("ResultRef must be an object")
                            # Reuse the strict address/body identity grammar. This
                            # intentionally rejects wrong-run, wrong-schema and
                            # incomplete v1-looking rows for the entire run.
                            ArchiveLocator(run_id, "result", ref.get("result_id"), ref.get("path"),
                                           "object", ref.get("sha256"), ref.get("size"),
                                           ref.get("sha256"), ref.get("size"), terminal_at)
                            if ref.get("schema") != "praxis.result-ref.v1" or ref.get("run_id") != run_id:
                                raise ContractError("wrong ResultRef schema/run")
                        except (ContractError, TypeError, AttributeError):
                            reason = "invalid_result_ref"
                            break
                        result_id = ref["result_id"]
                        path = ref["path"]
                        by_id = (path, ref["sha256"], ref["size"])
                        by_path = (result_id, ref["sha256"], ref["size"])
                        if ((result_id in identities_by_id
                             and identities_by_id[result_id] != by_id)
                                or (path in identities_by_path
                                    and identities_by_path[path] != by_path)):
                            conflicting = True
                            break
                        identities_by_id[result_id] = by_id
                        identities_by_path[path] = by_path
                        valid_refs.append(ref)
                    if conflicting:
                        reason = "conflicting_result_refs"

                    # Materialize each body once through the shared descriptor-relative,
                    # ancestor-confined no-follow reader. This pins one regular FD for
                    # the bounded read, so a post-scan ancestor/path replacement cannot
                    # redirect verification to outside bytes or a blocking FIFO.
                    verified_sizes: dict[str, int] = {}
                    for ref in valid_refs if not reason else ():
                        if ref["path"] in verified_sizes:
                            continue
                        if ref["size"] > MAX_ARCHIVE_OBJECT_SIZE:
                            continue
                        try:
                            payload = read_evidence_bytes(run_dir / ref["path"],
                                                          max_bytes=ref["size"])
                        except ContractError:
                            continue
                        if (len(payload) == ref["size"]
                                and hashlib.sha256(payload).hexdigest() == ref["sha256"]):
                            verified_sizes[ref["path"]] = len(payload)
                    if verified_sizes:
                        reclaimable["results"] = sum(verified_sizes.values())
            except NotImplementedError as exc:
                raise ContractError(f"retention inventory requires POSIX file support: {exc}") from exc
            except ContractError:
                reason = "unsettled_events"
            except (OSError, UnicodeError, ValueError, TypeError):
                reason = "unreadable_events"
        eligible = not reason
        if eligible:
            for kind, size in reclaimable.items():
                totals[kind] = totals.get(kind, 0) + size
        runs.append({
            "run_id": run_id, "status": status or "unknown", "terminal_at": terminal_at,
            "eligible": eligible, "excluded_reason": reason or None,
            "reclaimable_bytes_by_class": reclaimable if eligible else {},
            "bytes_by_class": dict(sorted(sizes.items())), "bytes": sum(sizes.values()),
        })
    return {
        "schema": INVENTORY_SCHEMA,
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "policy": {"min_age_seconds": int(min_age.total_seconds())},
        "dry_run": True,
        "summary": {"runs_seen": len(runs), "runs_eligible": sum(r["eligible"] for r in runs),
                    "reclaimable_bytes": sum(totals.values()),
                    "reclaimable_bytes_by_class": dict(sorted(totals.items()))},
        "open_evidence_scan_errors": evidence_errors,
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only durable-run retention inventory")
    parser.add_argument("--base", type=Path, default=Path.cwd())
    parser.add_argument("--min-age-days", type=int, default=30)
    parser.add_argument("--open-run-id", action="append", default=[])
    args = parser.parse_args(argv)
    if args.min_age_days < 0:
        parser.error("--min-age-days must be non-negative")
    report = inventory(args.base, min_age=dt.timedelta(days=args.min_age_days),
                       open_evidence_run_ids=args.open_run_id)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
