"""Durable Telegram follow-up ledger: СЛЕД нити и ЗАКАЗАННЫЙ отчёт — разные вещи.

Каждое прямое сообщение кому-то, кроме Егора, оставляет здесь СЛЕД нити. След —
это её собственная память: через час, внутри пульса, Telegram отключён, и эта
запись — единственное свидетельство, что по этому поводу она уже говорила.
След Егору НЕ уходит никогда и гаснет по возрасту (`TRACE_TTL_SEC`).

ОТЧЁТ Егору («ответили — вот что») — отдельное свойство нити, `notify_owner`.
Его включает только заказчик: Егор словами («сообщи, когда ответит») или она
сама (`set_notice`, тул `watch_reply`). Заказанное обязательство по возрасту не
гаснет — потерять её намерение молча хуже, чем выполнить его поздно.

⚠ До 27.07.2026 этот докстринг утверждал обратное: «the owner need not remember
to say «сообщи, когда ответит»: that follow-up is the useful default». Дефолт
назначили в коде, Егора не спросили, в манифесте рельсов не назвали. Замер на
проде: 17 писем ему за две недели, из 11 owner-записей `wants_followup` не
проходит НИ ОДНА — он не заказал ни одного отчёта; шесть нитей она погасила
руками. Финальная капля — 27.07 02:32: Егор ответил ей реплаем в AbstractDL, и
его же реплика уехала ему в ЛС под заголовком «AbstractDL Chat ответил(а)».
Поэтому ответ самого Егора закрывает нить (она должна знать, что ответ пришёл),
но письмом ему не становится никогда.

JSON остаётся источником правды через рестарты.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path


BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
STATE_PATH = BASE / "memory" / ".state" / "telegram_followups.json"
_LOCK = threading.RLock()
_VERSION = 1
_SETTLED_KEEP = 500

CONTEXT_LIMIT = 12          # сколько нитей влезает в ленту пульса


def _trace_ttl() -> float:
    """Срок жизни СЛЕДА нити, оставшейся без ответа.

    try/except обязателен: `telegram_followups` импортирует `mtproto_runner`, и
    сломанное значение в `.deploy.env` иначе роняет импорт вместе со всем
    раннером — ровно грабли `forge.py:128`. Битая переменная обязана пережиться
    молчаливым возвратом дефолта, а не падением контейнера.
    """
    try:
        value = float((os.environ.get("PRAXIS_FOLLOWUP_TRACE_TTL_SEC") or "").strip())
    except Exception:
        return 259200.0
    return value if value > 0 else 259200.0


# 72ч. Читается ОДИН раз на импорте — смена переменной требует рестарта раннера;
# это названо в рельсе `followup_notice`, чтобы предел не был молчаливым.
TRACE_TTL_SEC = _trace_ttl()

_EXPIRY_PATTERNS = (
    re.compile(
        r"(?is)(?:действ\w*|актив\w*|истеч\w*|протух\w*)"
        r".{0,40}?(\d+(?:[.,]\d+)?)\s*"
        r"(минут\w*|час\w*|дн(?:я|ей|и)?)"
    ),
    re.compile(
        r"(?is)(?:valid|active|expires?)\s+(?:for|in)\s+"
        r"(\d+(?:[.,]\d+)?)\s*(minutes?|hours?|days?)"
    ),
)


_FOLLOWUP_PATTERNS = (
    re.compile(
        r"(?is)(?:сообщи|скажи|дай\s+знать|отпишись|уведом\w*)"
        r".{0,100}(?:ответ\w*|отпиш\w*|реакци\w*)"
    ),
    re.compile(
        r"(?is)(?:когда|если).{0,60}(?:ответ\w*|отпиш\w*)"
        r".{0,80}(?:сообщи|скажи|дай\s+знать|отпишись|уведом\w*)"
    ),
    re.compile(r"(?is)(?:let\s+me\s+know|tell\s+me).{0,80}(?:repl\w*|respond\w*)"),
    re.compile(r"(?is)(?:when|if).{0,60}(?:they|he|she).{0,30}(?:repl\w*|respond\w*)"),
)

_NO_FOLLOWUP_PATTERNS = (
    re.compile(
        r"(?is)(?:не\s+(?:надо|нужно)?\s*(?:сообщать|отписываться|следить)|"
        r"без\s+(?:отч[её]та|follow[- ]?up))"
    ),
    re.compile(r"(?is)(?:don'?t|do\s+not)\s+(?:tell|notify|track|follow\s*up)"),
)


def wants_followup(text: str) -> bool:
    """Whether the owner's wording explicitly asks to hear about the answer."""
    value = str(text or "").strip()
    return bool(value and any(p.search(value) for p in _FOLLOWUP_PATTERNS))


