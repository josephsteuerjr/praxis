"""Fail-closed, single-peer Telegram admin ledger: the room's rules, not one message.

Sibling of `telegram_moderation`, deliberately a separate module with a separate
ledger.  Moderation acts on one message by one sender and is keyed by both; these
actions act on the room — slow mode, what everybody may send, one member restricted
for a while — and have no message to be keyed by.  Folding them into the moderation
chain would have meant loosening a verifier that re-derives every key from
`(peer, message, sender, action)`, and that verifier is the only thing standing
between a tampered ledger and a believed one.

Why these four and not the whole admin surface: they are the measures that can be
taken back.  `delete_and_ban` cannot — it is permanent, and with the moderation hand
alone Praxis cannot undo her own ban.  `unrestrict` here is that missing hand, and it
is the reason the rest is safe to hand over at all.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time

TARGET_PEER_ID = -1001240718803
ACTIONS = {"slow_mode", "default_rights", "restrict", "unrestrict",
           "ban_member", "purge_member"}

# Telegram accepts only this ladder for slow mode; anything else is coerced upstream,
# which would leave the ledger saying one thing and the room doing another.
SLOW_MODE_SECONDS = (0, 10, 30, 60, 300, 900, 3600)

# The default-rights flags this hand may flip.  Anything outside the set is refused
# rather than ignored: a right she cannot name here is a right she cannot strip by
# accident.  `change_info` and `pin_messages` stay out — they are the room's shape
# rather than its conduct, and she has no admin right for the first anyway.
RIGHTS = frozenset({
    "send_messages", "send_media", "send_photos", "send_videos", "send_docs",
    "send_audios", "send_voices", "send_stickers", "send_gifs", "send_polls",
    "send_roundvideos", "embed_links", "invite_users",
})

# A restriction is temporary by construction.  Permanence is what `delete_and_ban`
# is for: a different decision, with a different receipt, in a different ledger.
MIN_RESTRICT_SECONDS = 30
MAX_RESTRICT_SECONDS = 30 * 24 * 3600

# Повтор (replay) существует для durable-рана, доигрывающего СВОЙ вызов после
# рестарта, — это минуты. Completed-чек старше окна не «уже сделано»: комнату могли
# сменить руками мимо журнала, и месячной давности чек — не факт о мире.
REPLAY_WINDOW_SECONDS = 600

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
LEDGER = BASE / "memory" / ".state" / "telegram_admin.jsonl"
_LOCK = threading.Lock()


def _canonical(row: dict) -> bytes:
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def operation_key(peer_id: int, action: str, subject: dict) -> str:
    """Same request, same key.

    A durable run that replays an admin call must not take the measure twice.  Most
    of these are idempotent in effect — setting slow mode to 30 twice leaves 30 — but
    `restrict` carries a duration, and re-applying it would silently extend the
    restriction past what was decided.  Folding the parameters into the key makes an
    identical replay a no-op and a different request genuinely new.
    """
    raw = f"{peer_id}:{action}:" + _canonical(subject).decode("utf-8")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _exact_int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be an exact JSON integer")
    return value


def _string_list(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise TypeError(f"{what} must be a list of right names")
    return sorted(set(value))


def normalize(peer_id, action, params: dict) -> tuple[str, dict]:
    """Refuse everything not exactly understood, and return the subject to act on.

    Fail-closed on purpose: an admin call that is almost right is the dangerous case.
    `default_rights` names what 968 people may do, and a typo read as "no change"
    would be worse than a refusal she can see and correct.
    """
    peer_id = _exact_int(peer_id, "peer_id")
    if peer_id != TARGET_PEER_ID:
        raise PermissionError("admin actuator is scoped only to AbstractDL")
    if not isinstance(action, str):
        raise TypeError("action must be an exact string")
    action = action.strip()
    if action not in ACTIONS:
        raise ValueError("action must be one of: " + ", ".join(sorted(ACTIONS)))
    if not isinstance(params, dict):
        raise TypeError("params must be a JSON object")

    if action == "slow_mode":
        extras = sorted(set(params) - {"seconds"})
        if extras:
            raise ValueError("slow_mode accepts only 'seconds'; unexpected: "
                             + ", ".join(extras))
        seconds = _exact_int(params.get("seconds"), "seconds")
        if seconds not in SLOW_MODE_SECONDS:
            raise ValueError("seconds must be one of "
                             + ", ".join(str(s) for s in SLOW_MODE_SECONDS))
        return action, {"seconds": seconds}

    if action == "default_rights":
        extras = sorted(set(params) - {"allow", "deny"})
        if extras:
            raise ValueError("default_rights accepts only 'allow' and 'deny'; "
                             "unexpected: " + ", ".join(extras))
        allow = _string_list(params.get("allow"), "allow")
        deny = _string_list(params.get("deny"), "deny")
        unknown = sorted((set(allow) | set(deny)) - RIGHTS)
        if unknown:
            raise ValueError("unknown rights: " + ", ".join(unknown)
                             + "; known: " + ", ".join(sorted(RIGHTS)))
        both = sorted(set(allow) & set(deny))
        if both:
            raise ValueError("allowed and denied at once: " + ", ".join(both))
        if not allow and not deny:
            raise ValueError("name at least one right to allow or deny")
        return action, {"allow": allow, "deny": deny}

    if action == "restrict":
        extras = sorted(set(params) - {"user_id", "seconds"})
        if extras:
            raise ValueError("restrict accepts only 'user_id' and 'seconds'; "
                             "unexpected: " + ", ".join(extras))
        user_id = _exact_int(params.get("user_id"), "user_id")
        seconds = _exact_int(params.get("seconds"), "seconds")
        if user_id <= 0:
            raise ValueError("a positive user_id is required")
        if not MIN_RESTRICT_SECONDS <= seconds <= MAX_RESTRICT_SECONDS:
            raise ValueError(
                f"seconds must be between {MIN_RESTRICT_SECONDS} and "
                f"{MAX_RESTRICT_SECONDS}: a restriction is temporary by construction, "
                "and permanence is delete_and_ban's decision to make")
        return action, {"user_id": user_id, "seconds": seconds}

    if action == "ban_member":
        extras = sorted(set(params) - {"user_id"})
        if extras:
            raise ValueError("ban_member accepts only 'user_id'; unexpected: "
                             + ", ".join(extras))
        user_id = _exact_int(params.get("user_id"), "user_id")
        if user_id <= 0:
            raise ValueError("a positive user_id is required")
        # Перманентный бан по user_id — для случая, когда сообщение уже удалено
        # (чужой рукой или модерацией) и moderate_abstractdl не за что зацепить.
        # Снимается unrestrict'ом — та же сторона медали, тот же журнал.
        return action, {"user_id": user_id}

    # purge_member: DeleteParticipantHistory — вычистить ВСЮ историю участника.
    # Требует живого бана (Telegram отклоняет запрос к не-забаненному), поэтому
    # wire-слой сначала банит, потом чистит. Идемпотент по (user_id): повтор
    # безопасен, истории больше нет.
    if action == "purge_member":
        extras = sorted(set(params) - {"user_id"})
        if extras:
            raise ValueError("purge_member accepts only 'user_id'; unexpected: "
                             + ", ".join(extras))
        user_id = _exact_int(params.get("user_id"), "user_id")
        if user_id <= 0:
            raise ValueError("a positive user_id is required")
        return action, {"user_id": user_id}

    extras = sorted(set(params) - {"user_id"})
    if extras:
        raise ValueError("unrestrict accepts only 'user_id'; unexpected: "
                         + ", ".join(extras))
    user_id = _exact_int(params.get("user_id"), "user_id")
    if user_id <= 0:
        raise ValueError("a positive user_id is required")
    # Deliberately unbounded and always available: undoing must never be harder than
    # doing.  This is also the only hand that can lift a `delete_and_ban`.
    return action, {"user_id": user_id}


def validate(peer_id, action, params: dict) -> tuple[str, str, dict]:
    action, subject = normalize(peer_id, action, params)
    return operation_key(int(peer_id), action, subject), action, subject


def _read_verified() -> list[dict]:
    rows: list[dict] = []
    previous = ""
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RuntimeError(f"admin ledger unreadable: {exc}") from exc
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise RuntimeError(f"admin ledger corrupt at line {index}") from exc
        if not isinstance(row, dict) or row.get("schema") != "praxis.telegram.admin.v1":
            raise RuntimeError(f"admin ledger invalid schema at line {index}")
        digest = str(row.get("receipt_sha256") or "")
        unsigned = dict(row)
        unsigned.pop("receipt_sha256", None)
        expected = hashlib.sha256(_canonical(unsigned)).hexdigest()
        if digest != expected or str(row.get("previous_sha256") or "") != previous:
            raise RuntimeError(f"admin ledger hash-chain mismatch at line {index}")
        required = {"idempotency_key", "actor", "peer_id", "action", "subject",
                    "status", "before", "after", "error"}
        if not required.issubset(row):
            raise RuntimeError(f"admin ledger missing fields at line {index}")
        if operation_key(int(row["peer_id"]), str(row["action"]),
                         dict(row["subject"])) != row["idempotency_key"]:
            raise RuntimeError(f"admin ledger key mismatch at line {index}")
        rows.append(row)
        previous = digest
    return rows


def latest(key: str) -> dict | None:
    with _LOCK:
        for row in reversed(_read_verified()):
            if row["idempotency_key"] == key:
                return row
    return None


def _scope(action: str, subject: dict) -> tuple:
    """Предмет меры: участник — для restrict/unrestrict/ban_member/purge_member,
    самонастройка — для прочих."""
    if action in {"restrict", "unrestrict", "ban_member", "purge_member"}:
        try:
            return ("member", int(subject.get("user_id") or 0))
        except (TypeError, ValueError):
            return ("member", 0)
    return (action,)


def prior(key: str) -> dict | None:
    """Свежий и всё ещё последний ПО СВОЕМУ ПРЕДМЕТУ completed-чек — только он повтор.

    Прежний prior отвечал «этот ключ когда-либо совершался» — и адверсарка 28.08
    провела цену по циферблату: unrestrict исполнялся ОДИН раз на человека за всю
    жизнь журнала (второй раз — replayed=true, ноль RPC, человек остаётся забанен),
    а slow_mode 30→0→30 оставлял комнату на нуле с чеком «30 completed». Одноразовой
    оказалась ровно та рука, ради обратимости которой модуль построен.

    Повтор — это durable-ран, доигрывающий свой же вызов после рестарта. Два сторожа:

    - ПРЕДМЕТ: replay допустим, только пока никакой БОЛЕЕ ПОЗДНИЙ completed-чек не
      менял тот же предмет. Новый restrict после unrestrict делает старый ключ
      unrestrict историей, а не вечным правом; slow_mode 0 после 30 — то же самое.
    - ВРЕМЯ: completed старше REPLAY_WINDOW_SECONDS не повторяется — журнал не видит
      ручных действий админов мимо этих рук, и старый чек не факт о комнате.

    Ключ и верификатор журнала не тронуты: `operation_key` по-прежнему выводится из
    (peer, action, subject), меняется только чтение «уже сделано»."""
    with _LOCK:
        rows = _read_verified()
    target_index = None
    for index, row in enumerate(rows):
        if row["idempotency_key"] == key:
            target_index = index
    if target_index is None:
        return None
    target = rows[target_index]
    if target["status"] != "completed":
        return None
    try:
        if time.time() - float(target.get("ts") or 0) > REPLAY_WINDOW_SECONDS:
            return None
    except (TypeError, ValueError):
        return None
    scope = _scope(str(target["action"]), dict(target["subject"]))
    for row in rows[target_index + 1:]:
        if (row["status"] == "completed"
                and row["idempotency_key"] != key
                and _scope(str(row["action"]), dict(row["subject"])) == scope):
            return None
    return target


def history(limit: int = 20) -> list[dict]:
    """What she has already done to this room, oldest first within the window.

    A measure taken on 968 people should be readable without walking a hash chain by
    hand — by her, before she decides again, and by anyone auditing afterwards.
    """
    with _LOCK:
        rows = _read_verified()
    return rows[-limit:] if limit > 0 else rows


def append_receipt(payload: dict) -> dict:
    with _LOCK:
        rows = _read_verified()
        previous = str(rows[-1]["receipt_sha256"]) if rows else ""
        row = {"schema": "praxis.telegram.admin.v1", "ts": time.time(),
               "previous_sha256": previous, **payload}
        row["receipt_sha256"] = hashlib.sha256(_canonical(row)).hexdigest()
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LEDGER), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False,
                                     separators=(",", ":")) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return row
