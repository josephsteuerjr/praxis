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
import tool_report
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
            with llm.single_provider_attempt():
                return llm.chat("voice", system=system, messages=messages, tools=tools,
                                model=str(request.get("model") or "").strip() or None,
                                **(call_options or {}))
        except llm.BrokenChannelError as exc:
            retry = (attempt < retries and not isinstance(exc,llm.TornStreamError) and getattr(exc,'partial',None) is None
                     and getattr(exc,'request_not_admitted',False) is True)
            if journal is not None:
                partial = getattr(exc, "partial", None)
                reference = None
                if partial is not None:
                    reference = journal.payload(json.dumps({"text": partial.text, "blocks": partial.blocks,
                        "stop_reason": partial.stop_reason, "model": partial.model,
                        'usage':getattr(partial,'usage',{}),'framework':getattr(partial,'framework',''),
                        'provider_observation':getattr(partial,'provider_observation',{}),
                        "assistant": _assistant(partial)}, ensure_ascii=False))
                journal.event("model_transport_error", error=f"{type(exc).__name__}: {exc}",
                              output_ref=reference, attempt=attempt, retry=retry,
                              partial_not_dispatched=partial is not None)
                import forge_usage
                forge_usage.record(request_path.parent.parent.parent,unit_id=request['id'],
                    generation=journal.state['generation'],call_id=f'{logical_iteration}:attempt:{attempt}',
                    kind='transport_refused' if getattr(exc,'request_not_admitted',False) is True and partial is None else 'transport_unknown',
                    output_ref=reference,usage=getattr(partial,'usage',None))
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
        "impact/checks expose normalized semantic and test facts; definition/callers/callees use "
        "the shared code_query evidence engine, coverage and snapshot cursor; mailbox shows worker signals. "
        "Large read replies are exact UTF-8 JSON pages (<=8000 chars): continue with "
        "start=next_start, start_char=next_char, expected_sha256=sha256; a changed file refuses "
        "the old hash. Small reads retain the legacy numbered-line display."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["status", "orientation", "overview", "review", "model", "symbols",
                    "references", "definition", "callers", "callees", "diagnostics", "impact", "checks", "lessons", "mailbox",
                    "read", "list", "search", "diff", "history"], "description": "What to inspect in this task."},
        "path": {"type": "string", "description": "File or directory relative to the task root."}, "query": {"type": "string", "description": "Text or symbol to find."},
        "glob": {"type": "string", "description": "File mask narrowing the search."}, "start": {"type": "integer", "description": "First line, one-based."}, "end": {"type": "integer", "description": "Last line, inclusive."},
        "start_char": {"type": "integer", "description": "Unicode character offset within start line, for paged reads."},
        "expected_sha256": {"type": "string", "description": "Full 64-hex source hash from a read page; pins continuation to those bytes."},
        "module": {"type": "string", "description": "Target defining module for semantic evidence."},
        "limit": {"type": "integer", "description": "Semantic rows per page: default40, maximum200."},
        "cursor": {"type": "string", "description": "Semantic snapshot:offset; changed files refuse continuation."},
    }, ["action"]),
}

VERIFY_TOOL = {
    "name": "verify",
    "description": (
        "Plan or supervise a durable verification matrix. plan derives targeted checks from the "
        "actual diff/impact map; start runs checks outside this model turn; poll gathers exact logs."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["plan", "start", "poll", "stop", "list"], "description": "plan derives checks; start launches them; poll reads results; stop requests cancellation; list shows runs."},
        "verification_id": {"type": "string", "description": "Verification run to poll or stop."}, "commands": {"type": "string", "description": "Explicit commands overriding the derived plan."},
        "full": {"type": "boolean", "description": "Run the authoritative project gate."}, "max_parallel": {"type": "integer", "description": "Maximum concurrent check commands."},
        "timeout": {"type": "integer", "description": "Seconds per command; 0 means unbounded."}, "tail": {"type": "integer", "description": "Trailing log lines to return."},
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
                                                    "result", "claim", "release"], "description": "finding, question, blocker, contract, result, advisory claim or release."},
        "message": {"type": "string", "description": "Finding, question or coordination message."}, "files": {"type": "array", "items": {"type": "string"}, "description": "Task-relative paths this signal concerns."},
    }, ["kind"]),
}

import edit_batch
import editor