def suppresses_followup(text: str) -> bool:
    """Whether the owner explicitly opted out of automatic answer tracking."""
    value = str(text or "").strip()
    return bool(value and any(pattern.search(value) for pattern in _NO_FOLLOWUP_PATTERNS))


def explicit_expiry_seconds(text: str) -> float | None:
    """Return a duration only when the message explicitly states its validity."""
    value = str(text or "")
    for pattern in _EXPIRY_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        amount = float(match.group(1).replace(",", "."))
        unit = match.group(2).lower()
        if unit.startswith(("минут", "minute")):
            return amount * 60
        if unit.startswith(("час", "hour")):
            return amount * 3600
        return amount * 86400
    return None


def request_from_owner_buffer(lines, *, limit: int = 12,
                              explicit_only: bool = True) -> str:
    """Return the latest explicit owner request from a live owner-DM buffer.

    The active tool history intentionally excludes the message currently being
    processed.  The MTProto buffer already contains it, so it is the reliable
    source at send time.  Praxis-authored lines are never treated as authority.
    """
    for raw in reversed(list(lines or ())[-max(1, int(limit)):]):
        line = str(raw or "").strip()
        if not line or line.casefold().startswith("praxis:"):
            continue
        body = line.split(":", 1)[1].strip() if ":" in line else line
        if suppresses_followup(body):
            return ""
        return body if (not explicit_only or wants_followup(body)) else ""
    return ""


def _empty() -> dict:
    return {"version": _VERSION, "items": [], "pending_revisions": []}


