"""Persistent DAG, mailbox and advisory ownership for Praxis Forge workers."""

from __future__ import annotations

import datetime as dt
from collections import deque
import json
import os
import re
from pathlib import Path
from typing import Callable


KINDS = {"finding", "question", "blocker", "contract", "result", "claim", "release"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _plan_path(task_dir: Path) -> Path:
    return task_dir / "swarm.json"


def load(task_dir: Path) -> dict:
    row = _load(_plan_path(task_dir), {})
    return row if isinstance(row, dict) else {}


def _node_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    return clean[:64]


def create(task_dir: Path, raw_plan: str | list | dict, *, max_parallel: int = 8) -> dict:
    if load(task_dir):
        raise ValueError("a durable plan already exists; use rework, not replacement")
    if isinstance(raw_plan, str):
        try:
            raw_plan = json.loads(raw_plan)
        except ValueError as exc:
            raise ValueError(f"plan is not valid JSON: {exc}") from exc
    if isinstance(raw_plan, dict):
        raw_nodes = raw_plan.get("nodes") or []
    else:
        raw_nodes = raw_plan or []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("plan needs a non-empty nodes list")
    nodes = []
    ids: set[str] = set()
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            raise ValueError(f"node {index + 1} is not an object")
        nid = _node_id(item.get("id") or f"step-{index + 1}")
        if not nid or nid in ids:
            raise ValueError(f"duplicate/empty node id: {nid!r}")
        ids.add(nid)
        role = str(item.get("role") or "worker").strip().lower()
        if role not in {"scout", "worker", "reviewer"}:
            raise ValueError(f"node {nid}: role must be scout|worker|reviewer")
        brief = str(item.get("brief") or "").strip()
        if not brief:
            raise ValueError(f"node {nid}: brief is required")
        deps = [_node_id(dep) for dep in (item.get("deps") or [])]
        owns = [str(path).replace("\\", "/") for path in (item.get("owns") or []) if str(path).strip()]
        nodes.append({"id": nid, "role": role, "brief": brief, "deps": deps,
                      "phase": str(item.get("phase") or ""),
                      "success_criteria": list(item.get("success_criteria") or []),
                      "owns": owns, "status": "pending", "agent_id": "", "attempts": []})
    for node in nodes:
        missing = [dep for dep in node["deps"] if dep not in ids]
        if missing:
            raise ValueError(f"node {node['id']}: unknown deps {missing}")
        if node["id"] in node["deps"]:
            raise ValueError(f"node {node['id']}: self dependency")
    graph = {node["id"]: node["deps"] for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(nid: str) -> None:
        if nid in visiting:
            raise ValueError(f"cycle at {nid}")
        if nid in visited:
            return
        visiting.add(nid)
        for dep in graph[nid]:
            visit(dep)
        visiting.remove(nid)
        visited.add(nid)

    for nid in graph:
        visit(nid)
    plan = {"version": 2, "created": _now(), "updated": _now(),
            "max_parallel": max(1, min(int(max_parallel or 8), 32)), "nodes": nodes}
    _atomic(_plan_path(task_dir), plan)
    return plan


def dependency_reason(node: dict, by_id: dict) -> str:
    """Eligibility of the EXACT prerequisite revisions consumed by this attempt."""
    bound = node.get("dependency_results") or {}
    stale = [dep for dep in node.get("deps") or []
             if by_id.get(dep, {}).get("status") != "done" or not bound.get(dep)
             or bound[dep] != by_id.get(dep, {}).get("accepted_sha256")]
    return "dependency result revision changed: " + ",".join(stale) if stale else ""


def refresh(task_dir: Path, agent_state: Callable[[str], dict]) -> dict:
    """Read/modify/write; callers serialize with Forge's task mutation mutex."""
    plan = load(task_dir)
    if not plan:
        return {}
    before = [dict(node) for node in plan.get("nodes") or []]
    legacy = plan.get('version', 1) == 1
    if legacy:
        # v1 made dependency failure terminal even before a node had run.
        # Restore only those unexecuted reservations; owner Stop and every
        # actual attempt remain fenced. ready_nodes still requires acceptance.
        for node in plan.get('nodes') or []:
            if (node.get('status') == 'blocked' and not node.get('agent_id')
                    and not node.get('reserved_agent_id') and not node.get('attempts')):
                node.update(status='pending', finished='', queued_reason='awaiting accepted dependencies')
        plan['version'] = 2
    for node in plan.get("nodes") or []:
        aid = str(node.get("agent_id") or "")
        if not aid:
            continue
        state = agent_state(aid)
        status = str(state.get("status") or "lost")
        digest = str(state.get("result_sha256") or "")
        decision = state.get("current_disposition") or {}
        accepted = (status == "done" and bool(digest) and decision.get("disposition") == "integrated"
                    and decision.get("result_sha256") == digest)
        mapped = {"done": "done" if accepted else "awaiting_acceptance",
                  "error": "failed", "failed": "failed", "timed_out": "failed",
                  "stopped": "stopped", "lost": "lost", "unknown": "unknown",
                  "queued": "queued", "stalled": "stalled"}.get(status, "running")
        updates = {"status": mapped, "execution_status": status,
                   "result_sha256": digest, "acceptance": decision.get("disposition") or "pending",
                   "accepted_sha256": digest if accepted else "",
                   "queued_reason": str(state.get("queued_reason") or ""),
                   "finished": state.get("finished", "") if mapped not in {"running", "queued"} else ""}
        if any(node.get(key) != value for key, value in updates.items()):
            node.update(updates)
    by_id = {node["id"]: node for node in plan.get("nodes", [])}
    # Effective acceptance follows the DAG, not storage order. A stale B must
    # invalidate C even if B's own immutable result/disposition did not change.
    # The full receipts remain in the unit; accepted_sha256 is current eligibility.
    children = {nid: [] for nid in by_id}
    remaining = {nid: len(node.get("deps") or []) for nid, node in by_id.items()}
    for nid, node in by_id.items():
        for dep in node.get("deps") or []:
            children[dep].append(nid)
    ready = deque(nid for nid, count in remaining.items() if not count)
    while ready:
        nid = ready.popleft()
        node = by_id[nid]
        if node.get("status") == "done" and node.get("deps"):
            if dependency_reason(node, by_id):
                node.update(status="stale_dependencies", accepted_sha256="",
                            queued_reason="dependency result revision changed")
        for child in children[nid]:
            remaining[child] -= 1
            if not remaining[child]:
                ready.append(child)
    if legacy or plan.get("nodes") != before:
        plan["updated"] = _now()
        _atomic(_plan_path(task_dir), plan)
    return plan


def ready_nodes(plan: dict) -> list[dict]:
    nodes = plan.get("nodes") or []
    accepted = {node.get("id"): node.get("status") == "done"
                and bool(node.get("result_sha256"))
                and node.get("accepted_sha256") == node.get("result_sha256") for node in nodes}
    by_id = {node["id"]: node for node in nodes}
    # A queued attempt bound to an old dependency cannot start. Retain its
    # identity/history, but let upstream repair use the otherwise idle slot.
    running = sum(1 for node in nodes
                  if node.get("status") in {"starting", "running", "unknown"}
                  or (node.get("status") == "queued" and not dependency_reason(node, by_id)))
    slots = max(0, int(plan.get("max_parallel") or 1) - running)
    ready = []
    for node in nodes:
        if node.get("status") != "pending":
            continue
        deps = node.get("deps") or []
        if all(accepted.get(dep) for dep in deps):
            ready.append(node)
            node.pop("queued_reason", None)
        else:
            # A repair can satisfy this dependency later. Do not terminalize the
            # dependent simply because the first execution failed or was deferred.
            node["queued_reason"] = "awaiting accepted dependencies: " + ",".join(
                dep for dep in deps if not accepted.get(dep))
    return ready[:slots]


def rework(task_dir: Path, node_id: str, brief: str, result_sha256: str) -> dict:
    """Keep the logical DAG node but retain every immutable previous unit/result."""
    plan = load(task_dir)
    node = next((n for n in plan.get("nodes", []) if n.get("id") == node_id), None)
    if not node or not node.get("agent_id") or not brief.strip():
        raise ValueError("rework needs an existing executed node and a concrete correction brief")
    if (node.get("status") in {"starting", "running", "queued", "unknown"}
            or not result_sha256 or node.get("result_sha256") != result_sha256):
        raise ValueError("rework needs the current exact terminal result hash")
    prior = {key: value for key, value in node.items() if key != "attempts"}
    node.setdefault("attempts", []).append(prior)
    node.update(previous_unit_id=node["agent_id"], previous_result_sha256=result_sha256,
                agent_id="", brief=brief.strip(), status="pending", execution_status="pending",
                result_sha256="", acceptance="pending", accepted_sha256="", finished="",
                reserved_agent_id="")
    save(task_dir, plan)
    return plan


def save(task_dir: Path, plan: dict) -> None:
    plan["updated"] = _now()
    _atomic(_plan_path(task_dir), plan)


def signal(task_dir: Path, *, node_id: str = "", agent_id: str = "", kind: str,
           message: str, files: list[str] | None = None) -> dict:
    kind = str(kind or "finding").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind: " + " | ".join(sorted(KINDS)))
    row = {"at": _now(), "node_id": _node_id(node_id), "agent_id": str(agent_id or ""),
           "kind": kind, "message": str(message or "").strip()[:12000],
           "files": [str(path).replace("\\", "/") for path in (files or [])][:100]}
    if not row["message"] and kind not in {"claim", "release"}:
        raise ValueError("message is required")
    mailbox = task_dir / "mailbox.jsonl"
    mailbox.parent.mkdir(parents=True, exist_ok=True)
    with mailbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if kind in {"claim", "release"}:
        claims_path = task_dir / "claims.json"
        claims = _load(claims_path, {})
        claims = claims if isinstance(claims, dict) else {}
        owner = row["node_id"] or row["agent_id"] or "unknown"
        conflicts = []
        for file in row["files"]:
            previous = claims.get(file)
            if kind == "claim":
                if previous and previous != owner:
                    conflicts.append({"file": file, "owner": previous})
                claims[file] = owner
            elif previous == owner:
                claims.pop(file, None)
        _atomic(claims_path, claims)
        row["conflicts"] = conflicts
    return row


def mailbox(task_dir: Path, limit: int = 100) -> list[dict]:
    try:
        lines = (task_dir / "mailbox.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, min(limit, 500)):]:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
        except ValueError:
            continue
    return out


def summary(plan: dict, messages: list[dict] | None = None) -> str:
    if not plan:
        return "У задачи нет swarm-плана."
    lines = [f"swarm max_parallel={plan.get('max_parallel')} updated={plan.get('updated')}"]
    for node in plan.get("nodes") or []:
        deps = ",".join(node.get("deps") or []) or "-"
        owns = ",".join(node.get("owns") or []) or "-"
        lines.append(f"- {node.get('id')} [{node.get('status')}] {node.get('role')} "
                     f"phase={node.get('phase') or '-'} acceptance={node.get('acceptance') or 'pending'} "
                     f"sha256={node.get('result_sha256') or '-'} "
                     f"agent={node.get('agent_id') or '-'} deps={deps} owns={owns}: {node.get('brief')}"
                     + (f"; queued: {node['queued_reason']}" if node.get('queued_reason') else ""))
    if messages:
        lines.append("mailbox:")
        for row in messages[-20:]:
            lines.append(f"- {row.get('at')} {row.get('kind')} {row.get('node_id') or row.get('agent_id')}: "
                         f"{row.get('message') or ','.join(row.get('files') or [])}")
    return "\n".join(lines)