EDIT_TOOL = {"name": "edit", "description": 'Change files inside your task root. For one or a few changes call this hand directly. For many mechanical changes a Python script is welcome: import praxis_hands as h and call h.edit, the same editor and shared write lock. Bare open().write or str.replace followed by writing bypasses both. Read first with inspect(action=read) and pass its sha256 as expected_sha256. replace: old must occur exactly once; all=true replaces every non-overlapping occurrence and reports the count. Overlapping matches still refuse; widen old. A confirmed after_sha256 is valid for the next edit. batch: files=[{path, expected_sha256, edits:[{old,new}]}]; every file is checked before any write. mode=snapshot (default) matches the original text; mode=sequential applies replacements in order. A batch stops at its first write failure; later files are untouched. No automatic rollback: reread and revert explicitly with fresh versions. write: new file or full rewrite. patch: unified diff, checked with git apply --check first. Reply is edit: JSON with status. confirmed: written and read back. refused: nothing written. in_doubt: writing may have happened; reread, never retry blind. partial (batch): some files written, some refused; inspect files. ok (patch or remote edit): backend reports success, no local readback promise. Host and Windows tasks refuse batch before sending it; use replace per file.',
             "input_schema": editor.schema(worker=True)}

RUN_TOOL = {
    "name": "run",
    "description": (
        "Run an arbitrary foreground shell command in the task. Full output is kept as evidence; "
        "the reply is capped. timeout=0 means no timeout. Use this to test, build, lint and probe. "
        "Python scripts here can import praxis_hands as h (h.read, h.edit, h.write, h.search): the same editor and shared mutation lock; the current Linux local foreground Forge command context is inherited; refusals raise. Edit files that way, not with str.replace."
    ),
    "input_schema": _obj({"command": {"type": "string", "description": "Foreground shell command to execute."}, "cwd": {"type": "string", "description": "Working directory inside the task root; omitted uses the root."},
                           "timeout": {"type": "integer", "description": "Seconds; 0 means unbounded."}}, ["command"]),
}

RUN_COMMAND_TOOL = {
    "name": "run_command",
    "description": (
        "Run one program in the task with exact argv and no shell: argv[0] is the program (PATH "
        "name or a path relative to cwd), each other item is one argument byte for byte. Quotes, "
        "spaces, ;, $(), *, backslashes and newlines are plain data. Use it for ordinary program "
        "calls; use run when you want shell grammar (pipes, redirects, globs, &&). It never falls "
        "back to shell. Same admission, custody, Stop and full log as run; stdout and stderr are "
        "combined. A missing program or cwd is refused before launch. The reply ends with a "
        "[run_command {...}] facts line and [exit N]; [exit unobserved] after a timeout is not "
        "success. Host and Windows tasks refuse it before sending anything."
    ),
    "input_schema": _obj({"argv": {"type": "array", "items": {"type": "string"}, "description": "Program then its arguments, one array item per argument."},
                           "cwd": {"type": "string", "description": "Working directory inside the task root; omitted uses the root."},
                           "timeout": {"type": "integer", "description": "Seconds; 0 means unbounded."},
                           "stdin_text": {"type": "string", "description": "Optional exact standard input text; omitted means closed stdin."},
                           "env": {"type": "array", "items": {"type": "object", "properties": {
                               "name": {"type": "string", "description": "Variable name."},
                               "value": {"type": "string", "description": "Exact value."}}, "required": ["name", "value"]},
                               "description": "Optional extra environment for this one program: [{name, value}]. The facts line and task receipts name variables only; like any tool argument, the values stay in this run's call record. PRAXIS_* is runtime-owned and refused. Nothing is read from settings."},
                           "outputs": {"type": "array", "items": {"type": "string"}, "description": "Optional files this program should produce; after the run each is reported as created/modified/unchanged/deleted/missing with sha256 and size."},
                           "scratch": {"type": "array", "items": {"type": "string"}, "description": "Optional throwaway paths inside cwd (untracked by Git): removed after the program exits and left out of [changes]; kept if nothing ran or the stop was not proven."}}, ["argv"]),
}

