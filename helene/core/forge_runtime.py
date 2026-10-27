"""Durable facts for one existing Forge agents/<unit> directory, not a task store.

The worker is the only writer (OS-held writer.lock). Checkpoints contain the full
model transcript, including route-bound protocol sidecars. Immutable UTF-8 payloads
hold complete tool arguments/results; events record their order. Readers never
infer process liveness from a phase or silently discard damaged journal records.
Only the core coordinator authorizes continuation after proving old-writer exit.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


class ContinuationRefused(ValueError):
    """Recorded work cannot safely be repeated or continued automatically."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("xb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path, default=None):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    return json.loads(raw.decode("utf-8"))


def _payload_path(directory: Path, sha256: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
        raise ValueError("invalid payload SHA-256")
    root = Path(directory).resolve()
    path = root / "payloads" / (sha256 + ".txt")
    if path.resolve().parent != root / "payloads" or (root / "payloads").is_symlink():
        raise ValueError("payload outside unit directory")
    return path


def read_payload(directory, sha256: str, offset: int = 0, limit: int = 8000) -> dict:
    raw = _payload_path(Path(directory), sha256).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("payload SHA-256 changed")
    text = raw.decode("utf-8")
    offset = max(0, int(offset))
    if offset > len(text):
        raise ValueError("payload offset outside source")
    part = text[offset:offset + max(1, min(8000, int(limit)))]
    end = offset + len(part)
    return {"sha256": sha256, "size_bytes": len(raw), "total_chars": len(text),
            "offset": offset, "text": part, "next_offset": end if end < len(text) else None}


def read_progress(directory) -> dict:
    value = _read(Path(directory) / "progress.json", {})
    if not isinstance(value, dict):
        raise ValueError("invalid worker progress")
    return value


def read_events(directory, after_seq: int = 0, limit: int = 100) -> dict:
    after_seq, limit = max(0, int(after_seq)), max(1, min(500, int(limit)))
    rows, prior, more = [], 0, False
    path = Path(directory) / "events.jsonl"
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return {"events": [], "next_after_seq": after_seq, "has_more": False}
    with stream:
        for raw in stream:
            # A writer can be appending the last record. Do not interpret a torn
            # record as absence; caller may retry, writer admission refuses it.
            if not raw.endswith(b"\n"):
                raise ValueError("incomplete worker event record")
            row = json.loads(raw.decode("utf-8"))
            seq = row.get("seq")
            if not isinstance(seq, int) or seq != prior + 1:
                raise ValueError("worker event sequence damaged")
            prior = seq
            if seq <= after_seq:
                continue
            if len(rows) == limit:
                more = True
                break
            rows.append(row)
    return {"events": rows, "next_after_seq": rows[-1]["seq"] if rows else after_seq,
            "has_more": more}


def _events(directory):
    cursor = 0
    while True:
        page = read_events(directory, cursor, 500)
        yield from page["events"]
        if not page["has_more"]:
            return
        cursor = page["next_after_seq"]


def request_identity(request: dict) -> str:
    # Core adds PID/birth/status after spawning; none changes the assignment.
    stable = {k: v for k, v in request.items() if k not in {
        "supervisor_pid", "supervisor_started_at", "status", "_verified_request_path"}}
    return hashlib.sha256(_json_bytes(stable)).hexdigest()


def _recover_result(directory: Path, checkpoint: dict) -> dict:
    """Close only the result->checkpoint crash window, NEVER repeat a call."""
    if checkpoint.get("next_boundary") != "tool_in_flight":
        return checkpoint
    pending = checkpoint.get("pending") or {}
    index = int(pending.get("next_call", 0))
    calls = pending.get("calls") or []
    if index >= len(calls):
        raise ValueError("invalid checkpoint tool cursor")
    call = calls[index]
    found = None
    for event in _events(directory):
        if (event.get("generation") == checkpoint.get("generation")
                and event.get("iteration") == checkpoint.get("iteration")
                and event.get("call_id") == call["id"] and event.get("kind") == "tool_result"):
            found = event
    if found is None:
        return checkpoint
    output = read_payload_full(directory, found["output_ref"])
    state = json.loads(json.dumps(checkpoint))
    state["pending"]["results"].append({"type": "tool_result", "tool_use_id": call["id"],
                                         "content": output})
    state["trace"].append({"tool": call["name"], "input_ref": found["input_ref"],
                            "output_ref": found["output_ref"], "call_id": call["id"],
                            "iteration": state["iteration"], "at": found["at"]})
    state["pending"]["next_call"] += 1
    state["next_boundary"] = "tools_ready"
    return state


def read_payload_full(directory, reference: dict) -> str:
    raw = _payload_path(Path(directory), reference["sha256"]).read_bytes()
    if (hashlib.sha256(raw).hexdigest() != reference["sha256"]
            or len(raw) != reference["size_bytes"]):
        raise ValueError("immutable worker payload changed")
    return raw.decode("utf-8")


def continuation_info(directory) -> dict:
    directory = Path(directory)
    path = directory / "checkpoint.json"
    if not path.exists():
        return {"safe": False, "reason": "checkpoint missing"}
    try:
        seen = False
        for _ in _events(directory):
            seen = True
        if not seen:
            raise ValueError('missing or empty event journal')
    except (OSError, ValueError) as exc:
        raise ContinuationRefused(f'source event journal is invalid: {exc}') from exc
    raw = path.read_bytes()
    checkpoint = json.loads(raw.decode("utf-8"))
    recovered = _recover_result(directory, checkpoint)
    boundary = recovered.get("next_boundary")
    safe = boundary in {"model_ready", "tools_ready", "summary_ready", "finalize"}
    return {"checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
            "generation": checkpoint.get("generation"), "next_boundary": boundary,
            "safe": safe, "reason": "settled boundary" if safe else
            "in-flight outcome unknown or terminal; no automatic replay"}


class Journal:
    """Lifetime writer lock plus checkpoint/event/payload operations for one unit."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (self.directory / "writer.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._lock.seek(0)
                if self._lock.read(1) == b"":
                    self._lock.write(b"\0")
                    self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            raise ContinuationRefused("worker writer is still active") from exc
        self.seq = 0
        try:
            for event in _events(self.directory):
                self.seq = event["seq"]
        except BaseException:
            self.close()
            raise
        self.state = {}

    def close(self):
        if not self._lock.closed:
            # Closing the descriptor releases flock/msvcrt even after a crash.
            self._lock.close()

    def payload(self, text: str) -> dict:
        raw = str(text).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        path = _payload_path(self.directory, sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("immutable worker payload damaged")
        else:
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(path.parent)
        return {"sha256": sha, "size_bytes": len(raw), "encoding": "utf-8"}

    def event(self, kind: str, **fields) -> dict:
        row = {"schema": 1, "seq": self.seq + 1, "generation": self.state["generation"],
               "at": now(), "kind": kind, "iteration": self.state.get("iteration", 0), **fields}
        path = self.directory / "events.jsonl"
        with path.open("ab") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.seq = row["seq"]
        return row

    def save(self, boundary: str, phase: str, **fields) -> None:
        self.state.update(next_boundary=boundary, **fields)
        self.state["event_seq"] = self.seq
        atomic_json(self.directory / "checkpoint.json", self.state)
        self.progress(phase)

    def progress(self, phase: str, **fields) -> None:
        pending = self.state.get("pending") or {}
        index = int(pending.get("next_call", 0))
        calls = pending.get("calls") or []
        call = calls[index] if index < len(calls) else {}
        atomic_json(self.directory / "progress.json", {
            "schema": 1, "generation": self.state["generation"], "phase": phase,
            "iteration": self.state.get("iteration", 0), "model": self.state.get("model", ""),
            "tool": call.get("name", ""), "call_id": call.get("id", ""),
            "observed_at": now(), "seq": self.seq, "status": "observed", **fields})

    def start(self, request: dict, initial: dict) -> dict:
        source = request.get("continuation_source")
        origin = self.directory
        if source:
            if not isinstance(source, dict):
                raise ContinuationRefused("invalid continuation source")
            origin = Path(source["unit_dir"]).resolve()
            if origin == self.directory or origin.parent != self.directory.parent:
                raise ContinuationRefused("continuation source is not another unit of this task")
            original = _read(origin / "request.json")
            if (not isinstance(original, dict) or original.get("task_id") != request["task_id"]
                    or original.get("id") != origin.name
                    or any(original.get(key) != request.get(key) for key in
                           ("role", "root", "self_review", "source_update"))):
                raise ContinuationRefused("continuation source identity/authority mismatch")
        path = origin / "checkpoint.json"
        if path.exists():
            info = continuation_info(origin)
            receipt_path = self.directory / "resume.json"
            receipt = _read(receipt_path, {})
            if not all(receipt.get(key) is True for key in (
                    "authorized", "old_writer_ended", "task_control_permits")):
                raise ContinuationRefused("explicit coordinator continuation receipt required")
            if (receipt.get("checkpoint_sha256") != info["checkpoint_sha256"]
                    or receipt.get("generation") != info["generation"]):
                raise ContinuationRefused("continuation checkpoint changed")
            if source and (source.get("checkpoint_sha256") != info["checkpoint_sha256"]
                           or source.get("generation") != info["generation"]):
                raise ContinuationRefused("source checkpoint changed")
            if not info["safe"]:
                raise ContinuationRefused(info["reason"])
            state = _recover_result(origin, _read(path))
            if any(state.get(key) != initial.get(key) for key in ("adapter_id", "tools", "call_options")):
                raise ContinuationRefused("execution adapter/route changed")
            if not source and state.get("request_sha256") != request_identity(request):
                raise ContinuationRefused("worker assignment changed")
            if source:
                # Only content-addressed references actually carried by the transcript
                # are copied; neither profiles nor the entire previous worktree travel.
                for row in state.get("trace", []):
                    for key in ("input_ref", "output_ref"):
                        self.payload(read_payload_full(origin, row[key]))
                state["continued_from"] = {"unit_id": origin.name, **info}
            # Receipt consumed before any new effect. A crash now needs a fresh
            # coordinator decision, not reuse of an old launch authorization.
            receipt_path.replace(self.directory / ("resume-consumed-" + uuid.uuid4().hex + ".json"))
            state["previous_generation"] = state.get("generation")
            self.state = state
        else:
            if source:
                raise ContinuationRefused("source checkpoint missing")
            if self.seq:
                raise ContinuationRefused("journal exists without checkpoint")
            self.state = initial
        self.state.update(schema=1, generation=uuid.uuid4().hex,
                          request_sha256=request_identity(request), task_id=request["task_id"], unit_id=request["id"])
        self.event("generation_started", continued_from=self.state.get("continued_from"))
        self.save(self.state["next_boundary"], "starting")
        return self.state

    def trace(self) -> list[dict]:
        return [{"tool": row["tool"], "input": json.loads(read_payload_full(self.directory, row["input_ref"])),
                 "output": read_payload_full(self.directory, row["output_ref"]), "at": row["at"],
                 "call_id": row["call_id"], "input_ref": row["input_ref"], "output_ref": row["output_ref"]}
                for row in self.state.get("trace", [])]
