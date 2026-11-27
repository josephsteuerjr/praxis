"""Durable facts for one existing Forge agents/<unit> directory, not a task store.

The OS-held writer.lock serializes the worker and, after proven exit, the
coordinator appending a reconstructed child-wait result. Checkpoints contain the full
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


def read_record(directory,sha256,kind='payload',offset=0,limit=8000):
    if not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise ValueError('payload_sha256 must contain the exact 64-character SHA-256; use the hash from poll')
    if kind=='payload':return read_payload(directory,sha256,offset,limit)
    if kind not in {'result','request'}:raise ValueError('unsupported worker record kind')
    raw=(Path(directory)/(kind+'.json')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha256:raise ValueError('worker record changed; refresh exact SHA')
    text=raw.decode('utf-8');offset=int(offset);limit=max(1,min(8000,int(limit)))
    if offset<0 or offset>len(text):raise ValueError('record offset outside content')
    end=min(len(text),offset+limit)
    return {'sha256':sha256,'size_bytes':len(raw),'total_chars':len(text),
            'offset':offset,'text':text[offset:end],'next_offset':end if end<len(text) else None}


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
        "supervisor_pid", "supervisor_started_at", "process_identity", "status", "_verified_request_path",
        "wait_launch_id"}}
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
        last_seq = 0
        for event in _events(directory):
            last_seq = event['seq']
        if not last_seq:
            raise ValueError('missing or empty event journal')
    except (OSError, ValueError) as exc:
        raise ContinuationRefused(f'source event journal is invalid: {exc}') from exc
    raw = path.read_bytes()
    checkpoint = json.loads(raw.decode("utf-8"))
    committed = checkpoint.get('event_seq')
    if type(committed) is not int or committed < 1 or last_seq < committed:
        raise ContinuationRefused('source event journal is shorter than the committed checkpoint')
    recovered = _recover_result(directory, checkpoint)
    boundary = recovered.get("next_boundary")
    safe = boundary in {"model_ready", "tools_ready", "summary_ready", "finalize"}
    return {"checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
            "generation": checkpoint.get("generation"), "next_boundary": boundary,
            "safe": safe, "reason": "settled boundary" if safe else
            "in-flight outcome unknown or terminal; no automatic replay"}


def wait_checkpoint(directory: Path, request: dict, child: str) -> tuple[dict, dict] | None:
    """The only in-flight tool with a coordinator-owned result reconstruction."""
    try:
        raw = (directory / "checkpoint.json").read_bytes()
        state = json.loads(raw)
        pending = state["pending"]
        call = pending["calls"][pending["next_call"]]
        args = {key: value for key, value in call["input"].items() if value is not None}
        if (state.get("next_boundary") == "tool_in_flight"
                and state.get("request_sha256") == request_identity(request)
                and state.get("task_id") == request["task_id"]
                and state.get("unit_id") == request["id"] == directory.name
                and state.get("generation") and call.get("id")
                and call["name"] == "delegate" and args == {"action": "wait", "agent_id": child}):
            return state, {"schema": 1, "checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
                           "generation": state["generation"], "call_id": call["id"]}
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    return None


def _wait_events(directory, state: dict, binding: dict) -> tuple[dict, ...]:
    return tuple(row for row in _events(directory)
                 if row["seq"] > state["event_seq"] and row.get("generation") == state["generation"]
                 and row.get("iteration") == state["iteration"] and row.get("call_id") == binding["call_id"])


def settle_wait(journal, state: dict, binding: dict, output: str, child: str) -> str:
    """Use the ordinary result->checkpoint recovery lane without dispatching a tool."""
    events = _wait_events(journal.directory, state, binding)
    kinds = tuple(row.get("kind") for row in events)
    if (kinds not in {("tool_start", "tool_dispatch"), ("tool_start", "tool_dispatch", "tool_result")}
            or any(row.get("tool", "delegate") != "delegate" for row in events)
            or json.loads(read_payload_full(journal.directory, events[0]["input_ref"]))
                != {"action": "wait", "agent_id": child}):
        return ""
    if kinds[-1] == "tool_result":
        event = events[-1]
        # Do not contradict what the old worker durably returned, including unknown.
        if (event.get("input_ref") != events[0]["input_ref"]
                or read_payload_full(journal.directory, event["output_ref"]) != output):
            return ""
    else:
        journal.state = state
        event = journal.event("tool_result", call_id=binding["call_id"], tool="delegate",
                              input_ref=events[0]["input_ref"], output_ref=journal.payload(output),
                              executions=[], recovered_from="forge_child_result")
    return hashlib.sha256(_json_bytes(event)).hexdigest()


def owns_wait_launch(directory: Path, receipt: dict) -> bool:
    import process_liveness
    declared = _read(directory / "request.json", {})
    handoff = _read(directory / "handoff.json", {})
    pid = os.getpid()
    birth = process_liveness.process_started_at(pid)
    launch_id = receipt.get("wait_launch_id")
    return (bool(launch_id and birth) and isinstance(declared, dict) and isinstance(handoff, dict)
            and handoff.get("wait_launch_id") == launch_id
            and declared.get("wait_launch_id") == launch_id and declared.get("status") == "running"
            and declared.get("supervisor_pid") == pid and declared.get("supervisor_started_at") == birth)


def _verify_wait_resume(directory, state: dict, receipt: dict) -> None:
    handoff = _read(directory / "handoff.json", {})
    events = _wait_events(directory, state, receipt)
    child = receipt["child_unit_id"]
    digest = receipt["child_result_sha256"]
    if (not owns_wait_launch(directory, receipt) or not re.fullmatch(r"agent-[A-Za-z0-9_.-]+", child)
            or handoff.get("child_unit_id") != child or handoff.get("result_sha256") != digest
            or handoff.get("state") != "returned" or handoff.get("restart_requested") is not True
            or tuple(row.get("kind") for row in events) != ("tool_start", "tool_dispatch", "tool_result")
            or hashlib.sha256(_json_bytes(events[-1])).hexdigest() != receipt["wait_event_sha256"]
            or hashlib.sha256((directory.parent / child / "result.json").read_bytes()).hexdigest() != digest):
        raise ContinuationRefused("child wait launch or selected result no longer matches its receipt")
    read_payload_full(directory, events[-1]["output_ref"])


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
        receipt_path = self.directory / "resume.json"
        receipt = _read(receipt_path, {})
        # A former explicit continuation retains its original provenance in
        # the request. A later same-ID wait receipt names this unit's checkpoint.
        source = None if "wait_event_sha256" in receipt else request.get("continuation_source")
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
                           ("role", "root", "self_review", "source_update",'run_context','model'))):
                raise ContinuationRefused("continuation source identity/authority mismatch")
        path = origin / "checkpoint.json"
        if path.exists():
            info = continuation_info(origin)
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
            old_state = _read(path)
            if "wait_event_sha256" in receipt:
                try:
                    _verify_wait_resume(origin, old_state, receipt)
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    raise ContinuationRefused("child wait recovery evidence unavailable or changed") from exc
            state = _recover_result(origin, old_state)
            if state.get("ordinary_context_sha256") != initial.get("ordinary_context_sha256"):
                raise ContinuationRefused("ordinary worker assignment or source context changed")
            if any(state.get(key) != initial.get(key) for key in ("adapter_id", "tools", "call_options",'route_identity')):
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