PROCESS_TOOL = {
    "name": "process",
    "description": (
        "Start/poll/stop/list a long-running command without blocking this agent. start returns an "
        "id immediately and keeps the complete log. timeout=0 is unbounded. start takes either "
        "command (a shell string) or argv (one exact program and arguments, no shell), never both."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["start", "poll", "stop", "list"], "description": "start launches a durable command; poll reads it; stop requests cancellation; list shows commands."},
        "process_id": {"type": "string", "description": "Process to poll or stop."}, "command": {"type": "string", "description": "Shell command string to start."},
        "argv": {"type": "array", "items": {"type": "string"}, "description": "Exact program then arguments to start without a shell; instead of command."},
        "cwd": {"type": "string", "description": "Working directory inside the task root."}, "name": {"type": "string", "description": "Human-readable command name."},
        "timeout": {"type": "integer", "description": "Seconds; 0 means unbounded."}, "tail": {"type": "integer", "description": "Trailing log lines to return."},
    }, ["action"]),
}

DELEGATE_TOOL = {
    "name": "delegate",
    "description": (
        "Spawn a child fresh-context worker, poll/stop agent descendants, or disposition their "
        "exact terminal result. The parent identity comes from your worker request, never from "
        "model arguments. Stop includes jobs owned by the selected agent subtree; unrelated task jobs are preserved. "
        "disposition requires the full result_sha256 returned by poll. To execute a queued editing "
        "child, wait explicitly yields your shared-root writer slot, blocks all your other work, "
        "and returns its full terminal result before you regain ownership. First settle detached jobs."
    ),
    "input_schema": _obj({
        "action": {"type": "string", "enum": ["spawn", "list", "poll", "stop", "disposition", "wait"], "description": "spawn a child, list descendants, poll/stop one, disposition its exact result, or wait while yielding the writer slot."},
        "agent_id": {"type": "string", "description": "Child or descendant agent selected for this action."}, "result_sha256": {"type": "string", "description": "Full result hash from poll; required for disposition."},
        "disposition": {"type": "string", "enum": ["integrated", "irrelevant", "deferred"], "description": "integrated, irrelevant or deferred: how the exact child result was handled."},
        "brief": {"type": "string", "description": "Concrete assignment for the child."},
        "role": {"type": "string", "enum": ["scout", "worker", "reviewer"], "description": "scout and reviewer inspect; worker may edit and execute."},
        "model": {"type": "string", "description": "Optional per-agent model; omitted keeps the voice-role default."},
        "max_iters": {"type": "integer", "description": "Model-turn ceiling for the child; omitted uses the runtime default."},
    }, ["action"]),
}

