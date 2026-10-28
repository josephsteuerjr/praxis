"""Independent model process used by Praxis Forge coding subagents.

Each worker gets a fresh context and task-bound tools.  A scout/reviewer is
read-oriented by construction; a worker can edit, apply patches, run commands,
start background processes, checkpoint and delegate further fresh-context
agents.  Results are files, so the parent Praxis process can poll them after a
restart and several workers can run concurrently.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import time
import traceback
from pathlib import Path

import forge
import llm
import run_context
import forge_runtime


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _bounded_env_int(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _bounded_env_float(name: str, default: float, *, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(low, min(high, value))


def _chat_with_transport_retry(request: dict, *, request_path: Path, system: str, messages: list[dict],
                               tools: list[dict] | None, logical_iteration: int,
                               call_options: dict | None = None, journal=None):
    """Retry an LLM iteration only while no response/tool call has reached the worker.

    Tool dispatch starts only after this helper returns. Received provider output
    carried by TornStreamError is retained without replay: it may include opaque
    reasoning or server-side work. Empty transport retries have a separate budget.
    """
    retries = _bounded_env_int("PRAXIS_FORGE_TRANSPORT_RETRIES", 2, low=0, high=8)
    pause = _bounded_env_float("PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC", 2.0,
                               low=0.0, high=60.0)
    for attempt in range(retries + 1):
        _control(request_path)  # Pause/Stop may arrive during a failed call or retry delay.
        try:
            return llm.chat("voice", system=system, messages=messages, tools=tools,
                            model=str(request.get("model") or "").strip() or None,
                            **(call_options or {}))
        except llm.BrokenChannelError as exc:
            retry = attempt < retries and not isinstance(exc, llm.TornStreamError)
            if journal is not None:
                partial = getattr(exc, "partial", None)
                reference = None
                if partial is not None:
                    reference = journal.payload(json.dumps({"text": partial.text, "blocks": partial.blocks,
                        "stop_reason": partial.stop_reason, "model": partial.model,
                        "assistant": _assistant(partial)}, ensure_ascii=False))
                journal.event("model_transport_error", error=f"{type(exc).__name__}: {exc}",
                              output_ref=reference, attempt=attempt, retry=retry,
                              partial_not_dispatched=partial is not None)
            if not retry:
                raise
            retry_number = attempt + 1
            print(
                f"{request.get('id') or 'forge-agent'}: transport broke on logical "
                f"iteration {logical_iteration}; retry {retry_number}/{retries} after "
                f"{pause:g}s ({type(exc).__name__}: {str(exc)[:160]})",
                flush=True,
            )
            try:
                forge._event(
                    request.get("task_id") or "", "agent_transport_retry",
                    agent_id=request.get("id"), iteration=logical_iteration,
                    attempt=retry_number, retries=retries,
                    summary=f"{type(exc).__name__}: {str(exc)[:240]}",
                )
            except Exception:
                pass
            if pause:
                time.sleep(pause)


def _write(path: Path, data: dict) -> None:
    forge_runtime.atomic_json(path, data)


def _publish_result(request_path: Path, task_id: str, data: dict) -> bool:
    """Stop and worker finalization share one CAS boundary; never replace a settled result."""
    with forge._mutation_lock(task_id):
        result = request_path.parent / "result.json"
        if result.exists():
            prior = forge._read_json(result)
            if not isinstance(prior, dict) or str(prior.get("status") or "") not in forge._LIVE_UNIT_STATES:
                return False
            data = {**prior, **data}  # preserve any earlier partial observations
        if data.get("status") in {"done", "stalled"}:
            reason = forge._admission_reason(task_id, str(request_path))
            if reason:
                raise WorkerControlStopped(reason)
        _write(result, data)
        return True


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


INSPECT_TOOL = {
    "name": "inspect",
    "description": (
        "See the exact task place. Besides read/search/diff, model/symbols/references/diagnostics/"
        "impact/checks expose normalized semantic and test facts; mailbox shows worker signals. "
        "Large read replies are exact UTF-8 JSON pages (<=8000 chars): continue with "
        "start=next_start, start_char=next_char, expected_sha256=sha256; a changed file refuses "
        "the old hash. Small reads retain the legacy numbered-line display."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["status", "orientation", "overview", "review", "model", "symbols",
                    "references", "diagnostics", "impact", "checks", "lessons", "mailbox",
                    "read", "list", "search", "diff", "history"]},
        "path": {"type": "string"}, "query": {"type": "string"},
        "glob": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"},
        "start_char": {"type": "integer", "description": "Unicode character offset within start line, for paged reads."},
        "expected_sha256": {"type": "string", "description": "Full 64-hex source hash from a read page; pins continuation to those bytes."},
    }, ["action"]),
}

VERIFY_TOOL = {
    "name": "verify",
    "description": (
        "Plan or supervise a durable verification matrix. plan derives targeted checks from the "
        "actual diff/impact map; start runs checks outside this model turn; poll gathers exact logs."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["plan", "start", "poll", "stop", "list"]},
        "verification_id": {"type": "string"}, "commands": {"type": "string"},
        "full": {"type": "boolean"}, "max_parallel": {"type": "integer"},
        "timeout": {"type": "integer"}, "tail": {"type": "integer"},
    }, ["action"]),
}

SIGNAL_TOOL = {
    "name": "signal",
    "description": (
        "Send a structured finding/question/blocker/contract/result to the shared swarm mailbox, "
        "or claim/release files as advisory ownership. Claims surface conflicts; they never veto edits."
    ),
    "input_schema": _obj({
        "kind": {"type": "string", "enum": ["finding", "question", "blocker", "contract",
                                                    "result", "claim", "release"]},
        "message": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}},
    }, ["kind"]),
}

EDIT_TOOL = {
    "name": "edit",
    "description": (
        "Edit inside this task root. replace is an exact unique replacement; write creates or "
        "rewrites a file; patch applies a unified diff only after git apply --check. Pass the "
        "full sha256 from inspect(read) as expected_sha256 for write/replace when another "
        "worker may race you (legacy 16-hex digests remain accepted). A multi-file patch "
        "refuses expected_sha256 rather than pretending it checked one file's hash."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["replace", "write", "patch"]},
        "path": {"type": "string"}, "content": {"type": "string"},
        "old": {"type": "string"}, "new": {"type": "string"},
        "patch": {"type": "string"}, "expected_sha256": {"type": "string"},
    }, ["action"]),
}

RUN_TOOL = {
    "name": "run",
    "description": (
        "Run an arbitrary foreground shell command in the task. Full output is kept as evidence; "
        "the reply is capped. timeout=0 means no timeout. Use this to test, build, lint and probe."
    ),
    "input_schema": _obj({"command": {"type": "string"}, "cwd": {"type": "string"},
                           "timeout": {"type": "integer"}}, ["command"]),
}

PROCESS_TOOL = {
    "name": "process",
    "description": (
        "Start/poll/stop/list a long-running command without blocking this agent. start returns an "
        "id immediately and keeps the complete log. timeout=0 is unbounded."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["start", "poll", "stop", "list"]},
        "process_id": {"type": "string"}, "command": {"type": "string"},
        "cwd": {"type": "string"}, "name": {"type": "string"},
        "timeout": {"type": "integer"}, "tail": {"type": "integer"},
    }, ["action"]),
}

DELEGATE_TOOL = {
    "name": "delegate",
    "description": (
        "Spawn a child fresh-context worker, poll/stop agent descendants, or disposition their "
        "exact terminal result. The parent identity comes from your worker request, never from "
        "model arguments. Detached process/verification jobs are task-scoped, separately stopped. "
        "disposition requires the full result_sha256 returned by poll. To execute a queued editing "
        "child, wait explicitly yields your shared-root writer slot, blocks all your other work, "
        "and returns its full terminal result before you regain ownership. First settle detached jobs."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["spawn", "list", "poll", "stop", "disposition", "wait"]},
        "agent_id": {"type": "string"}, "result_sha256": {"type": "string"},
        "disposition": {"type": "string", "enum": ["integrated", "irrelevant", "deferred"]},
        "brief": {"type": "string"},
        "role": {"type": "string", "enum": ["scout", "worker", "reviewer"]},
        "model": {"type": "string", "description": "Optional per-agent model; omitted keeps the voice-role default."},
        "max_iters": {"type": "integer"},
    }, ["action"]),
}

CHECKPOINT_TOOL = {
    "name": "checkpoint",
    "description": "Commit the current task working tree as a recoverable checkpoint.",
    "input_schema": _obj({"message": {"type": "string"}}, ["message"]),
}


def _system(request: dict) -> str:
    role = request["role"]
    # prompt_cache_key must stay stable across the turns of ONE fresh context, but two
    # concurrent scouts/reviewers must not share affinity merely because their role matches.
    identity = f"{request.get('task_id') or ''}:{request.get('id') or ''}"
    cache_scope = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    stance = {
        "scout": "You are a reconnaissance agent. Inspect broadly and return concrete evidence and file/line references; ask a worker for process probes.",
        "reviewer": "You are an adversarial code reviewer. Read the actual diff and relevant code, identify real defects or explicitly clear it; ask a worker for executable checks.",
        "worker": "You are an implementation agent. Orient, edit the real isolated working tree, run checks, and leave it materially closer to done. Do not stop at recommendations when you can act.",
    }[role]
    orientation = forge.inspect(request["task_id"], "orientation")
    return f"""audience_key=forge_{role}