def _load(path: Path = STATE_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return _empty()
    pending = data.get("pending_revisions")
    return {
        "version": _VERSION,
        "items": [x for x in data["items"] if isinstance(x, dict)],
        "pending_revisions": ([x for x in pending if isinstance(x, dict)]
                              if isinstance(pending, list) else []),
    }


def _save(data: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _owes_owner_notice(item: dict) -> bool:
    """Нить, по которой Егору ЕЩЁ должны письмо.

    Один предикат на три места (`_compact`, `pending_notifications`, `context`):
    разъехавшись, они дали бы «отвечено, отчёта не будет» — запись, защищённую от
    вытеснения навсегда, и леджер без границы.
    """
    return bool(
        item.get("status") == "answered"
        and item.get("notify_owner")
        and not item.get("notified_at")
        and not item.get("notice_skipped")
    )


def _response_revision_rank(source_id: str) -> tuple[int, float]:
    """Rank a response revision exactly like Telegram's live projection.

    The runner identity is ``<mid>:edit:<UTC second>:<payload hash>``.  The hash
    distinguishes exact replay from a second real edit in the same Telegram second;
    ordering remains timestamp then arrival, and deletion is terminal.
    """
    value = str(source_id or "")
    if ":delete" in value:
        return 2, float("inf")
    if ":edit:" in value:
        revision = value.split(":edit:", 1)[1].rsplit(":", 1)[0]
        try:
            stamp = dt.datetime.fromisoformat(revision.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            return 1, stamp.timestamp()
        except (TypeError, ValueError):
            return 1, 0.0
    return 0, 0.0


def _response_revision_is_stale(
    existing_source_id: str, incoming_source_id: str, *,
    existing_order: int = 0, incoming_order: int = 0,
) -> bool:
    existing = _response_revision_rank(existing_source_id)
    incoming = _response_revision_rank(incoming_source_id)
    if existing[0] == 2 and incoming[0] < 2:
        return True
    if existing[0] == 1 and incoming[0] == 0:
        return True
    return (existing[0] == incoming[0] == 1
            and (incoming[1], int(incoming_order))
            < (existing[1], int(existing_order)))


def _compact(items: list[dict], *, settled_keep: int = _SETTLED_KEEP) -> list[dict]:
    """Bound settled history without ever dropping an unresolved obligation."""
    protected = {
        index for index, item in enumerate(items)
        if (item.get("status") == "pending" or _owes_owner_notice(item)
            or isinstance(item.get("pending_response_revision"), dict))
    }
    settled = [index for index in range(len(items)) if index not in protected]
    keep_settled = max(0, int(settled_keep) - len(protected))
    selected = protected | set(settled[-keep_settled:] if keep_settled else ())
    return [item for index, item in enumerate(items) if index in selected]


def _expire_pending(items: list[dict], *, now: float) -> bool:
    changed = False
    for item in items:
        expires_at = item.get("expires_at")
        if item.get("status") != "pending" or not isinstance(expires_at, (int, float)):
            continue
        if float(expires_at) <= now:
            item["status"] = "expired"
            item["expired_at"] = now
            changed = True
    return changed


class FollowUpLedger:
    """Small synchronized facade, with an injectable path for tests."""

    def __init__(self, path: str | Path = STATE_PATH):
        self.path = Path(path)

    def create(
        self,
        *,
        target_ref: str,
        target_label: str,
        target_peer_id: str | int,
        target_user_id: str | int | None,
        sent_message_id: str | int,
        request_text: str,
        sent_at: float | None = None,
        idempotency_key: str = "",
        notify_owner: bool = False,
        notice_source: str = "",
        sent_excerpt: str = "",
        purpose: str = "",
    ) -> dict:
        now = float(sent_at if sent_at is not None else time.time())
        receipt = str(idempotency_key or "").strip()
        peer = str(target_peer_id)
        sent_mid = int(sent_message_id)
        expiry_seconds = explicit_expiry_seconds(request_text)
        item = {
            "id": "tgfu_" + uuid.uuid4().hex[:16],
            "status": "pending",
            "created_at": now,
            "target_ref": str(target_ref),
            "target_label": str(target_label),
            "target_peer_id": peer,
            "target_user_id": (str(target_user_id) if target_user_id is not None else None),
            "sent_message_id": sent_mid,
            "sent_at": now,
            "request_text": str(request_text or "")[:1000],
            # ЧТО она отправила, а не только ПОЧЕМУ. 27.07 02:29 она отправила
            # поправку второй раз реплаем на собственное #94144: id своей реплики
            # она взяла отсюда, а текста её не было нигде — в леджере не было поля.
            "sent_excerpt": str(sent_excerpt or "")[:500],
            # ЧЕМ она это сказала: "tool:send_message", "tool:narrate", … Нужно тем, кто
            # читает след как ответ на вопрос «кому я сегодня писала»: строка процесса,
            # брошенная наррацией в тот же тред, — тоже её слова, но это не письмо
            # человеку, и вытеснять им настоящее сообщение из обзора нельзя.
            # Пустая строка = запись старше 04.08 либо писатель, который не сказал.
            "purpose": str(purpose or "")[:64],
            # Отчёт Егору — не свойство механизма, а решение заказчика.
            "notify_owner": bool(notify_owner),
            "notice_source": str(notice_source or "")[:40],
            "notice_skipped": "",
            # У СЛЕДА срок жизни есть и он назван; у ЗАКАЗАННОГО обязательства его
            # нет — заказ по возрасту не гаснет (потерять её намерение молча хуже,
            # чем выполнить поздно). Явно названный в тексте срок бьёт оба случая.
            "expires_at": (now + expiry_seconds if expiry_seconds is not None
                           else (None if notify_owner else now + TRACE_TTL_SEC)),
            "response": None,
            "notified_at": None,
            "idempotency_key": receipt,
        }
        with _LOCK:
            state = _load(self.path)
            for existing in state["items"]:
                try:
                    same_message = (
                        str(existing.get("target_peer_id")) == peer
                        and int(existing.get("sent_message_id")) == sent_mid
                    )
                except (TypeError, ValueError):
                    same_message = False
                if ((receipt and str(existing.get("idempotency_key") or "") == receipt)
                        or same_message):
                    return dict(existing)
            state["items"].append(item)
            state["items"] = _compact(state["items"])
            _save(state, self.path)
        return dict(item)

    def snapshot(self, *, status: str | None = None) -> list[dict]:
        """Read the persisted ledger without expiring or rewriting any entries."""
        with _LOCK:
            items = _load(self.path)["items"]
        if status is not None:
            items = [x for x in items if x.get("status") == status]
        return [dict(x) for x in items]

    def list(self, *, status: str | None = None) -> list[dict]:
        with _LOCK:
            state = _load(self.path)
            if _expire_pending(state["items"], now=time.time()):
                state["items"] = _compact(state["items"])
                _save(state, self.path)
            items = state["items"]
        if status is not None:
            items = [x for x in items if x.get("status") == status]
        return [dict(x) for x in items]

    def observe_incoming(
        self,
        *,
        peer_id: str | int,
        sender_id: str | int | None,
        message_id: str | int,
        text: str,
        reply_to_message_id: str | int | None = None,
        received_at: float | None = None,
        sender_name: str = "",
        sender_is_owner: bool = False,
    ) -> dict | None:
        """Resolve one pending follow-up from a concrete incoming message.

        In a DM, a later incoming message from the addressed user is an answer.
        In a group/channel, only a Telegram reply to the exact sent message is
        accepted; unrelated room traffic must not produce a false notification.

        `sender_name` и `sender_is_owner` приходят снаружи: модуль не знает
        OWNER_ID и не должен его знать, а раннер имя ответившего и так печатает
        в лог («получен ответ #94244 от Yegor Kosyrev») — до 27.07 оно просто не
        доезжало ни до письма, ни до записи.
        """
        now = float(received_at if received_at is not None else time.time())
        peer = str(peer_id)
        sender = str(sender_id) if sender_id is not None else None
        mid = int(message_id)
        reply_mid = int(reply_to_message_id) if reply_to_message_id is not None else None
        with _LOCK:
            state = _load(self.path)
            expired = _expire_pending(state["items"], now=now)
            # Telegram may replay an update after reconnect.  One concrete incoming
            # message must never settle a second obligation on the next replay.
            for existing in state["items"]:
                response = existing.get("response") or {}
                try:
                    already_observed = (
                        str(response.get("peer_id")) == peer
                        and int(response.get("message_id")) == mid
                    )
                except (TypeError, ValueError):
                    already_observed = False
                if already_observed:
                    return None
            candidates = []
            for index, item in enumerate(state["items"]):
                if str(item.get("target_peer_id")) != peer:
                    continue
                pending_revision = item.get("pending_response_revision") or {}
                pending_match = self._pending_revision_matches(item, peer, mid)
                if (item.get("status") != "pending"
                        and not (item.get("status") == "response_deleted" and pending_match)):
                    continue
                sent_mid = int(item.get("sent_message_id") or 0)
                target_user = item.get("target_user_id")
                is_dm = bool(target_user)
                if is_dm:
                    if sender != str(target_user) or mid <= sent_mid:
                        continue
                    exact_reply = int(reply_mid == sent_mid)
                elif reply_mid != sent_mid:
                    continue
                else:
                    exact_reply = 1
                candidates.append((exact_reply, float(item.get("sent_at") or 0), index))
            if not candidates:
                if expired:
                    state["items"] = _compact(state["items"])
                    _save(state, self.path)
                return None
            _, _, index = max(candidates)
            item = state["items"][index]
            pending_revision = item.pop("pending_response_revision", None)
            if not isinstance(pending_revision, dict):
                pending_revision = next((
                    row for row in state.get("pending_revisions", [])
                    if str(row.get("peer_id")) == peer
                    and int(row.get("message_id") or 0) == mid
                ), None)
            state["pending_revisions"] = [
                row for row in state.get("pending_revisions", [])
                if not (str(row.get("peer_id")) == peer
                        and int(row.get("message_id") or 0) == mid)
            ]
            response = {
                "peer_id": peer,
                "sender_id": sender,
                # Отвечает ЧЕЛОВЕК, а не чат: заголовок письма брал `target_label`
                # и выходило «AbstractDL Chat ответил(а)». Пусто здесь — честное
                # «не знаю кто», выдумывать метку чата вместо имени нельзя.
                "sender_name": str(sender_name or "")[:120],
                "message_id": mid,
                "reply_to_message_id": reply_mid,
                "text": str(text or "")[:4000],
                "received_at": now,
                "revision_source_id": str(mid),
                # Revision evidence is append-only while the thread is retained; the
                # bounded follow-up ledger itself is not an archival store.
                "revisions": [{
                    "kind": "message", "source_id": str(mid),
                    "text": str(text or "")[:4000], "observed_at": now,
                }],
            }
            if isinstance(pending_revision, dict):
                source_id = str(pending_revision.get("source_id") or "")
                kind = str(pending_revision.get("kind") or "")
                if kind == "deletion" or ":delete" in source_id:
                    response["revisions"].append({
                        "kind": "deletion", "source_id": source_id or f"{mid}:delete",
                        "observed_at": pending_revision.get("observed_at"),
                    })
                    response["text"] = ""
                    response["deleted"] = True
                    response["deleted_at"] = pending_revision.get("observed_at")
                    response["revision_source_id"] = source_id or f"{mid}:delete"
                    item["status"] = "response_deleted"
                    item["notice_skipped"] = "ответ удалён до отправки отчёта"
                    item["notice_skipped_at"] = pending_revision.get("observed_at") or now
                elif kind == "edit" and ":edit:" in source_id:
                    value = str(pending_revision.get("text") or "")[:4000]
                    response["revisions"].append({
                        "kind": "edit", "source_id": source_id, "text": value,
                        "observed_at": pending_revision.get("observed_at"),
                    })
                    response["text"] = value
                    response["edited_at"] = pending_revision.get("observed_at")
                    response["revision_source_id"] = source_id
                    response["revision_order"] = int(
                        pending_revision.get("revision_order") or 0)
                    item["status"] = "answered"
                else:
                    item["status"] = "answered"
            else:
                item["status"] = "answered"
            item["response"] = response
            if sender_is_owner:
                # 27.07 02:32: Егор ответил ей реплаем в AbstractDL — и его же слова
                # уехали ему в ЛС под заголовком «AbstractDL Chat ответил(а)». Нить
                # ответом ЗАКРЫТА (она должна знать, что ответ пришёл), но пересылать
                # человеку его собственную реплику нечего и никогда не будет.
                # Пишем здесь, а не в тике доставки: факт истинен ровно сейчас, без
                # окна между матчем и отправкой и без знания OWNER_ID внутри модуля.
                item["notice_skipped"] = "ответил сам Егор"
                item["notice_skipped_at"] = now
            _save(state, self.path)
            return dict(item)

    def get(self, followup_id: str) -> dict | None:
        """Return one durable thread without mutating expiry/current state."""
        with _LOCK:
            for item in _load(self.path)["items"]:
                if str(item.get("id") or "") == str(followup_id or ""):
                    return dict(item)
        return None

    @staticmethod
    def _response_matches(item: dict, peer: str, mid: int) -> bool:
        response = item.get("response") or {}
        try:
            return (str(response.get("peer_id")) == peer
                    and int(response.get("message_id")) == mid)
        except (TypeError, ValueError):
            return False

    def _pending_revision_matches(self, item: dict, peer: str, mid: int) -> bool:
        pending = item.get("pending_response_revision") or {}
        try:
            return (str(pending.get("peer_id")) == peer
                    and int(pending.get("message_id")) == mid)
        except (TypeError, ValueError):
            return False

    def _store_pending_revision(
        self, state: dict, *, peer: str, mid: int, kind: str, source_id: str,
        observed_at: float, text: str = "", revision_order: int = 0,
    ) -> dict | None:
        """Remember an edit/delete that arrived before its delayed original response.

        Group revisions do not identify which pending thread they answer, so they stay in
        a peer/message quarantine until ``observe_incoming`` supplies reply lineage.  DMs
        have one addressed user per thread and can bind the revision immediately.
        """
        candidates: list[tuple[float, int]] = []
        for index, item in enumerate(state["items"]):
            if str(item.get("target_peer_id")) != peer or item.get("status") != "pending":
                continue
            target_user = item.get("target_user_id")
            if not target_user or mid <= int(item.get("sent_message_id") or 0):
                continue
            candidates.append((float(item.get("sent_at") or 0), index))
        if candidates:
            _, index = max(candidates)
            item = state["items"][index]
            existing = item.get("pending_response_revision") or {}
            existing_source = str(existing.get("source_id") or "")
            existing_order = int(existing.get("revision_order") or 0)
            if (source_id == existing_source
                    or _response_revision_is_stale(
                        existing_source, source_id,
                        existing_order=existing_order,
                        incoming_order=int(revision_order),
                    )):
                return None
            item["pending_response_revision"] = {
                "peer_id": peer, "message_id": mid, "kind": kind,
                "source_id": source_id, "observed_at": observed_at,
                "revision_order": int(revision_order),
                **({"text": str(text or "")[:4000]} if kind == "edit" else {}),
            }
            if kind == "deletion":
                item["status"] = "response_deleted"
                item["notice_skipped"] = "ответ удалён до получения исходного апдейта"
                item["notice_skipped_at"] = observed_at
            _save(state, self.path)
            return dict(item)

        pending = state.setdefault("pending_revisions", [])
        existing = next((row for row in pending
                         if str(row.get("peer_id")) == peer
                         and int(row.get("message_id") or 0) == mid), None)
        existing_source = str((existing or {}).get("source_id") or "")
        existing_order = int((existing or {}).get("revision_order") or 0)
        if (source_id == existing_source
                or _response_revision_is_stale(
                    existing_source, source_id,
                    existing_order=existing_order,
                    incoming_order=int(revision_order),
                )):
            return None
        row = {
            "peer_id": peer, "message_id": mid, "kind": kind,
            "source_id": source_id, "observed_at": observed_at,
            "revision_order": int(revision_order),
            **({"text": str(text or "")[:4000]} if kind == "edit" else {}),
        }
        state["pending_revisions"] = [
            item for item in pending
            if not (str(item.get("peer_id")) == peer
                    and int(item.get("message_id") or 0) == mid)
        ][-999:] + [row]
        _save(state, self.path)
        return None

    def revise_response(
        self, *, peer_id: str | int, message_id: str | int, text: str,
        revision_source_id: str, observed_at: float | None = None,
        revision_order: int = 0,
    ) -> dict | None:
        """Project an admitted Telegram edit onto a stored response.

        Returns the changed thread only when the lineage exists and the revision is
        newer/current under the same timestamp ordering as the live Telegram buffer.
        Every accepted revision is appended to ``response.revisions``; current consumers
        read only ``response.text``.
        """
        peer, mid = str(peer_id), int(message_id)
        source_id = str(revision_source_id or "").strip()
        if not source_id or ":edit:" not in source_id:
            raise ValueError("an edit revision source id is required")
        now = float(observed_at if observed_at is not None else time.time())
        with _LOCK:
            state = _load(self.path)
            for item in state["items"]:
                if not self._response_matches(item, peer, mid):
                    continue
                response = item["response"]
                existing = str(response.get("revision_source_id") or mid)
                revisions = response.get("revisions")
                if not isinstance(revisions, list):
                    revisions = [{
                        "kind": "message", "source_id": existing,
                        "text": str(response.get("text") or "")[:4000],
                        "observed_at": response.get("received_at"),
                    }]
                    response["revisions"] = revisions
                if (source_id == existing
                        or any(str(row.get("source_id") or "") == source_id
                               for row in revisions if isinstance(row, dict))
                        or _response_revision_is_stale(
                            existing, source_id,
                            existing_order=int(response.get("revision_order") or 0),
                            incoming_order=int(revision_order),
                        )):
                    return None
                value = str(text or "")[:4000]
                revisions.append({
                    "kind": "edit", "source_id": source_id,
                    "text": value, "observed_at": now,
                })
                response["text"] = value
                response["revision_source_id"] = source_id
                response["revision_order"] = int(revision_order)
                response["edited_at"] = now
                if not item.get("notified_at"):
                    item["status"] = "answered"
                    if str(item.get("notice_skipped") or "").startswith("ответ удалён"):
                        item["notice_skipped"] = ""
                        item["notice_skipped_at"] = None
                _save(state, self.path)
                return dict(item)
            return self._store_pending_revision(
                state, peer=peer, mid=mid, kind="edit", source_id=source_id,
                observed_at=now, text=text, revision_order=int(revision_order),
            )

    def delete_response(
        self, *, peer_id: str | int, message_id: str | int,
        observed_at: float | None = None,
    ) -> dict | None:
        """Tombstone a stored response and suppress any still-unsent owner notice.

        A notification already marked ``notified`` remains historical truth and is not
        described as recalled.  The original/edited text survives only in the append-only
        revision evidence, never in the current response fields or live context.
        """
        peer, mid = str(peer_id), int(message_id)
        source_id = f"{mid}:delete"
        now = float(observed_at if observed_at is not None else time.time())
        with _LOCK:
            state = _load(self.path)
            for item in state["items"]:
                if not self._response_matches(item, peer, mid):
                    continue
                response = item["response"]
                existing = str(response.get("revision_source_id") or mid)
                revisions = response.get("revisions")
                if not isinstance(revisions, list):
                    revisions = [{
                        "kind": "message", "source_id": existing,
                        "text": str(response.get("text") or "")[:4000],
                        "observed_at": response.get("received_at"),
                    }]
                    response["revisions"] = revisions
                if (existing == source_id
                        or any(str(row.get("source_id") or "") == source_id
                               for row in revisions if isinstance(row, dict))):
                    return None
                revisions.append({
                    "kind": "deletion", "source_id": source_id,
                    "observed_at": now,
                })
                response["text"] = ""
                response["deleted"] = True
                response["deleted_at"] = now
                response["revision_source_id"] = source_id
                if not item.get("notified_at"):
                    item["status"] = "response_deleted"
                    item["notice_skipped"] = "ответ удалён до отправки отчёта"
                    item["notice_skipped_at"] = now
                _save(state, self.path)
                return dict(item)
            return self._store_pending_revision(
                state, peer=peer, mid=mid, kind="deletion", source_id=source_id,
                observed_at=now,
            )

    def pending_notifications(self) -> list[dict]:
        """Только ЗАКАЗАННЫЕ отчёты: след нити — её память, а не почта Егору.

        До 27.07 здесь стояло «любая отвеченная нить» — отсюда и брались 17 писем
        ему за две недели, ни одного из которых он не просил.
        """
        return [x for x in self.list(status="answered") if _owes_owner_notice(x)]

    def cancel(self, followup_id: str) -> bool:
        with _LOCK:
            state = _load(self.path)
            changed = False
            for item in state["items"]:
                if item.get("id") == followup_id and item.get("status") in ("pending", "answered"):
                    item["status"] = "cancelled"
                    item["cancelled_at"] = time.time()
                    changed = True
                    break
            if changed:
                state["items"] = _compact(state["items"])
                _save(state, self.path)
            return changed

    def set_notice(self, followup_id: str, on: bool, *, source: str = "praxis") -> dict | None:
        """Включить/выключить отчёт Егору по конкретной нити.

        Её рычаг: раньше она могла только ОТМЕНЯТЬ чужое решение (`cancel`) — шесть
        раз и отменяла. Теперь она автор нити, а не проситель: завести отчёт там,
        где он нужен ей, и снять там, где не нужен. Возвращает None, если нити нет
        или она уже закрыта — молча «получилось» отвечать нельзя.
        """
        with _LOCK:
            state = _load(self.path)
            for item in state["items"]:
                if item.get("id") != followup_id:
                    continue
                if item.get("status") not in ("pending", "answered"):
                    return None
                item["notify_owner"] = bool(on)
                item["notice_source"] = (str(source or "")[:40] if on else "")
                if on:
                    item["notice_skipped"] = ""
                    # Обязательство, в отличие от следа, по возрасту не гаснет.
                    # Явно названный в тексте срок («код действует 30 минут») —
                    # единственное, что остаётся сильнее заказа.
                    if explicit_expiry_seconds(item.get("request_text") or "") is None:
                        item["expires_at"] = None
                _save(state, self.path)
                return dict(item)
            return None

    def mark_notified(self, followup_id: str, *, at: float | None = None) -> bool:
        with _LOCK:
            state = _load(self.path)
            changed = False
            for item in state["items"]:
                if (item.get("id") == followup_id
                        and item.get("status") == "answered"
                        and isinstance(item.get("response"), dict)
                        and not item.get("notified_at")):
                    item["notified_at"] = float(at if at is not None else time.time())
                    item["status"] = "notified"
                    changed = True
                    break
            if changed:
                state["items"] = _compact(state["items"])
                _save(state, self.path)
            return changed

    def context(self, *, limit: int = CONTEXT_LIMIT, offset: int = 0) -> str:
        """Compact factual context for Praxis's hourly social pulse.

        ⚠ 01.08: лента отдавала САМЫЕ СТАРЫЕ незакрытые нити (`unresolved[:cap]`), а
        закрытые добивали остаток. При 18 pending и потолке 12 свежее просто не влезало:
        в 05:29 она дважды спросила «что у меня в нитях», оба раза получила одни и те же
        12 чужих ниток возрастом 62-66 часов — и, не найдя себя рядом с Егором, поздоровалась
        с ним второй раз за утро. Лента пульса отвечает на вопрос «что я только что делала»,
        поэтому по умолчанию она показывает СВЕЖЕЕ. Старые обязательства при этом не
        пропадают: они гаснут сами через TRACE_TTL_SEC и достижимы явным offset — который
        до сегодня молча игнорировался, и пролистывание возвращало ту же страницу.
        """
        all_items = self.list()
        cap = max(1, int(limit))
        skip = max(0, int(offset))
        unresolved = [
            item for item in all_items
            if item.get("status") == "pending" or _owes_owner_notice(item)
        ]
        fresh_first = list(reversed(unresolved))
        items = list(reversed(fresh_first[skip:skip + cap]))
        if len(items) < cap:
            unresolved_ids = {str(item.get("id") or "") for item in unresolved}
            settled = [
                item for item in all_items
                if str(item.get("id") or "") not in unresolved_ids
                and item.get("status") != "expired"
            ]
            items.extend(settled[-(cap - len(items)):])
        if not items:
            return "Нет активных Telegram follow-up нитей."
        rows = []
        now = time.time()
        for item in items:
            age_h = max(0.0, (now - float(item.get("sent_at") or now)) / 3600)
            row = (
                f"- {item.get('id')} [{item.get('status')}] {item.get('target_label')} "
                f"peer={item.get('target_peer_id')} message_id={item.get('sent_message_id')} "
                f"{age_h:.1f}ч назад"
            )
            # Inside the hourly pulse the Telegram client is disconnected, so this
            # ledger is the only record of what a thread already covered.  Without it
            # a wake re-derives "I should tell them X" and sends the same thing again.
            # Поэтому гист — это в первую очередь ЕЁ отправленный текст: `request_text`
            # у owner-веток содержит слова Егора («им отправить»), и анти-повтор на
            # них не работает вовсе. `sent_text` читаем тоже: соседняя бригада вводит
            # то же поле под своим именем — пусть смена имени не гасит след молча.
            # ⚠ 04.08. Здесь ЕЁ СОБСТВЕННЫЙ отправленный текст подписывался как «повод
            # отправки» — то есть единственное место кадра, где лежит ответ на вопрос
            # «что я уже сказала этому человеку», было озаглавлено как причина, а не как
            # речь. Данные доезжали, позиция терялась. Разводим два разных факта:
            # sent_excerpt/sent_text — это её слова, request_text — чужая просьба.
            said = str(item.get("sent_excerpt") or item.get("sent_text") or "").strip()
            asked = str(item.get("request_text") or "").strip()
            if said:
                row += f"; я сказала: «{said[:240]}»"
            elif asked:
                row += f"; повод отправки: {asked[:240]}"
            # Правило 2 в строке, которую она читает: кто заказал отчёт и уйдёт ли он.
            if item.get("notify_owner"):
                source = str(item.get("notice_source") or "").strip()
                row += "; отчёт Егору: " + {
                    "owner": "заказан им словами",
                    "praxis": "мой — я включила сама",
                }.get(source, f"заказан ({source})" if source else "заказан")
            else:
                row += "; отчёт Егору: нет — это мой след"
            skipped = str(item.get("notice_skipped") or "").strip()
            if skipped:
                row += f"; письма не будет: {skipped}"
            response = item.get("response") or {}
            if response:
                row += (
                    f"; ответ message_id={response.get('message_id')}: "
                    f"{str(response.get('text') or '')[:240]}"
                )
            rows.append(row)
        # Все четыре предела названы здесь и только здесь: раннер отдаёт текст
        # целиком именно потому, что режет и объявляет их леджер (правило 2 —
        # молчаливого усечения быть не должно ни на одном слое).
        return (
            f"Telegram follow-up ledger (факты; показываю {len(items)} нитей из "
            f"{len(all_items)}, не больше {cap}; тексты режу до 240 симв. в строке "
            f"и до 500 в самой записи; след без ответа гаснет через "
            f"{TRACE_TTL_SEC / 3600:.0f}ч, заказанный отчёт не гаснет никогда):\n"
            + "\n".join(rows)
        )


LEDGER = FollowUpLedger()
