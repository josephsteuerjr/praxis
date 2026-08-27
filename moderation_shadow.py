"""Deterministic shadow-only spam screening for AbstractDL."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata

TARGET_PEER_ID = -1001240718803
BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
STATE_PATH = BASE / "memory" / ".state" / "moderation_shadow.json"
_STATE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Detection:
    verdict: str
    matched_features: tuple[str, ...]


_HOMOGLYPHS = str.maketrans({
    "a": "а", "A": "а", "o": "о", "O": "о", "e": "е", "E": "е",
    "p": "р", "P": "р", "c": "с", "C": "с", "x": "х", "X": "х",
    "y": "у", "Y": "у", "k": "к", "K": "к", "m": "м", "M": "м",
    "t": "т", "T": "т", "b": "в", "B": "в", "h": "н", "H": "н",
})


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").translate(_HOMOGLYPHS).lower()
    return re.sub(r"\s+", " ", value).strip()


def _features(text: str, *, first_message: bool, repeated_within_hour: bool) -> tuple[str, ...]:
    value = normalize_text(text)
    found: list[str] = []
    if first_message:
        found.append("first_message")
    if re.search(r"(?:ден(?:ьг|ег)|доход|заработ|оплат|зарплат|₽|руб(?:л|\b)|\bu(?:s|с)(?:d|д)(?:t|т)\b|\bтыс\.?\b|\d[\d\s]*(?:р\.?|₽)|(?:по\s+(?:факту|завершени\w*))\s+\d[\d\s]*)", value):
        found.append("money")
    if re.search(r"(?:куплю|продам|предлагаю|ищем|ищу\s+кто|нуж(?:ен|на|ны)|подсобить|разбирать|раскладывать|очистить|собрать\s+мусор|по\s+завершени|по\s+факту)", value):
        found.append("commercial_offer")
    if re.search(r"(?:пиши(?:те)?|напиши(?:те)?|став(?:ь|ьте)|жми(?:те)?|перейди(?:те)?|найди(?:те)?|ищи(?:те)?|в личк|в лс|остав(?:ь|ьте).{0,12}(?:\+|плюс)|отклик)", value):
        found.append("call_to_action")
    if re.search(r"(?:бот\b|bot\b|бота\b|боте\b)", value) and re.search(r"(?:найди|поиск|перейд|запуст|пиши)", value):
        found.append("promoted_bot")
    if repeated_within_hour:
        found.append("repeat")
    return tuple(found)


def detect_message(*, peer_id: int, text: str, first_message: bool,
                   repeated_within_hour: bool = False) -> Detection:
    if int(peer_id) != TARGET_PEER_ID:
        return Detection("pass", ())
    matched = _features(text, first_message=bool(first_message),
                        repeated_within_hour=bool(repeated_within_hour))
    flagged = "repeat" in matched or len(set(matched)) >= 2
    return Detection("review" if flagged else "pass", matched)


def _history_seed() -> dict:
    """Build sender state once from the private canonical archive, not from deployment time."""
    try:
        import group_context
        senders: dict[str, dict] = {}
        messages: dict[str, float] = {}
        for row in group_context.iter_records(str(TARGET_PEER_ID)):
            if row.get("kind") != "message":
                continue
            sender = row.get("sender_id")
            message = row.get("message_id")
            if not sender or not message:
                continue
            raw_ts = str(row.get("timestamp") or "")
            try:
                ts = __import__("datetime").datetime.fromisoformat(
                    raw_ts.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                ts = 0.0
            key = str(int(sender))
            previous = senders.get(key) or {}
            if ts >= float(previous.get("ts") or 0):
                digest = hashlib.sha256(normalize_text(str(row.get("text") or "")).encode("utf-8")).hexdigest()
                senders[key] = {"digest": digest, "ts": ts}
            messages[str(int(message))] = ts
        return {"senders": senders, "messages": messages}
    except Exception:
        return {"senders": {}, "messages": {}}


def _load_state() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return _history_seed()


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(STATE_PATH)


def observe_message(*, peer_id: int, message_id: int, sender_id: int,
                    text: str, observed_at: float | None = None) -> Detection | None:
    """Persist ids/timestamps/text digest locally; emit only privacy-minimal review facts."""
    if not text:
        return None
    if int(peer_id) != TARGET_PEER_ID or not message_id or not sender_id:
        return None
    now = float(observed_at or time.time())
    digest = hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
    message_key, sender_key = str(int(message_id)), str(int(sender_id))
    with _STATE_LOCK:
        state = _load_state()
        messages = state.setdefault("messages", {})
        if message_key in messages:
            return None
        senders = state.setdefault("senders", {})
        previous = senders.get(sender_key) or {}
        first = not bool(previous)
        repeated = previous.get("digest") == digest and now - float(previous.get("ts") or 0) <= 3600
        result = detect_message(peer_id=peer_id, text=text, first_message=first,
                                repeated_within_hour=repeated)
        if result.verdict != "pass":
            from core import events as core_events
            emitted = core_events.emit("moderation_review", "telegram.new_message", {
                "peer_id": int(peer_id), "message_id": int(message_id),
                "sender_id": int(sender_id), "verdict": result.verdict,
                "matched_features": list(result.matched_features),
            }, dedup_key=f"moderation:{int(peer_id)}:{int(message_id)}", ts=now)
            if emitted is None:
                return result  # WAL failed: do not consume message_id; replay must retry.
        senders[sender_key] = {"digest": digest, "ts": now}
        messages[message_key] = now
        if len(messages) > 4000:
            state["messages"] = dict(sorted(messages.items(), key=lambda item: item[1])[-2000:])
        _save_state(state)
    return result