cache_scope={cache_scope}
You are a fresh-context coding subagent created by Praxis, not a conversational assistant.

Overall goal: {request.get('goal')}
Your brief: {request.get('brief')}
Your role: {role}
{stance}

FACTUAL ORIENTATION
{orientation}

Operating contract:
- The task root is your exact address. Inspect current files and git state; never invent project facts.
- Work autonomously and use as many tool turns as the job needs. The parent can run other agents concurrently.
- For concurrency-sensitive edits, read the file hash and pass expected_sha256; a mismatch is a signal to rebase your reasoning, not to overwrite blindly.
- A worker may use `run` for arbitrary build/test/probes, `process` for long-lived commands, and `verify` for a persisted matrix. A scout or reviewer cannot launch them; share needed checks with a worker using `signal`. Detached process/verification jobs are task-scoped; stopping your agent subtree does NOT stop them. Stop those jobs explicitly.
- Use semantic `inspect` actions before broad text guessing. `delegate` can create further specialists within your role; `signal` leaves findings/contracts/blockers in the shared DAG mailbox.
- Editing children queue behind your single shared-root writer slot. `delegate(action="wait", agent_id=...)` explicitly yields that slot to ONE immediate worker child and returns its full result. You cannot work meanwhile; settle detached processes/checks first. Parent Stop/Pause still fences the child.
- Never put credentials or secret contents in your prose. Code facts, commands, diffs and test output are welcome.
- End with a compact but complete result: what you learned/changed, exact verification, remaining risks, and the next useful integration step. Do not merely narrate intentions.
"""


def _dispatch(request: dict, name: str, args: dict) -> str:
    task_id = request["task_id"]
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    role = str(request.get("role") or "worker")
    # The provider may return a tool name absent from the offered schema. Keep
    # read-oriented roles read-only at the actual dispatch boundary, not only in
    # the prompt or tool list. Arbitrary shell/verification commands can mutate.
    if role != "worker" and name not in {"inspect", "signal", "delegate"}:
        return f"{role}: tool {name} refused (read-oriented role)."
    if role != "worker" and name == "inspect" and clean.get("action") not in {
        "status", "orientation", "overview", "review", "model", "symbols",
        "references", "diagnostics", "impact", "checks", "lessons", "mailbox",
        "read", "list", "search", "diff", "history",
    }:
        return f"{role}: inspect action refused (read-oriented role)."
    if name == "inspect":
        return forge.inspect(task_id, **clean)
    if name == "edit":
        return forge.edit(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    if name == "run":
        return forge.run(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    if name == "process":
        return forge.process(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    if name == "verify":
        return forge.verify(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    if name == "signal":
        return forge.swarm(task_id, "signal", node_id=str(request.get("node_id") or request.get("id") or ""),
                           kind=str(clean.get("kind") or "finding"),
                           message=str(clean.get("message") or ""), files=clean.get("files") or [])
    if name == "delegate":
        action = str(clean.pop("action", "spawn"))
        if action == "spawn":
            clean.pop("agent_id", None)  # only Forge mints a child identity
        return forge.agent(task_id, action, _parent_request_path=request.get("_verified_request_path", ""),
                           **clean)
    if name == "checkpoint":
        return forge.checkpoint(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    return f"Unknown tool {name}"


def _model_tool_result(output: str, *, limit: int = 12000) -> str:
    """No model-visible tool output may disappear without an omission marker."""
    text = str(output)
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    while True:
        warning = (f"\n[TOOL RESULT OMITTED: {omitted} characters not delivered. "
                   "This is NOT a complete result. Narrow the query/read range and retry; "
                   "do not infer facts from the missing tail.]\n")
        corrected = len(text) - (limit - len(warning))
        if corrected == omitted:
            break
        omitted = corrected
    return text[:limit - len(warning)] + warning


def run(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    return _run_request(request_path, request)


class WorkerControlStopped(RuntimeError):
    pass


def _control(request_path: Path) -> None:
    decision = forge.worker_control(str(request_path))
    if not decision.get("allowed"):
        raise WorkerControlStopped(str(decision.get("reason") or "worker control denied"))


def _assistant(response, blocks=None):
    helper = getattr(llm, "assistant_message", None)
    selected = ((response.blocks or ([{"type": "text", "text": response.text}] if response.text else []))
                if blocks is None else blocks)
    return helper(response, selected) if callable(helper) else {"role": "assistant", "content": selected}


def _loop(journal, request_path, request, adapter):
    """Persist each intent before effects; move the cursor only after durable result."""
    state = journal.state
    while state["next_boundary"] != "finalize":
        _control(request_path)
        boundary = state["next_boundary"]
        if boundary == "tools_ready":
            pending = state["pending"]
            if pending["next_call"] == len(pending["calls"]):
                state["messages"].append(pending["assistant"])
                state["messages"].append({"role": "user", "content": pending["results"]})
                journal.save("model_ready", "ready", pending={})
                continue
            call = pending["calls"][pending["next_call"]]
            args = {k: v for k, v in (call.get("input") or {}).items() if v is not None}
            input_ref = journal.payload(json.dumps(args, ensure_ascii=False, sort_keys=True))
            # Checkpoint in-flight FIRST. A crash before the event or after the
            # external effect both leave an unknown, never a duplicate dispatch.
            _control(request_path)
            journal.save("tool_in_flight", "tool")
            journal.event("tool_start", call_id=call["id"], tool=call["name"], input_ref=input_ref)
            try:
                output = (adapter.dispatch(request, call["name"], args) if adapter is not None
                          else _dispatch(request, call["name"], args))
            except Exception as exc:
                output = f"Tool error {type(exc).__name__}: {exc}"
            output = str(output)
            output_ref = journal.payload(output)
            event = journal.event("tool_result", call_id=call["id"], tool=call["name"],
                                  input_ref=input_ref, output_ref=output_ref)
            state["trace"].append({"tool": call["name"], "input_ref": input_ref,
                                    "output_ref": output_ref, "call_id": call["id"],
                                    "iteration": state["iteration"], "at": event["at"]})
            # Complete result also goes to the model transcript; no invisible tail
            # and no second lossy '30 calls / 2000 chars' terminal history.
            pending["results"].append({"type": "tool_result", "tool_use_id": call["id"], "content": output})
            pending["next_call"] += 1
            journal.save("tools_ready", "ready")
            continue
        if boundary == "model_ready" and state["iteration"] >= state["max_iters"]:
            journal.save("summary_ready", "finishing")
            continue
        if boundary not in {"model_ready", "summary_ready"}:
            raise forge_runtime.ContinuationRefused("unexpected/unknown execution boundary")
        summarizing = boundary == "summary_ready"
        if not summarizing:
            state["iteration"] += 1
        system = adapter.system(request) if adapter is not None else _system(request)
        journal.save("summary_in_flight" if summarizing else "model_in_flight", "model",
                     system=system)
        journal.event("model_start", summary=summarizing, model_requested=request.get("model", ""))
        response = _chat_with_transport_retry(
            request, request_path=request_path, system=system, messages=state["messages"],
            tools=None if summarizing else state["tools"],
            logical_iteration=state["iteration"] + int(summarizing),
            call_options=state["call_options"], journal=journal,
        )
        state["model"] = response.model
        response_ref = journal.payload(json.dumps({"text": response.text, "blocks": response.blocks,
            "stop_reason": response.stop_reason, "model": response.model,
            "usage": getattr(response, "usage", {}), "assistant": _assistant(response)}, ensure_ascii=False))
        journal.event("model_result", output_ref=response_ref, summary=summarizing, model=response.model)
        if summarizing or response.stop_reason != "tool_use":
            # A textual answer is not completion of an adapter-owned transaction.
            # Keep the same journal and budget; never replay tools or reset turns.
            continuation = getattr(adapter, "completion_feedback", None)
            if not summarizing and callable(continuation):
                feedback = continuation(request)
                if feedback:
                    state["messages"].append(_assistant(response))
                    state["messages"].append({"role": "user", "content": feedback})
                    journal.event("completion_deferred", reason=feedback)
                    journal.save("model_ready", "ready", reply="", stopped_himself=False)
                    continue
            state["reply"] = response.text.strip()
            if not summarizing:
                state["stopped_himself"] = (response.stop_reason == "end_turn" if adapter is not None else True)
            state["messages"].append(_assistant(response))
            journal.save("finalize" if state["reply"] or summarizing else "summary_ready", "finishing")
            continue
        blocks = [block for block in response.blocks if block.get("type") in {"text", "tool_use"}]
        calls = [block for block in blocks if block.get("type") == "tool_use"]
        if any(not call.get("id") or not call.get("name") for call in calls):
            raise ValueError("model tool call missing identity")
        journal.save("tools_ready", "ready", pending={"assistant": _assistant(response, blocks),
                     "calls": calls, "next_call": 0, "results": []})


def _run_request(request_path: Path, request: dict, *, reviewer=None, adapter=None) -> int:
    # Historical keyword remains accepted; trusted adapters never inherit worker tools.
    if reviewer is not None and adapter is not None:
        raise ValueError("choose one trusted adapter")
    adapter = adapter if adapter is not None else reviewer
    request = {**request, "_verified_request_path": str(request_path.resolve())}
    journal = None
    bindings = contextlib.ExitStack()
    try:
        journal = forge_runtime.Journal(request_path.parent)
        prior = forge._read_json(request_path.parent / "result.json")
        if isinstance(prior, dict) and prior.get("status") not in forge._LIVE_UNIT_STATES:
            return 0  # terminal redelivery cannot start another model/tool turn
        _control(request_path)
        bindings.enter_context(run_context.bind_run(None))
        captured = request.get("run_context")
        if captured is not None and captured != {}:
            if not isinstance(captured, dict):
                raise TypeError("run_context must be an object or null")
            context = run_context.RunContext.from_dict(captured)
            from dataclasses import replace
            context = replace(context, forge_task_id=str(request["task_id"]))
            bindings.enter_context(run_context.bind_run(context))
        role = request.get("role") or "worker"
        call_options = adapter.prepare(llm, request) if adapter is not None else None
        if adapter is not None:
            tools = list(adapter.tools)
        else:
            tools = [INSPECT_TOOL, SIGNAL_TOOL, DELEGATE_TOOL]
            if role == "worker":
                tools += [EDIT_TOOL, RUN_TOOL, PROCESS_TOOL, VERIFY_TOOL, CHECKPOINT_TOOL]
        adapter_id = (type(adapter).__module__ + "." + type(adapter).__qualname__) if adapter is not None else ""
        state = journal.start(request, {
            "next_boundary": "model_ready", "iteration": 0,
            "max_iters": int(request.get("max_iters") or 0) or max(20, llm.limits().max_tool_iters * 2),
            "messages": [{"role": "user", "content": str(request.get("brief") or "Execute the brief.")}],
            "tools": tools, "call_options": call_options, "adapter_id": adapter_id,
            "trace": [], "pending": {}, "reply": "", "model": "", "stopped_himself": False,
        })
        _loop(journal, request_path, request, adapter)
        _control(request_path)
        trace = journal.trace()
        diff = str(adapter.diff() if adapter is not None else forge.inspect(request["task_id"], "diff"))
        diff_ref = journal.payload(diff)
        stopped = state["stopped_himself"]
        data = {
            "status": "done" if stopped else "stalled", "complete": bool(stopped),
            "stop": ("модель остановилась сама" if stopped else
                     "кончились ходы: %d из %d" % (state["iteration"], state["max_iters"])),
            "iters_used": state["iteration"], "iters_max": state["max_iters"],
            "tool_errors": sum(str(row["output"]).startswith("Tool error") for row in trace),
            "finished": _now(), "role": role, "model_requested": str(request.get("model") or ""),
            "model": state["model"], "result": state["reply"], "tool_calls": len(trace),
            "trace": trace, "diff_tail": diff[-8000:], "diff_ref": diff_ref,
            "generation": state["generation"], "events_ref": {"file": "events.jsonl", "last_seq": journal.seq},
        }
        if adapter is not None:
            data.update(adapter.result_stamp())
        if not _publish_result(request_path, request["task_id"], data):
            journal.progress("settled_elsewhere")
            return 0
        journal.event("result_published", status=data["status"], diff_ref=diff_ref)
        journal.save("settled", "finished", result_sha256=hashlib.sha256(
            (request_path.parent / "result.json").read_bytes()).hexdigest())
        forge._event(request["task_id"], "agent_finished", agent_id=request["id"], role=role,
                     summary=f"{request['id']} {data['status']}; tools={len(trace)}")
        forge.emit_unit_event(request["task_id"], request["id"], data, request=request)
        print(f"{request['id']} {data['status']}; tools={len(trace)}", flush=True)
        return 0
    except forge_runtime.ContinuationRefused as exc:
        # Do not overwrite a previous checkpoint or settle a still-live writer.
        print(f"Continuation refused: {exc}", flush=True)
        return 2
    except Exception as exc:
        controlled = isinstance(exc, WorkerControlStopped)
        data = {"status": "stopped" if controlled else "error", "finished": _now(),
                "model_requested": str(request.get("model") or ""),
                "model": (journal.state.get("model", "") if journal else ""),
                "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(),
                "trace": journal.trace() if journal and journal.state else []}
        try:
            if journal and journal.state:
                journal.event("control_stopped" if controlled else "worker_error", error=data["error"])
                journal.progress("stopped" if controlled else "error")
            if not _publish_result(request_path, request["task_id"], data):
                return 2
        except Exception:
            print(data["traceback"], flush=True)
            return 2
        try:
            forge._event(request["task_id"], "agent_error", agent_id=request.get("id"), summary=data["error"])
        except Exception:
            pass
        forge.emit_unit_event(request.get("task_id") or "", request.get("id") or "", data, request=request)
        print(data["traceback"], flush=True)
        return 2
    finally:
        try:
            if journal is not None and journal.state:
                # The model/tool loop is now over. Relinquish only task-root
                # mutation; the immutable result/private journal stay intact.
                release = getattr(forge, "release_worker_root", None)
                if callable(release):
                    release(request_path)
        finally:
            if journal is not None:
                journal.close()
            bindings.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    raise SystemExit(run(Path(args.request)))
