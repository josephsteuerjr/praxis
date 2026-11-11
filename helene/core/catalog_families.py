"""Explicit action families; no aliases for retired model-facing tool names."""
from __future__ import annotations


class PreflightRefusal(ValueError):
    """Arguments refused before invoking any backend effect, never an uncertain failure."""


def _required(**values):
    missing = [name for name, value in values.items() if not str(value or "").strip()]
    if missing:
        raise PreflightRefusal("Required for this action: " + ", ".join(missing))


def proposal(action: str, id: str = "", reason: str = "", title: str = "",
             why: str = "", review: str = "", checked: str = "",
             override_reason: str = "", reviewer: str = "",
             risk_action: str = "list", pattern: str = "") -> str:
    import agent as a
    if action == "start":
        return a.tool_start_proposal(reason)
    if action == "list":
        return a.tool_list_proposals()
    if action == "diff":
        _required(id=id)
        return a.tool_proposal_diff(id)
    if action == "submit":
        _required(id=id, title=title)
        return a.tool_submit_proposal(id, title, why, review, checked, override_reason, reviewer)
    if action == "test":
        # This is the existing current-open-proposal route, not an arbitrary id.
        if id:
            raise PreflightRefusal("test uses the current open proposal; omit id")
        return a.workshop.run_tests("self")
    if action == "risk":
        if risk_action not in {"list", "add", "remove"}:
            raise PreflightRefusal("risk_action: list | add | remove")
        if risk_action != "list":
            _required(pattern=pattern)
        return a.tool_manage_autonomy(risk_action, pattern)
    raise PreflightRefusal("action: start | list | diff | submit | test | risk")


def project(action: str, name: str = "", brief: str = "", packages: str = "") -> str:
    import workshop as w
    if action == "list":
        return w.project_list()
    if action not in {"create", "status", "test", "pip"}:
        raise PreflightRefusal("action: create | list | status | test | pip")
    _required(name=name)
    if action == "create":
        return w.project_create(name, brief)
    if action == "status":
        return w.project_status(name)
    if action == "test":
        if name == "self":
            raise PreflightRefusal("Core proposal tests: proposal(action='test')")
        return w.run_tests(name)
    _required(packages=packages)
    return w.pip_install(name, packages)


def memory_graph(action: str, name: str, to: str = "", depth: int = 1) -> str:
    import agent as a
    bound = a._catalog_for_turn().get("memory_graph")
    if bound is not None:
        offered = bound["input_schema"]["properties"]["action"]["enum"]
        if action not in offered:
            raise PreflightRefusal("Action outside this observer's offered memory_graph schema")
    _required(name=name)
    if action == "look":
        if to:
            raise PreflightRefusal("Use action='path' with to")
        return a.tool_connections(name, depth)
    if action not in {"path", "alias", "forget"}:
        raise PreflightRefusal("action: look | path | alias | forget")
    _required(to=to)
    if action == "path":
        return a.tool_connections(name, depth, to)
    if action == "alias":
        return a.tool_add_alias(name, to)
    return a.tool_forget_connection(name, to)


def field(description, type="string", **extra):
    return {"type": type, "description": description, **extra}


def schema(name, description, properties, required=()):
    return {"name": name, "description": description, "input_schema": {
        "type": "object", "properties": properties, "required": list(required)}}


PROPOSAL_TOOL = schema("proposal",
    "Work on your own core through a separate proposal worktree: start, inspect diff, "
    "test the current open proposal, submit with evidence/reviewer or explicit override, list. "
    "risk manages advisory low-risk patterns; it does not grant authority or replace review.", {
        "action": field("Operation", enum=["start", "diff", "test", "submit", "list", "risk"]),
        "id": field("Proposal id for diff or submit; test uses the current open proposal"),
        "reason": field("Why to open a new proposal"),
        "title": field("Submit: concise change title"),
        "why": field("Submit: purpose and expected behavior"),
        "review": field("Submit: review evidence"),
        "checked": field("Submit: actual verification evidence"),
        "override_reason": field("Submit: explicit reason to override an advisory result"),
        "reviewer": field("Submit: independent reviewer verdict and provenance"),
        "risk_action": field("risk: list (default), add or remove a pattern", enum=["list", "add", "remove"]),
        "pattern": field("risk add/remove: exact path pattern"),
    }, ["action"])

PROJECT_TOOL = schema("project",
    "Create/list/inspect workshop projects; run their tests or install pip dependencies into "
    "their venv. Use python or shell with cwd for execution; proposal(action='test') tests core.", {
        "action": field("Operation", enum=["create", "list", "status", "test", "pip"]),
        "name": field("Project name, required except for list"),
        "brief": field("create: project purpose"),
        "packages": field("pip: package requirements, space separated"),
    }, ["action"])

MEMORY_GRAPH_TOOL = schema("memory_graph",
    "Inspect memory neighbors (look) or shortest path between two nodes (path); record an "
    "alias (name -> canonical to), or forget their edge and the related dossier entry. "
    "Uses the same memory scope and provenance as the original graph operations.", {
        "action": field("Operation", enum=["look", "path", "alias", "forget"]),
        "name": field("Person/topic node; alias uses the alternate spelling"),
        "to": field("Second node for path/forget; canonical spelling for alias"),
        "depth": field("look: neighbor depth, default 1", type="integer", enum=[1, 2]),
    }, ["action", "name"])

PYTHON_TOOL = schema("python",
    "Run Python directly, without shell quoting. h is already praxis_hands: h.read, h.edit, "
    "h.write, h.search use the same editor, version checks and receipts. "
    "h.hand('name', **args) calls any hand in this turn's authorized catalogue through the live "
    "parent dispatcher, with its rights, durable receipts and Stop. r['outcome'] is the hand's own "
    "outcome (confirmed/refused/in_doubt/partial/ok/failed, unknown if it reports none); print "
    "r['content'] for its reply; r also has result_ref, call_id and image_count. "
    "h.describe('name')['content'] gives the full schema. InDoubt means check the receipt, never "
    "repeat the effect blindly. The bridge expires when this outer call returns or reaches its "
    "ceiling; a nested hand already started when the script stops waiting still finishes and its "
    "receipt follows [exit N]. Standalone workers/background survivors have no live bridge. "
    "Print results you need. "
    "cwd defaults to your local home, or the local Forge task root with task_id. Local task execution "
    "inherits custody and Stop; remote tasks are refused before execution. At home timeout=0 "
    "uses the configured shell default (normally 30s), capped at 600s; in a task 0 means unlimited. "
    "The output ends with [exit N]; a timeout closes the ordinary process group. Background "
    "descendants survive normal completion. Prefer h.edit to whole-file string replacement.", {
        "code": field("Python source, exact line numbers preserved; h is preloaded"),
        "cwd": field("Working directory; task paths are relative to the task root"),
        "timeout": field("Seconds; 0 uses home default or no task deadline", type="integer"),
        "task_id": field("Optional local Forge task; omitted means home"),
    }, ["code"])
