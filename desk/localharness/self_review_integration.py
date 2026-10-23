"""Owner controls and lifecycle hooks for Forge-backed source review."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import time
import uuid

from localharness import self_review


log = logging.getLogger("helene.self-review")
SCHEMA = "helene.self-review.status.v1"
ACTIONS = {"request", "cancel", "disposition"}
DECISIONS = {"accepted", "fix", "deferred", "continue-with-reason"}
TOOL = {
    "name": "self_review",
    "description": "Read source-review status or request independent Forge reviewers. Source-only review never proves installed behavior. Available only in an owner dialogue. A contested update requires review even when optional reviews are off. Use explicit disposition with the exact subject_sha256 and reason; this does not rewrite the critic verdict.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "request", "cancel", "disposition"]},
        "id": {"type": "string"}, "depth": {"type": "string", "enum": list(self_review.DEPTHS)},
        "intent": {"type": "string"}, "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "retry": {"type": "boolean"},
        "reason": {"type": "string"}, "subject_sha256": {"type": "string"}},
        "required": ["action"]},
}


def _root(tree):
    return Path(tree) / "memory" / ".self-review"


def _read(path):
    return self_review._read(path) or {}


def command(tree, body):
    if not isinstance(body, dict) or body.get("action") not in ACTIONS:
        raise ValueError("action must be request, cancel or disposition")
    value = {key: body[key] for key in
             ("action", "id", "depth", "intent", "decision", "reason", "subject_sha256", "retry") if key in body}
    if "retry" in value and not isinstance(value["retry"], bool):
        raise ValueError("retry must be a boolean")
    if value.get("depth") is not None and value["depth"] not in self_review.DEPTHS:
        raise ValueError("depth must be quick, normal or deep")
    for key in ("id", "intent", "decision", "reason", "subject_sha256"):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 12000):
            raise ValueError(key + " must be text of at most 12000 characters")
    if value["action"] != "request" and not re.fullmatch(r"review-[0-9a-f]{16}", value.get("id", "")):
        raise ValueError("review id required")
    if value["action"] == "disposition":
        if (value.get("decision") not in DECISIONS or not value.get("reason", "").strip()
                or not re.fullmatch(r"[0-9a-f]{64}", value.get("subject_sha256", ""))):
            raise ValueError("disposition, reason and exact subject_sha256 required")
    command_id = "command-" + uuid.uuid4().hex
    self_review._write(_root(tree) / "commands" / (command_id + ".json"),
                       {**value, "command_id": command_id, "created": time.time(), "state": "queued"})
    return {"ok": True, "command_id": command_id, "state": "queued"}


def status(tree, config=None, review_id=""):
    root = _root(tree)
    runtime = _read(root / "runtime.json")
    reviews = [_read(path) for path in root.glob("review-*/request.json")]
    if review_id:
        reviews = [row for row in reviews if row.get("id") == review_id]
    commands = [_read(path) for path in (root / "commands").glob("command-*.json")]
    fresh = 0 <= time.time() - float(runtime.get("at") or 0) < 15
    capability = {"supported": runtime.get("supported", False),
                  "available": fresh and bool(runtime.get("supported")),
                  "reason": runtime.get("reason") or "Runner has not published self-review capability."}
    return {"schema": SCHEMA, "settings": self_review.settings((config or {}).get("self_review", {})),
            "reviews": sorted(reviews, key=lambda row: row.get("created", 0), reverse=True),
            "commands": sorted(commands, key=lambda row: row.get("created", 0), reverse=True),
            "capability": capability, "runtime": runtime}


class Integration:
    def __init__(self, tree, code_dir, app_root, forge, config_path, agent):
        self.tree, self.code_dir, self.app_root = map(Path, (tree, code_dir, app_root))
        self.config_path, self.agent = Path(config_path), agent
        self.service = self_review.Service(tree, code_dir, app_root, forge,
                                          self._config().get("self_review", {}))
        self.seen_config = None
        self.next_tick = 0.0

    def _config(self):
        from localharness import boot
        value = json.loads(boot.read_config_text(self.config_path))
        if not isinstance(value, dict):
            raise ValueError("helene.json must be an object")
        return value

    def install(self):
        if getattr(self.agent, "SELF_EDIT_REVIEW_API", 0) == 1:
            self.agent.SELF_EDIT_REVIEW_HOOK = self.after_self_edit
        tools, impl = getattr(self.agent, "BASE_TOOLS", None), getattr(self.agent, "TOOL_IMPL", None)
        if isinstance(tools, list) and isinstance(impl, dict):
            def hand(action="status", **body):
                context = getattr(self.agent, "_TURN_CHANNEL", None)
                channel = context.get() if context is not None else None
                if channel is None or not getattr(channel, "owner", False):
                    return "Self-review controls are available only in an owner dialogue."
                try:
                    if action == "status":
                        result = status(self.tree, self._config(), str(body.get("id") or ""))
                    else:
                        result = command(self.tree, {**body, "action": action})
                    return json.dumps(result, ensure_ascii=False)
                except (OSError, ValueError) as exc:
                    return "Self-review request not saved: " + str(exc)
            impl[TOOL["name"]] = hand
            if not any(row.get("name") == TOOL["name"] for row in tools if isinstance(row, dict)):
                tools.append(TOOL)
            purposes = getattr(self.agent, "HAND_PURPOSE", None)
            if isinstance(purposes, dict):
                purposes[TOOL["name"]] = "request independent source review and read its exact evidence"
        self.publish()
        return self

    def publish(self, error=""):
        supported = getattr(self.service.forge, "SELF_REVIEW_API", 0) == 1
        hook = getattr(self.agent, "SELF_EDIT_REVIEW_API", 0) == 1
        reason = ("Source-only Forge review; installed/runtime checks remain separate." if supported
                  else "Selected preserved Forge lacks self-review API v1; shipped stock code is not the executing code.")
        self_review._write(_root(self.tree) / "runtime.json", {
            "at": time.time(), "supported": supported, "self_edit_supported": hook,
            "reason": reason, "code": str(self.code_dir), "error": error,
            "self_edit_note": "hook installed" if hook else "Selected agent lacks self-edit review callback API v1."})

    def after_self_edit(self, sha, message, paths):
        source_paths = [str(path) for path in paths if self_review._code_file(Path(path))
                        and not any(part.lower() in self_review.EXCLUDED for part in Path(path).parts)]
        if not source_paths:
            return None
        sources = {"agent": self.code_dir, "app": self.app_root / "app"}
        if any((self.tree / path).is_file() for path in source_paths):
            sources["home_code"] = self.tree
        return self.service.request(trigger="after_self_edit", operation_id=str(sha or ""),
                                    intent=str(message), criteria=["Inspect self-edited source: " + ", ".join(source_paths)],
                                    sources=sources)

    def _overlay_sources(self, overlay):
        if not overlay.get("conflicts"):
            return {}, overlay
        try:
            raw_folder = str(overlay.get("folder") or "")
            folder = Path(raw_folder)
            if (folder.is_absolute() or not folder.parts or folder.parts[0] != "workspace"
                    or ".." in folder.parts or ":" in raw_folder or "\\" in raw_folder):
                raise ValueError("conflict materials must name a relative workspace folder")
            materials = self.tree / folder
            if not materials.resolve().is_relative_to((self.tree / "workspace").resolve()):
                raise ValueError("conflict materials escaped the selected home")
            if not isinstance(overlay["conflicts"], list):
                raise ValueError("conflicts must be an exact path list")
            sources = {}
            for index, row in enumerate(overlay["conflicts"]):
                name = row.get("path") if isinstance(row, dict) else None
                if not isinstance(name, str):
                    raise ValueError("conflict path missing")
                relative = Path(name)
                if (relative.is_absolute() or len(relative.parts) < 2 or relative.parts[0] not in {"tree", "app"}
                        or ".." in relative.parts or ":" in name or "\\" in name):
                    raise ValueError("conflict path outside selected source roots")
                kind = relative.parts[0]
                logical = Path(*relative.parts[1:]).as_posix()
                # A private conflict is still a required review request, but
                # never a model input. Convert it to an evidence gap here,
                # before explicit source collection can reject the request.
                self_review._relative_code_name(logical)
                for side, suffix in (("base", "base"), ("mine", "mine"), ("release", "theirs")):
                    sources[f"{side}-{index}"] = {"root": materials / kind,
                                                  "files": {logical: logical + "." + suffix}}
                root = self.code_dir if kind == "tree" else self.app_root / "app"
                sources[f"resolved-{index}"] = {"root": root, "files": {logical: logical}}
            return sources, overlay
        except (OSError, ValueError) as exc:
            return {}, {**overlay, "materials_error": str(exc)}

    def observe_update(self, receipt):
        if not isinstance(receipt, dict) or not (receipt.get("kind") == "package" or receipt.get("state") == "done"):
            return None
        operation = "update:" + str(receipt.get("id") or "") + ":" + str(receipt.get("state") or "")
        observed_path = _root(self.tree) / "updates.json"
        observed = _read(observed_path)
        if operation in observed:
            return None
        if any(operation in row.get("operations", []) for row in self.service.status()):
            return None
        overlay = receipt.get("agent_code") or {}
        if not isinstance(overlay, dict):
            overlay = {"materials_error": True}
        if overlay.get("skipped"):
            overlay = {**overlay, "skipped_code": overlay["skipped"]}
        intent = "Inspect update " + str(receipt.get("from_version") or "unknown") + " -> " + str(receipt.get("to_version") or "unknown")
        sources = {"agent": self.code_dir, "app": self.app_root / "app"}
        stock = self.app_root / "tree"
        if stock.is_dir() and stock.resolve() != self.code_dir.resolve():
            sources["release_agent"] = stock
        sides, overlay = self._overlay_sources(overlay)
        sources.update(sides)
        result = self.service.request(trigger="after_update", operation_id=operation,
                                      overlay=overlay, intent=intent, sources=sources,
                                      criteria=["Check integration of the release with retained agent edits; report unverified inputs."])
        observed[operation] = {"state": result.get("state"), "review_id": result.get("id"), "at": time.time()}
        self_review._write(observed_path, observed)
        return result

    def observe_updates(self):
        from deskd import control
        self.observe_update(_read(self.tree / "memory" / ".control" / control.UPDATE_RECEIPT))
        self.observe_update(_read(self.tree / "memory" / ".state" / "package-update.json"))

    def tick(self, paused=False):
        if time.monotonic() < self.next_tick:
            return
        self.next_tick = time.monotonic() + 5
        try:
            stamp = self.config_path.stat().st_mtime_ns
            if stamp != self.seen_config:
                self.service.settings(self._config().get("self_review", {}))
                self.seen_config = stamp
            self.observe_updates()
            for path in sorted((_root(self.tree) / "commands").glob("command-*.json")):
                asked = _read(path)
                if asked.get("state") != "queued":
                    continue
                try:
                    action = asked["action"]
                    if action == "request":
                        record = next((row for row in self.service.status()
                                       if asked["command_id"] in row.get("operations", [])), None)
                        if record is None:
                            record = self.service.request(operation_id=asked["command_id"],
                                                          depth=asked.get("depth"), intent=asked.get("intent", ""),
                                                          retry=asked.get("retry", False))
                    elif action == "cancel":
                        record = self.service.cancel(asked["id"])
                    else:
                        record = self.service.disposition(asked["id"], asked["decision"], asked["reason"], asked["subject_sha256"])
                    asked.update(state="done", review_id=record.get("id"), at=time.time())
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    asked.update(state="error", error=str(exc), at=time.time())
                self_review._write(path, asked)
            if not paused:
                self.service.tick()
            self.publish()
        except (OSError, ValueError, TypeError) as exc:
            log.exception("self-review maintenance failed; startup and Stop remain independent")
            self.publish(error=str(exc))
