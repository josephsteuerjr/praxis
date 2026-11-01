"""Owner dialogue budgets, shared by folding and the actual conversation input."""
from __future__ import annotations
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import time
import uuid
from deskd import rooms

DEFAULT_MESSAGES = 600
DEFAULT_CHARS = 128_000


def _number(value, default, low, high):
    try:
        if isinstance(value, bool):
            raise ValueError
        return min(high, max(low, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def policy(config: dict, life) -> dict:
    raw = config.get("owner_context")
    raw = raw if isinstance(raw, dict) else {}
    messages = _number(raw.get("messages"), DEFAULT_MESSAGES, 50, 2000)
    chars = _number(raw.get("characters"), DEFAULT_CHARS, 1000, 600_000)
    tokens = _number(raw.get("tokens"), 40_000, 2000, 200_000)
    telegram = config.get("telegram")
    value = telegram.get("owner_id") if isinstance(telegram, dict) else None
    owner = str(value or "").strip()
    owner = str(int(owner)) if not isinstance(value, bool) and re.fullmatch(r'[0-9]+', owner) and int(owner) > 0 else ''
    return {"keep": max(25, messages // 2), "offer_at": max(40, messages * 5 // 6),
            "hard_at": messages, "token_cap": tokens, "tape_chars": chars,
            "places": [rooms.ROOM_DEFAULT, rooms.ROOM_LEGACY] + ([owner] if owner else [])}


def is_owner_place(place, profile: dict) -> bool:
    key = str(place or "")
    return bool(profile) and (key in profile.get("places", ()) or rooms.is_room(key))


def window(life, place, rows):
    if is_owner_place(place, getattr(life, "helene_owner_context", {})):
        rows = list(rows)[-life.hot_bounds(place)[2]:]
    kept = list(life.tape_window(rows, life.tape_chars_for(place)))
    if not is_owner_place(place, getattr(life, "helene_owner_context", {})):
        return kept
    estimate = getattr(life, "estimate_tokens", lambda text: max(1, len(text) // 3))
    sizes = [token_count(r, estimate) for r in kept]
    total = sum(sizes)
    first = 0
    while len(kept) - first > 1 and total > life.hot_bounds(place)[3]:
        total -= sizes[first]; first += 1
    return kept[first:]


def token_count(row, estimate=lambda text: max(1, len(text) // 3)):
    value = row.get('tokens')
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10**9:
        return value
    return max(1, estimate(str(row.get('line') or '')))


def _commit_profile(tree, target, event):
    import update_inspection
    journal = target.with_name('context-policy.events.jsonl')
    prior_text = journal.read_text(encoding='utf8') if journal.exists() else ''
    existing = prior_text.splitlines()
    if not any(update_inspection_id(line) == event['id'] for line in existing):
        with journal.open('a', encoding='utf8') as dst:
            if prior_text and not prior_text.endswith('\n'): dst.write('\n')
            dst.write(json.dumps(event, ensure_ascii=False) + '\n'); dst.flush(); os.fsync(dst.fileno())
    update_inspection.write(target, event['after'])
    p, before = event['after'], event['before']
    # An initial profile has no previously installed budget. Do not turn the
    # internal fallback into a fictitious migration notice in the owner chat.
    # Older admitted journals without this flag retain their recovery contract.
    if event.get('notify_owner') is not False:
        update_inspection.write(Path(tree) / 'memory/.state/context-policy-notice.json', {
            'id': event['id'], 'done': False,
            'text': f"Контекст владельца: до {p['hard_at']} свежих сообщений, {p['tape_chars']} знаков и примерно {p['token_cap']} токенов разговора. Пределы можно изменить в Настройках → Контекст владельца. Прежние значения: {before['hard_at']} сообщений, {before['tape_chars']} знаков, {before['token_cap']} токенов; архив и сводки сохранены."})
    target.with_name('.context-policy-change.json').unlink(missing_ok=True)


def update_inspection_id(line):
    try: return json.loads(line).get('id')
    except (ValueError, AttributeError): return None


def valid_snapshot(value):
    return isinstance(value, dict) and all(type(value.get(k)) is int and value[k] > 0 for k in ('hard_at', 'tape_chars', 'token_cap'))


def valid_change(event):
    after = event.get('after') if isinstance(event, dict) else None
    return (isinstance(event, dict) and isinstance(event.get('id'), str) and bool(event['id'])
            and valid_snapshot(event.get('before')) and valid_snapshot(after)
            and all(type(after.get(k)) is int and after[k] > 0 for k in ('keep', 'offer_at'))
            and isinstance(after.get('places'), list) and all(isinstance(p, str) for p in after['places']))


def configure(life, config: dict, *, tree: Path | None = None) -> dict:
    """Keep non-owner/group policy intact; reuse original functions on reconfigure."""
    profile = policy(config, life)
    if not hasattr(life, "_helene_context_originals"):
        life._helene_context_originals = (life.hot_bounds, life.tape_chars_for)
    original_bounds, original_chars = life._helene_context_originals

    def bounds(place):
        if is_owner_place(place, life.helene_owner_context):
            p = life.helene_owner_context
            return p["keep"], p["offer_at"], p["hard_at"], p["token_cap"]
        return original_bounds(place)

    def chars(place):
        if is_owner_place(place, life.helene_owner_context):
            return life.helene_owner_context["tape_chars"]
        return original_chars(place)

    if tree is not None:
        import update_inspection
        target = Path(tree) / "memory/context-policy.json"
        transaction = target.with_name('.context-policy-change.json')
        pending = update_inspection.read(transaction)
        if valid_change(pending):
            _commit_profile(tree, target, pending)
        elif transaction.exists():
            # Preserve broken input for diagnosis, instead of retrying a KeyError
            # on every startup or silently overwriting the only evidence.
            os.replace(transaction, transaction.with_name(transaction.name + '.invalid-' + uuid.uuid4().hex))
        previous = update_inspection.read(target)
        if previous != profile:
            before = previous if valid_snapshot(previous) else {"hard_at": original_bounds(rooms.ROOM_DEFAULT)[2],
                                  "tape_chars": original_chars(rooms.ROOM_DEFAULT),
                                  "token_cap": original_bounds(rooms.ROOM_DEFAULT)[3]}
            change_id = uuid.uuid4().hex
            source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            initialized = not valid_snapshot(previous)
            notify = not initialized and any(previous[key] != profile[key]
                for key in ('hard_at','tape_chars','token_cap'))
            event = {"kind": "owner_context_policy_initialized" if initialized else "owner_context_policy_changed",
                     "notify_owner": notify, "author": f"deploy:{source_sha}",
                     "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "id": change_id,
                     "before": before, "after": profile,
                     "reason": "Initial owner dialogue context configuration" if initialized else "Owner dialogue context settings changed"}
            update_inspection.write(transaction, event)
            _commit_profile(tree, target, event)
        # The model reads an explicit persisted profile, not a parse-time fallback.
        profile = update_inspection.read(target)
    life.helene_owner_context = profile
    life.hot_bounds = bounds
    life.tape_chars_for = chars
    # An offer raised by the old 5500-character budget must not later force a
    # fold of a dialogue that fits the newly configured owner profile.
    if callable(getattr(life, "fold_offers", None)):
        for place in life.fold_offers():
            if not is_owner_place(place, profile): continue
            rows = life.hot_records(place, profile["hard_at"])
            tokens = sum(token_count(r) for r in rows)
            if len(rows) < profile["offer_at"] and tokens <= profile["token_cap"] and sum(len(str(r.get("line") or "")) for r in rows) <= profile["tape_chars"]:
                life.clear_fold_offer(place)
    return profile


def notice(tree: Path, desk, now):
    import update_inspection
    target = Path(tree) / "memory/.state/context-policy-notice.json"
    pending = update_inspection.read(target)
    if not pending or pending.get("done"): return
    text = str(pending.get("text") or "")
    source_id = f"context-policy-{pending['id']}"
    if not any(row.get('source_id') == source_id for row in desk.rows(2000)):
        desk.archive(text, outgoing=False, now=now, sender="Hélène", source_id=source_id)
    desk.life(text, direction="in", actor="Hélène", source_id=source_id, now=now)
    pending.update(done=True, at=time.time())
    update_inspection.write(target, pending)
