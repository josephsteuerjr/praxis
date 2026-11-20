"""Persist the second account's wake rules and hand off matched arrivals."""

from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


SIGNALS = frozenset({"notifications", "mention", "name", "reply", "reply_to_praxis", "reply_to_owner"})


def _write(path: Path, value: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    return json.loads(path.read_bytes())


def validate_rules(rules: list) -> list:
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("rules must be a list of up to 100 entries")
    labels = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"name", "all", "any", "chat", "topic"}:
            raise ValueError("each rule has name, all/any and optional chat/topic")
        label = rule.get("name")
        if not isinstance(label, str) or not label.strip() or len(label) > 120 or label in labels:
            raise ValueError("rules need unique names")
        labels.add(label)
        for key in ("all", "any"):
            values = rule.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or v not in SIGNALS for v in values):
                raise ValueError(f"unknown wake condition in {key}")
        if not rule.get("all") and not rule.get("any"):
            raise ValueError("each rule needs at least one wake condition")
        if "chat" in rule and not re.fullmatch(r"-?[1-9][0-9]*", str(rule["chat"])):
            raise ValueError("chat must be a Telegram peer id")
        if "topic" in rule and (type(rule["topic"]) is not int or rule["topic"] < 1):
            raise ValueError("topic must be a positive integer")
    return rules


def matching_rules(rules: list, event: dict) -> list[str]:
    signals = set(event["signals"])
    return [rule["name"] for rule in rules
            if ("chat" not in rule or str(rule["chat"]) == str(event["chat"]))
            and ("topic" not in rule or rule["topic"] == event.get("topic"))
            and signals.issuperset(rule.get("all", ()))
            and (not rule.get("any") or not signals.isdisjoint(rule["any"]))]


class WakeStore:
    def __init__(self, root: Path, account: int):
        self.root = root
        self.account = account
        self.path = root / "wake-policy.json"
        self.queue = root / "wake-queue"
        self.done = root / "wake-done"
        self.sent = root / "sent"
        for directory in (self.queue, self.done, self.sent):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            _write(self.path, {"revision": 0, "account": account, "rules": [], "names": ["Егор"]})
        self.policy = _read(self.path)
        if self.policy["account"] != account:
            raise ValueError("wake policy belongs to another account")
        self.recent = deque(maxlen=200)

    def change(self, *, rules: list, names: list | None = None, revision: int | None = None) -> dict:
        if revision is not None and self.policy["revision"] != revision:
            return {"status": "conflict", "policy": self.policy}
        if names is None:
            names = self.policy["names"]
        if (not isinstance(names, list) or len(names) > 50
                or any(not isinstance(n, str) or not n.strip() or len(n) > 80 for n in names)):
            raise ValueError("names must be a list of short non-empty strings")
        policy = {"revision": self.policy["revision"] + 1, "account": self.account,
                  "rules": validate_rules(rules), "names": names}
        _write(self.path, policy)
        self.policy = policy
        return {"status": "ok", "policy": policy}

    def preview(self, rules: list | None) -> dict:
        rules = self.policy["rules"] if rules is None else validate_rules(rules)
        matches = [{**event, "matched": hits} for event in self.recent
                   if (hits := matching_rules(rules, event))]
        return {"status": "ok", "observed": len(self.recent), "matches": matches}

    def matches_name(self, text: str) -> bool:
        return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
                   for name in self.policy["names"])

    def observe(self, event: dict) -> None:
        self.recent.append(event)
        hits = matching_rules(self.policy["rules"], event)
        if not hits:
            return
        identity = hashlib.sha256(f'{self.account}:{event["chat"]}:{event["message"]}'.encode()).hexdigest()
        target = self.queue / f"{identity}.json"
        if target.exists() or (self.done / target.name).exists():
            return
        _write(target, {**event, "id": identity, "account": self.account, "matched": hits,
                        "policy_revision": self.policy["revision"], "received_at": time.time()})

    def next_event(self) -> dict | None:
        paths = sorted(self.queue.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        return _read(paths[0]) if paths else None

    def acknowledge(self, identity: str, run: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", identity) or not run:
            raise ValueError("event id and run are required")
        filename = f"{identity}.json"
        path = self.queue / filename
        done = self.done / filename
        if done.exists():
            path.unlink(missing_ok=True)
            return
        event = _read(path)
        _write(done, {"id": identity, "run": run, "account": event["account"]})
        path.unlink()

    def _sent_path(self, chat: int, message: int) -> Path:
        return self.sent / f"{int(chat)}_{int(message)}.json"

    def note_sent(self, chat: int, message: int) -> None:
        path = self._sent_path(chat, message)
        if not path.exists():
            _write(path, {"account": self.account, "chat": chat, "message": message})

    def was_sent(self, chat: int, message: int) -> bool:
        return self._sent_path(chat, message).is_file()
