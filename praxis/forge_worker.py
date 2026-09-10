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


def _chat_with_transport_retry(request: dict, *, system: str, messages: list[dict],
                               tools: list[dict] | None, logical_iteration: int):
    """Retry an LLM iteration only while no response/tool call has reached the worker.

    ``llm.chat`` either returns a complete response object or raises.  Tool dispatch starts
    only after this helper returns, so repeating a ``BrokenChannelError`` cannot execute a
    tool twice.  The retry budget is deliberately separate from ``max_iters``: a torn HTTP
    body is not a model turn and must not consume the agent's reasoning budget.
    """
    retries = _bounded_env_int("PRAXIS_FORGE_TRANSPORT_RETRIES", 2, low=0, high=8)
    pause = _bounded_env_float("PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC", 2.0,
                               low=0.0, high=60.0)
    for attempt in range(retries + 1):
        try:
            return llm.chat("voice", system=system, messages=messages, tools=tools,
                            model=str(request.get("model") or "").strip() or None)
        except llm.BrokenChannelError as exc:
            if attempt >= retries:
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
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


INSPECT_TOOL = {
    "name": "inspect",
    "description": (
        "See the exact task place. Besides read/search/diff, model/symbols/references/diagnostics/"
        "impact/checks expose normalized semantic and test facts; mailbox shows worker signals."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["status", "orientation", "overview", "review", "model", "symbols",
                    "references", "diagnostics", "impact", "checks", "lessons", "mailbox",
                    "read", "list", "search", "diff", "history"]},
        "path": {"type": "string"}, "query": {"type": "string"},
        "glob": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"},
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
        "sha256 returned by inspect(read) as expected_sha256 when another worker may race you."
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
        "Spawn another independent fresh-context coding agent on this same task. Use several scouts "
        "for parallel reconnaissance, a worker for a separable implementation, or a reviewer for a "
        "hostile second look. Returns immediately; poll it through inspect(status) or ask the parent."
    ),
    "input_schema": _obj({
        "brief": {"type": "string"},
        "role": {"type": "string", "enum": ["scout", "worker", "reviewer"]},
        "model": {"type": "string", "description": "Optional per-agent model; omitted keeps the voice-role default."},
        "max_iters": {"type": "integer"},
    }, ["brief", "role"]),
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
        "scout": "You are a reconnaissance agent. Inspect broadly, run non-mutating probes when useful, and return concrete evidence and file/line references.",
        "reviewer": "You are an adversarial code reviewer. Read the actual diff and relevant code, run focused checks, and identify real defects or explicitly clear it.",
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
- `run` is arbitrary shell for build/test/probes. `process` is for servers, watchers and other long-lived commands. `delegate` can create further fresh-context specialists.
- Use semantic `inspect` actions before broad text guessing. Use `verify` for a persisted test matrix, and `signal` to leave findings/contracts/blockers in the shared DAG mailbox.
- Never put credentials or secret contents in your prose. Code facts, commands, diffs and test output are welcome.
- End with a compact but complete result: what you learned/changed, exact verification, remaining risks, and the next useful integration step. Do not merely narrate intentions.
"""


def _dispatch(request: dict, name: str, args: dict) -> str:
    task_id = request["task_id"]
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    if name == "inspect":
        return forge.inspect(task_id, **clean)
    if name == "edit":
        return forge.edit(task_id, **clean)
    if name == "run":
        return forge.run(task_id, **clean)
    if name == "process":
        return forge.process(task_id, **clean)
    if name == "verify":
        return forge.verify(task_id, **clean)
    if name == "signal":
        return forge.swarm(task_id, "signal", node_id=str(request.get("node_id") or request.get("id") or ""),
                           kind=str(clean.get("kind") or "finding"),
                           message=str(clean.get("message") or ""), files=clean.get("files") or [])
    if name == "delegate":
        # расписка манометра: бриф сочинил ЭТОТ воркер, не она напрямую
        clean.setdefault("spawned_by", str(request.get("id") or ""))
        return forge.agent(task_id, "spawn", **clean)
    if name == "checkpoint":
        return forge.checkpoint(task_id, **clean)
    return f"Unknown tool {name}"


def run(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    return _run_request(request_path, request)


def _run_request(request_path: Path, request: dict) -> int:
    result_path = request_path.parent / "result.json"
    trace: list[dict] = []
    bindings = contextlib.ExitStack()
    try:
        # Clear ambient authority even for legacy requests; always restore it.
        bindings.enter_context(run_context.bind_run(None))
        captured = request.get("run_context")
        # Empty dict is the historical serialized no-context marker.
        if captured is not None and captured != {}:
            if not isinstance(captured, dict):
                raise TypeError("run_context must be an object or null")
            context = run_context.RunContext.from_dict(captured)
            from dataclasses import replace
            context = replace(context, forge_task_id=str(request["task_id"]))
            bindings.enter_context(run_context.bind_run(context))
        role = request.get("role") or "worker"
        tools = [INSPECT_TOOL, RUN_TOOL, PROCESS_TOOL, VERIFY_TOOL, SIGNAL_TOOL, DELEGATE_TOOL]
        if role == "worker":
            tools += [EDIT_TOOL, CHECKPOINT_TOOL]
        messages: list[dict] = [{"role": "user", "content": str(request.get("brief") or "Execute the brief.")}]
        max_iters = int(request.get("max_iters") or 0) or max(20, llm.limits().max_tool_iters * 2)
        reply = ""
        final_model = ""
        # ⚠ 10.08.2026. Цикл мог кончиться ДВУМЯ разными способами, а запись знала только
        # один. Если модель остановилась сама — это «сделано». Если кончились ходы —
        # это «упёрся в потолок», и воркеру при этом сказано «use as many tool turns as
        # the job needs». Дальше forge.py:826 читал `status == "done"` и говорил ей
        # «воркер закончил». Она узнавала о завершении там, где было исчерпание.
        stopped_himself = False
        used = 0
        for _ in range(max_iters):
            used += 1
            # Re-orient for each logical model turn, but freeze that exact frame across
            # transport retries of this turn. A retry must not observe a half-new system
            # prompt; the next successful tool iteration may legitimately see new state.
            system = _system(request)
            response = _chat_with_transport_retry(
                request, system=system, messages=messages, tools=tools,
                logical_iteration=used,
            )
            final_model = response.model
            if response.stop_reason != "tool_use":
                reply = response.text.strip()
                stopped_himself = True
                break
            assistant_blocks, tool_results = [], []
            for block in response.blocks:
                if block.get("type") == "text":
                    assistant_blocks.append(block)
                elif block.get("type") == "tool_use":
                    assistant_blocks.append(block)
                    args = {k: v for k, v in (block.get("input") or {}).items() if v is not None}
                    try:
                        output = _dispatch(request, block.get("name", ""), args)
                    except Exception as exc:
                        output = f"Tool error {type(exc).__name__}: {exc}"
                    trace.append({"tool": block.get("name"), "input": args,
                                  "output": str(output)[:2000], "at": _now()})
                    tool_results.append({"type": "tool_result", "tool_use_id": block.get("id", ""),
                                         "content": str(output)[:12000]})
            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_results})
        if not reply:
            system = _system(request)
            summary_response = _chat_with_transport_retry(
                request, system=system, messages=messages, tools=None,
                logical_iteration=used + 1,
            )
            final_model = summary_response.model
            reply = summary_response.text.strip()
        diff = forge.inspect(request["task_id"], "diff")
        data = {
            "status": "done" if stopped_himself else "stalled",
            "complete": bool(stopped_himself),
            "stop": ("модель остановилась сама" if stopped_himself else
                     "кончились ходы: %d из %d" % (used, max_iters)),
            "iters_used": used, "iters_max": max_iters,
            # Упавшие руки не делают прогон ошибкой — падение руки это сведение, а не
            # провал. Но «done при десяти подряд упавших руках» — тоже неправда, поэтому
            # число едет в запись и видно рядом со статусом.
            "tool_errors": sum(1 for t in trace
                               if str(t.get("output") or "").startswith("Tool error")),
            "finished": _now(), "role": role,
            "model_requested": str(request.get("model") or ""),
            "model": final_model,
            "result": reply, "tool_calls": len(trace), "trace": trace[-30:],
            "diff_tail": diff[-8000:],
        }
        _write(result_path, data)
        forge._event(request["task_id"], "agent_finished", agent_id=request["id"], role=role,
                     summary=(f"{request['id']} done; tools={len(trace)}" if stopped_himself
                              else f"{request['id']} исчерпал ходы {used}/{max_iters}; "
                                   f"tools={len(trace)}"))
        # PASS 30 Этап 1: завершение — входящее событие родительской петли, не повод
        # ждать таймера. Пишем из СВОЕГО процесса, durable; best-effort (не роняет выход).
        forge.emit_unit_event(request["task_id"], request["id"], data, request=request)
        print(f"{request['id']} " + ("done" if stopped_himself
                                          else f"исчерпал ходы {used}/{max_iters}")
              + f"; tools={len(trace)}", flush=True)
        return 0
    except Exception as exc:
        data = {"status": "error", "finished": _now(),
                "model_requested": str(request.get("model") or ""),
                "model": str(locals().get("final_model") or ""),
                "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-8000:],
                "trace": trace[-20:]}
        _write(result_path, data)
        try:
            forge._event(request["task_id"], "agent_error", agent_id=request.get("id"),
                         summary=data["error"])
        except Exception:
            pass
        forge.emit_unit_event(request.get("task_id") or "", request.get("id") or "",
                              data, request=request)
        print(data["traceback"], flush=True)
        return 2
    finally:
        bindings.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    raise SystemExit(run(Path(args.request)))