CHECKPOINT_TOOL = {
    "name": "checkpoint",
    "description": "Commit the current task working tree as a recoverable checkpoint.",
    "input_schema": _obj({"message": {"type": "string", "description": "What this recoverable task commit contains."}}, ["message"]),
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
- Change files with `edit` (replace, batch, write, patch) after inspect(action=read). When a script suits the job, write Python and use import praxis_hands as h (h.read, h.edit): the same version check and write lock. Bare file writes, including str.replace followed by open().write, bypass both.
- A worker may use `run_command` for one program with exact arguments, `run` for shell commands, builds, tests and probes, `process` for long-lived commands, and `verify` for a persisted matrix. A scout or reviewer cannot launch them; share needed checks with a worker using `signal`. Stop of an agent branch also requests Stop for its descendant-owned process/verification jobs. A job keeps custody until its whole command tree is proven settled; an unknown outcome is never a clean Stop. Legacy task-scoped jobs without a parent require explicit job or whole-task control.
- Use semantic `inspect` actions before broad text guessing. `delegate` can create further specialists within your role; `signal` leaves findings/contracts/blockers in the shared DAG mailbox.
- Editing children queue behind your single shared-root writer slot. `delegate(action="wait", agent_id=...)` explicitly yields that slot to ONE immediate worker child and returns its full result. You cannot work meanwhile; settle detached processes/checks first. Parent Stop/Pause still fences the child.
- Never put credentials or secret contents in your prose. Code facts, commands, diffs and test output are welcome.
- Independent reviewers are voluntary help for ordinary tasks, not permission to finish. Retain their exact findings; an author's decision does not rename BLOCKED/findings to clear. Conflicting source updates have a separate mandatory battery/panel protocol.
- End with a compact but complete result: what you learned/changed, exact verification, remaining risks, and the next useful integration step. Do not merely narrate intentions.
"""


def _dispatch(request: dict, name: str, args: dict) -> str:
    task_id = request["task_id"]
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    if any(k.startswith('_') or k in {'task_id','root','parent_unit_id','run_context','spawned_by'} for k in clean):
        return 'Worker authority fields are runtime-owned; tool request refused.'
    role = str(request.get("role") or "worker")
    # The provider may return a tool name absent from the offered schema. Keep
    # read-oriented roles read-only at the actual dispatch boundary, not only in
    # the prompt or tool list. Arbitrary shell/verification commands can mutate.
    if role != "worker" and name not in {"inspect", "signal", "delegate",'transcript'}:
        return f"{role}: tool {name} refused (read-oriented role)."
    if role != "worker" and name == "inspect" and clean.get("action") not in {
        "status", "orientation", "overview", "review", "model", "symbols",
        "references", "definition", "callers", "callees", "diagnostics", "impact", "checks", "lessons", "mailbox",
        "read", "list", "search", "diff", "history",
    }:
        return f"{role}: inspect action refused (read-oriented role)."
    if name == 'transcript':
        if set(clean)-{'sha256','offset','limit'}:return 'Unsupported transcript parameters.'
        address=request.get('_verified_request_path')
        if not address:raise ValueError('verified worker identity required')
        forge._worker_parent(task_id,address)
        return json.dumps(forge_runtime.read_payload(Path(address).parent,clean['sha256'],
                          int(clean.get('offset',0)),int(clean.get('limit',8000))),ensure_ascii=False)
    if name == "inspect":
        return forge.inspect(task_id, **clean)
    if name == "edit":
        return editor.edit(task_id=task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    if name == "run":
        return forge.run(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
    if name == "run_command":
        return forge.run_command(task_id, _parent_request_path=request.get("_verified_request_path", ""), **clean)
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


TRANSCRIPT_TOOL={'name':'transcript','description':'Read an immutable full output from this execution by exact SHA; offset and limit page Unicode characters. Other task/private directories are inaccessible.',
    'input_schema':_obj({'sha256':{'type':'string', "description": "Exact full output SHA from this execution."},'offset':{'type':'integer', "description": "Zero-based Unicode character offset."},'limit':{'type':'integer', "description": "Maximum Unicode characters in this page."}},['sha256'])}


def _route_identity(request):
    config=getattr(llm,'_config',None)
    if not callable(config):return None
    value=config()
    binding={'voice':value.get('roles',{}).get('voice',{}),'frameworks':value.get('frameworks',{}),
             'requested_model':request.get('model','')}
    # Credentials/endpoints are never serialized; only this private digest is
    # retained to detect an incompatible route/authority change before reuse.
    return hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()


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
            journal.event("tool_dispatch", call_id=call["id"], tool=call["name"],
                          receipt={"kind": "python", "handler": (f"{type(adapter).__module__}.{type(adapter).__qualname__}.dispatch"
                                      if adapter is not None else "forge_worker._dispatch"),
                                   "status": "entered"})
            report = tool_report.new()
            report_token = tool_report.attach(report)
            tool_error = None
            try:
                output = (adapter.dispatch(request, call["name"], args) if adapter is not None
                          else _dispatch(request, call["name"], args))
            except Exception as exc:
                output = f"Tool error {type(exc).__name__}: {exc}"
                tool_error = exc
            finally:
                tool_report.detach(report_token)
            output = str(output)
            output_ref = journal.payload(output)
            event = journal.event("tool_result", call_id=call["id"], tool=call["name"],
                                  input_ref=input_ref, output_ref=output_ref,
                                  executions=report["executions"],
                                  **tool_report.summary(report, error=tool_error))
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
        # Freeze this execution's first system frame. New observations arrive as
        # tool results/messages; refreshing orientation would replace old input.
        system = state.get('system') or (adapter.system(request) if adapter is not None else _system(request))
        if state.get('route_identity') != _route_identity(request):
            raise forge_runtime.ContinuationRefused('provider route changed; explicit new epoch/rework required')
        journal.save("summary_in_flight" if summarizing else "model_in_flight", "model",
                     system=system)
        journal.event("model_start", summary=summarizing, model_requested=request.get("model", ""))
        import forge_usage
        usage_call=f'{state["iteration"]}:{"summary" if summarizing else "model"}'
        usage_directory=request_path.parent.parent.parent
        forge_usage.record(usage_directory,unit_id=request['id'],generation=state['generation'],
                           call_id=usage_call,kind='model_start')
        response = _chat_with_transport_retry(
            request, request_path=request_path, system=system, messages=state["messages"],
            tools=None if summarizing else state["tools"],
            logical_iteration=state["iteration"] + int(summarizing),
            call_options=state["call_options"], journal=journal,
        )
        state["model"] = response.model
        state["model_stop_reason"] = str(response.stop_reason or "")
        response_ref = journal.payload(json.dumps({"text": response.text, "blocks": response.blocks,
            "stop_reason": response.stop_reason, "model": response.model,
            'framework':getattr(response,'framework',''),'provider_observation':getattr(response,'provider_observation',{}),
            "usage": getattr(response, "usage", {}), "assistant": _assistant(response)}, ensure_ascii=False))
        journal.event("model_result", output_ref=response_ref, summary=summarizing, model=response.model)
        forge_usage.record(usage_directory,unit_id=request['id'],generation=state['generation'],
                           call_id=usage_call,kind='model_result',usage=getattr(response,'usage',None),
                           response_ref=response_ref,model=response.model,framework=getattr(response,'framework',''))
        if summarizing or response.stop_reason != "tool_use":
            # A textual answer is not completion of an adapter-owned transaction.
            # Keep the same journal and budget; never replay tools or reset turns.
            continuation = getattr(adapter, "completion_feedback", None)
            if not summarizing and response.stop_reason == "end_turn" and callable(continuation):
                feedback = continuation(request)
                if feedback:
                    state["messages"].append(_assistant(response))
                    state["messages"].append({"role": "user", "content": feedback})
                    journal.event("completion_deferred", reason=feedback)
                    journal.save("model_ready", "ready", reply="", stopped_himself=False)
                    continue
            state["reply"] = response.text.strip()
            if not summarizing:
                state["stopped_himself"] = response.stop_reason == "end_turn"
            state["messages"].append(_assistant(response))
            # Explicit incomplete output (including hidden-only max_tokens) is
            # retained, not an empty response to replay or summarize as success.
            journal.save("finalize" if state["reply"] or summarizing or response.stop_reason != "end_turn"
                         else "summary_ready", "finishing")
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
        # Journal/control admission can fail before captured context is parsed.
        # None of those failures may borrow the caller's ambient identity.
        bindings.enter_context(run_context.bind_run(None))
        journal = forge_runtime.Journal(request_path.parent)
        prior = forge._read_json(request_path.parent / "result.json")
        if isinstance(prior, dict) and prior.get("status") not in forge._LIVE_UNIT_STATES:
            return 0  # terminal redelivery cannot start another model/tool turn
        captured = request.get("run_context")
        if captured is not None and captured != {}:
            if not isinstance(captured, dict):
                raise TypeError("run_context must be an object or null")
            context = run_context.RunContext.from_dict(captured)
            from dataclasses import replace
            context = replace(context, forge_task_id=str(request["task_id"]))
            bindings.enter_context(run_context.bind_run(context))
        _control(request_path)
        role = request.get("role") or "worker"
        call_options = adapter.prepare(llm, request) if adapter is not None else None
        if adapter is not None:
            tools = list(adapter.tools)
        else:
            tools = [INSPECT_TOOL, SIGNAL_TOOL, DELEGATE_TOOL,TRANSCRIPT_TOOL]
            if role == "worker":
                tools += [EDIT_TOOL, RUN_TOOL, RUN_COMMAND_TOOL, PROCESS_TOOL, VERIFY_TOOL, CHECKPOINT_TOOL]
        adapter_id = (type(adapter).__module__ + "." + type(adapter).__qualname__) if adapter is not None else ""
        state = journal.start(request, {
            "next_boundary": "model_ready", "iteration": 0,
            "max_iters": int(request.get("max_iters") or 0) or max(20, llm.limits().max_tool_iters * 2),
            "messages": [{"role": "user", "content": str(request.get("brief") or "Execute the brief.")}],
            "tools": tools, "call_options": call_options, "adapter_id": adapter_id,
            'route_identity':_route_identity(request),
            "trace": [], "pending": {}, "reply": "", "model": "", "stopped_himself": False,
        })
        _loop(journal, request_path, request, adapter)
        _control(request_path)
        trace = journal.trace()
        diff = str(adapter.diff() if adapter is not None else forge.inspect(request["task_id"], "diff"))
        diff_ref = journal.payload(diff)
        stopped = state["stopped_himself"]
        model_stop = str(state.get("model_stop_reason") or "")
        data = {
            "status": "done" if stopped else "stalled", "complete": bool(stopped),
            "stop": ("модель остановилась сама" if stopped else
                     (f"ответ не завершён: stop_reason={model_stop}" if model_stop != "end_turn" else
                      "кончились ходы: %d из %d" % (state["iteration"], state["max_iters"]))),
            "stop_reason": model_stop,
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
