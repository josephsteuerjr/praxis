"""Frozen group epoch E and source-complete append tape, selected per wake.

PRAXIS_FRAME_EPOCH and PRAXIS_FRAME_EPOCH_ROOMS select the existing rollout
(default off; '*' all groups, never DMs). ARCHITECTURE.md owns the source
contract: canonical current uncovered life + exact residuals, not a numeric
anchor or a replay of the entire historical raw archive. Producer-stamped live
archive receipts preserve failed life writes; backfill is archival evidence.

E, summary, tape, source boundary and cursor travel together in GroupWake.
The existing epoch JSON is the sole durable store, with one writer lock for
prepare/serve/flip. Selected text is never silently shortened or replaced by a
sliding window. Oversized/incomplete sources remain on disk with an explicit
operational outcome; no model is called by this module and no compactor added.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import json
import logging
import os
import re
import sys
import time
import threading
import uuid
from pathlib import Path

log = logging.getLogger("praxis-epoch")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
STORE = BASE / "memory" / ".state" / "epoch"
SCHEMA = 1
LEVER = "PRAXIS_FRAME_EPOCH"
ROOMS = "PRAXIS_FRAME_EPOCH_ROOMS"
# Existing source-capacity setting (0 = unlimited). Preserve full material and
# raise EpochUnavailable on overflow, never substitute the old sliding window.
MAX_CHARS_ENV = "PRAXIS_FRAME_EPOCH_MAX_CHARS"
DEFAULT_MAX_CHARS = 400_000

_ON = {"1", "true", "yes", "on"}

# Заголовки ярусов конверта, которые едут в E. Сверка по ЗАГОЛОВКУ ярлыка (до скобочной
# оговорки сборщика, `frame_layout.split_title`), префиксом — как в `frame_layout._TIERS`.
# Живые ярусы с тем же корнем названы отдельно и проверяются ПЕРВЫМИ.
STABLE_PREFIXES = (
    "Canonical desire continuity",
    "Ранее в этом диалоге",
    "Мои досье на людей",
    "Карта памяти",
    "Почтовый ящик — ЛОКАТОР",
    "Куда здесь уходит ответ",
    "Эта комната",
)
# ⚑ ЕЁ §5.1, дословно (личка 15.09 11:26): «„Визитка“ и „Mutable operational continuity“ — в
# хвост, не разрез внутри E». Первая редакция кандидата держала визитку в E, а её живую
# фактуру (расход, окна, карточки) выносила отдельным ярусом — это и есть разрез, который
# она отклонила. Визитка целиком остаётся живым ярусом конверта; в E её нет.
LIVE_PREFIXES = (
    "Эпоха этой комнаты",
)
# Ярусы, которые под эпохой рождаются здесь. Ярлыки — литералы кадра: причины и слоты в
# `frame_layout._TIERS` по префиксу.
LIVE_TIER = ("Эпоха этой комнаты — живая справка (что заморожено, что изменилось после; "
             "в эпоху не входит)")

_BOUND: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_anchor", default=None)
_TAKEN: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_message", default=None)
_ACTIVE: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_active", default=False)
_TAPE: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_tape", default=None)
_STORE_LOCK = threading.RLock()


@contextlib.contextmanager
def tape_store_guard(chat_id):
    """All writers of the existing epoch JSON share this transaction."""
    from self_model import FileLock
    with _STORE_LOCK, FileLock(path_for(chat_id).with_suffix(".lock"),
                               timeout=30.0, stale_after=300.0, heartbeat=5.0):
        yield


class EpochUnavailable(RuntimeError):
    """Full source is retained, but cannot honestly be served as an ordinary pass."""


def require_servable(selection):
    capacity = selection["capacity"]
    if not selection["complete"] or not capacity["within"]:
        raise EpochUnavailable(
            f"epoch_source_unavailable: chat={selection['chat']} n={selection['n']} "
            f"chars={capacity['chars']} cap={capacity['cap']} gaps={len(selection['gaps'])}; "
            "full tape retained in epoch JSON; no sliding fallback. "
            "Inspect residual sources/coverage or explicitly revise the source capacity setting.")


def _hot_key(row):
    return hashlib.sha256(json.dumps(
        [row.get("id"), row.get("source_id"), row.get("direction"), row.get("line")],
        ensure_ascii=False).encode()).hexdigest()


def _hot_row(row):
    line, actor = row["line"], row.get("actor") or ""
    own = row["direction"] == "out"
    prefix = actor + ": "
    return {"self": own, "line": line,
            "role_line": line[len(prefix):] if own and actor and line.startswith(prefix) else line,
            "logical_send": copy.deepcopy((row.get("meta") or {}).get("logical_send")),
            "source": {key: row.get(key) for key in
                       ("id", "source_id", "chat", "unprovable", "state_residual")}}


def prepare_tape(chat_id, *, peer_id, topic_id=None, whole_room=False,
                 members=None, thread_word=None, summary_max_chars=7000):
    """Current canonical life baseline + append-only archive after bootstrap.

    Prebaseline archive is retained raw evidence, NOT assumed covered or selected.
    The cursor precedes the coherent life read, so neither ingestion write order
    loses a message between the two. Future orphan archive occurrences survive
    subsequent authored folds until exact source coverage can account for them.
    """
    import group_context
    import memory_life
    import memory_provenance

    scope = dict(peer_id=str(peer_id), topic_id=topic_id, whole_room=bool(whole_room),
                 members=sorted(members, key=lambda item: (item is not None, item))
                 if members is not None else None,
                 thread_word=thread_word)
    with tape_store_guard(chat_id):
        bootstrap = group_context.archive_cursor(peer_id)
        initial = None
        record = load(chat_id) or {}
        old = record.get("tape") or {}
        if old.get("v") != 4:
            # Only new producer-stamped live evidence can recover a failed life
            # write before bootstrap. Legacy/backfill is not inferred live.
            initial = group_context.archive_occurrences(peer_id)
        snapshot = memory_life.epoch_snapshot(chat_id, summary_max_chars=summary_max_chars)
        summary_policy = dict(max_chars=summary_max_chars, sha256=hashlib.sha256(
            snapshot["summary"].encode("utf-8")).hexdigest())
        same = (old.get("v") == 4 and old.get("boundary") == snapshot["boundary"]
                and old.get("scope") == scope and old.get("flip_id") == record.get("pending_id")
                and old.get("summary_policy") == summary_policy)
        if same:
            tape = copy.deepcopy(old)
        else:
            n = max(int(record.get("n") or 0), int(old.get("n") or 0)) + 1
            anchor = next((int(str(r.get("source_id") or "").split(":", 1)[0])
                           for r in snapshot["hot"]
                           if str(r.get("source_id") or "").split(":", 1)[0].isdigit()), 0)
            tape = dict(v=4, n=n, anchor=anchor, boundary=snapshot["boundary"],
                        scope=scope, flip_id=record.get("pending_id"), summary=snapshot["summary"],
                        summary_policy=summary_policy,
                        frozen_at=_stamp(), reason=("flip: " + str(record["pending"])
                                                   if record.get("pending") else "source_boundary"),
                        rows=[], cursor=old.get("cursor") if old.get("v") == 4 else bootstrap,
                        bootstrap_cursor=old.get("bootstrap_cursor", bootstrap),
                        selection="canonical_current_uncovered_plus_future_archive",
                        prebaseline_archive="retained_archival_evidence_not_claimed_covered",
                        seen_hot=[], residuals=[], gaps=[])
        covered = set(snapshot["covered_event_ids"])
        current_ids = set(snapshot.get("current_event_ids", [r["id"] for r in snapshot["hot"]]))
        events = {}
        for event in snapshot["events"]:
            key = memory_provenance.telegram_message_key(event)
            if key and str(key[0]) == str(peer_id):
                signature = (str(event.get("source_id") or ""),
                             str(event.get("direction") or ""), str(event.get("text", "")))
                events.setdefault(signature, []).append(str(event.get("id")))
        hot = {str(r["id"]): r for r in snapshot["hot"] if not r.get("state_residual")}
        seen = set(tape["seen_hot"])

        def emit(row):
            identity = _hot_key(row)
            if identity not in seen:
                tape["rows"].append(_hot_row(row))
                seen.add(identity)

        def identities(occurrence):
            signature = group_context.epoch_life_signature(occurrence["row"])
            return events.get(signature, []) if signature else []

        if same:
            # A life write can finish AFTER its archive occurrence was already
            # selected. Keep the frozen archive bytes, but do not append it twice.
            archived_rows = {(r["archive_occurrence"]["offset"], r["archive_occurrence"]["sha256"]): r
                             for r in tape["rows"] if "archive_occurrence" in r}
            for occurrence in tape["residuals"]:
                if "row" in occurrence:
                    for eid in identities(occurrence):
                        if eid in hot:
                            seen.add(_hot_key(hot[eid]))
                            archived = archived_rows.get((occurrence["offset"], occurrence["sha256"]))
                            if archived is not None and (hot[eid].get("meta") or {}).get("logical_send"):
                                archived["logical_send"] = copy.deepcopy(
                                    (hot[eid].get("meta") or {}).get("logical_send"))
        if not same:
            for row in snapshot["hot"]:
                emit(row)
        batch = initial if initial is not None else group_context.archive_occurrences(peer_id, cursor=tape["cursor"])
        pending = ([] if same else old.get("residuals", [])) + batch["occurrences"]
        for occurrence in pending:
            if "gap" in occurrence:
                if initial is not None and occurrence["end"] <= bootstrap["offset"]:
                    continue  # undecodable legacy archive is not asserted live
                tape["gaps"].append({k: occurrence[k] for k in ("offset", "end", "sha256", "gap")})
                tape["rows"].append(group_context.epoch_occurrence_row(occurrence))
                tape["residuals"].append(occurrence)
                continue
            raw = occurrence["row"]
            if raw.get("origin") != "live":
                continue
            in_scope = (whole_room or raw.get("topic_id") == topic_id
                        or members is not None and raw.get("topic_id") in members
                        or topic_id is not None and topic_id in
                        (raw.get("message_id"), raw.get("reply_to_message_id")))
            if raw["kind"] not in ("message", "deletion") or not in_scope:
                continue
            ids = identities(occurrence)
            if (initial is not None and occurrence["end"] <= bootstrap["offset"]
                    and ids and all(eid not in current_ids for eid in ids)):
                continue  # exact superseded historical revision, not coverage
            if ids and any(eid in hot for eid in ids):
                for eid in ids:
                    if eid in hot:
                        emit(hot[eid])
                continue
            if ids and all(eid in covered for eid in ids):
                continue
            tape["rows"].append(group_context.epoch_occurrence_row(occurrence))
            tape["residuals"].append(occurrence)
        for row in snapshot["hot"]:
            emit(row)
        tape["cursor"], tape["seen_hot"] = batch["cursor"], sorted(seen)
        tape["incomplete_tail_bytes"] = batch.get("incomplete_tail_bytes", 0)
        record["tape"] = tape
        _write_atomic(path_for(chat_id), json.dumps(record, ensure_ascii=False, indent=1))
        chars = max(sum(len(row[k]) + 1 for row in tape["rows"])
                    for k in ("line", "role_line")) + len(tape["summary"])
        cap = max_chars()
        selected = dict(tape, chat=str(chat_id), complete=not tape["gaps"]
                        and not tape["incomplete_tail_bytes"],
                        capacity=dict(chars=chars, cap=cap, within=cap <= 0 or chars <= cap))
        if record.get("envelope_key") == [tape["n"], tape["boundary"]]:
            selected["envelope"] = {k: record[k] for k in
                                    ("blocks", "n", "anchor", "frozen_at", "reason", "chars")}
        return copy.deepcopy(selected)


# ------------------------------------------------------------------------------ рычаги


def enabled() -> bool:
    return str(os.getenv(LEVER) or "").strip().lower() in _ON


def rooms() -> tuple[str, ...]:
    return tuple(x.strip() for x in str(os.getenv(ROOMS) or "").split(",") if x.strip())


def room_enabled(chat_id) -> bool:
    """Комната под эпохой: рычаг включён И комната названа (или `*`)."""
    if not enabled() or chat_id is None:
        return False
    listed = rooms()
    return "*" in listed or str(chat_id) in listed


def applies(ctx) -> bool:
    """Эпоха применима к этому ctx: комната (не личка, не свой ход) из списка."""
    return bool(ctx is not None
                and not getattr(ctx, "is_dm", True)
                and getattr(ctx, "chat_id", None) is not None
                and room_enabled(getattr(ctx, "chat_id", None)))


def max_chars() -> int:
    try:
        return max(0, int(os.getenv(MAX_CHARS_ENV, str(DEFAULT_MAX_CHARS)) or 0))
    except ValueError:
        return DEFAULT_MAX_CHARS


def stable_title(title: str) -> bool:
    """Ярус едет в E? Решается по заголовку ярлыка; живые с тем же корнем — первыми."""
    import frame_layout  # noqa: PLC0415 — только ради split_title, без цикла импорта
    head = frame_layout.split_title(title)[0]
    if head.startswith(LIVE_PREFIXES):
        return False
    return head.startswith(STABLE_PREFIXES)


# ------------------------------------------------------------------------------- якорь


def anchor_for(chat_id) -> int | None:
    """message_id первой записи горячего окна memory_life — граница последней свёртки.

    Правки в горячем слое лежат отдельными записями с source_id вида
    `<mid>:edit:<ts>:<hash>` — берётся ведущее число. Нет записей или числа — якоря нет,
    и эпоха на этот ход не собирается (вызывающий идёт прежним путём и говорит об этом)."""
    try:
        import memory_life  # noqa: PLC0415
        for row in memory_life.hot_records(chat_id):
            sid = row.get("source_id") or (row.get("meta") or {}).get("source_id")
            head = str(sid or "").split(":", 1)[0].strip()
            if head.isdigit():
                return int(head)
    except Exception:
        log.debug("якорь эпохи не прочитался [%s]", chat_id, exc_info=True)
    return None


@contextlib.contextmanager
def bind(chat_id, anchor, *, refused: bool = False, tape: dict | None = None):
    """Привязать якорь, с которого раннер собрал ленту, к ходу: сборщик кадра читает его
    отсюда, а не считает заново (между снимком и ходом могла пройти свёртка).

    `refused=True` — раннер собрал ленту ПРЕЖНИМ окном (якоря нет, лента пуста или
    превысила предохранитель). 26.09 (ревью W1 S6): прежде это было неотличимо от «якорь
    не передали», сборщик считал якорь сам и подавал эпоху со справкой «лента с сообщения
    #якорь», хотя лента хода начиналась много позже, а между ними не было ни сводки, ни
    ленты."""
    if refused:
        value = (str(chat_id), None)
    else:
        value = (str(chat_id), int(anchor)) if anchor is not None else None
    token = _BOUND.set(value)
    tape_token = _TAPE.set((str(chat_id), copy.deepcopy(tape))
                           if tape is not None and not refused else None)
    try:
        yield
    finally:
        _TAPE.reset(tape_token)
        _BOUND.reset(token)


def bound_tape(chat_id) -> dict | None:
    """The exact detached tape selected for this wake, not later room state."""
    value = _TAPE.get()
    return value[1] if value is not None and value[0] == str(chat_id) else None


def bound_anchor(chat_id) -> int | None:
    value = _BOUND.get()
    if value is None or value[0] != str(chat_id) or value[1] is None:
        return None
    return int(value[1])


def refused(chat_id) -> bool:
    """Раннер собрал ленту этого хода прежним окном — эпоху подавать нельзя."""
    value = _BOUND.get()
    return value is not None and value[0] == str(chat_id) and value[1] is None


# ------------------------------------------------------------------------------ хранение


def _safe(chat_id) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(chat_id)) or "_"


def path_for(chat_id) -> Path:
    return STORE / f"{_safe(chat_id)}.json"


def load(chat_id) -> dict | None:
    try:
        raw = json.loads(path_for(chat_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        log.exception("эпоха комнаты [%s] нечитаема — перезаморозка", chat_id)
        return None
    return raw if isinstance(raw, dict) else None


def _write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _stamp(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts if ts is not None else time.time()))


def _sha8(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def serve(chat_id, anchor: int, blocks: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str, str]], dict]:
    with tape_store_guard(chat_id):
        selected = bound_tape(chat_id)
        if selected is None:
            return _serve_legacy(chat_id, anchor, blocks)
        require_servable(selected)
        saved = load(chat_id) or {}
        envelope = selected.get("envelope")
        key = [selected["n"], selected["boundary"]]
        if envelope is None and saved.get("envelope_key") == key:
            envelope = {k: copy.deepcopy(saved[k]) for k in
                        ("blocks", "n", "anchor", "frozen_at", "reason", "chars")}
        if envelope is None:
            envelope = dict(blocks=[list(b) for b in blocks], n=selected["n"],
                            anchor=selected["anchor"], frozen_at=selected["frozen_at"],
                            reason=selected["reason"], chars=sum(len(b) for _t, _k, b in blocks))
            # A delayed older wake may serve itself, never roll room storage back.
            current = saved.get("tape") or {}
            if [current.get("n"), current.get("boundary")] == key:
                saved.update(envelope, envelope_key=key)
                if saved.get("pending_id") == selected.get("flip_id"):
                    saved.pop("pending", None)
                _write_atomic(path_for(chat_id), json.dumps(saved, ensure_ascii=False, indent=1))
        selected["envelope"] = copy.deepcopy(envelope)
        frozen = [tuple(item) for item in envelope["blocks"]]
        old = {t: b for t, _k, b in frozen}
        now = {t: b for t, _k, b in blocks}
        drift = {t: dict(frozen_chars=len(old.get(t, "")), current_chars=len(now.get(t, "")))
                 for t in sorted(set(old) | set(now)) if old.get(t) != now.get(t)}
        receipt = {k: envelope[k] for k in ("n", "anchor", "frozen_at", "chars")}
        receipt.update(reason="reused", frozen_reason=envelope["reason"], drift=drift,
                       boundary=selected["boundary"], cursor=copy.deepcopy(selected["cursor"]),
                       selection=selected["selection"],
                       bootstrap_cursor=copy.deepcopy(selected["bootstrap_cursor"]))
        return frozen, receipt


def _serve_legacy(chat_id, anchor: int, blocks: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str, str]], dict]:
    """Замороженные блоки E для (комната, якорь) и расписка.

    `blocks` — (ярлык, kind, текст блока) такими, какими они собрались бы СЕЙЧАС. Если для
    комнаты есть эпоха с тем же якорем — возвращаются ЕЁ блоки байт-в-байт, а расхождение с
    нынешними считается по ярусам (drift) и едет в расписку. Иначе эпоха замораживается
    заново: первая, по сдвигу якоря или по ручному перевороту (`pending`)."""
    saved = load(chat_id)
    now_sha = {title: _sha8(block) for title, _kind, block in blocks}
    now_chars = {title: len(block) for title, _kind, block in blocks}
    if (saved and int(saved.get("anchor") or -1) == int(anchor) and not saved.get("pending")
            and isinstance(saved.get("blocks"), list)):
        frozen = [(str(t), str(k), str(b)) for t, k, b in saved["blocks"]
                  if isinstance(b, str)]
        old_sha = {t: _sha8(b) for t, _k, b in frozen}
        old_chars = {t: len(b) for t, _k, b in frozen}
        drift = {}
        for title in sorted(set(old_sha) | set(now_sha)):
            if old_sha.get(title) != now_sha.get(title):
                drift[title] = {"frozen_chars": old_chars.get(title, 0),
                                "current_chars": now_chars.get(title, 0)}
        receipt = {"n": int(saved.get("n") or 0), "anchor": int(anchor),
                   "frozen_at": str(saved.get("frozen_at") or ""),
                   "reason": "reused", "frozen_reason": str(saved.get("reason") or ""),
                   "drift": drift, "chars": sum(old_chars.values())}
        return frozen, receipt
    n = int((saved or {}).get("n") or 0) + 1
    if not saved:
        reason = "first"
    elif saved.get("pending"):
        reason = "flip: " + str(saved.get("pending"))
    else:
        reason = f"anchor_moved: #{saved.get('anchor')} → #{anchor}"
    record = dict(saved or {})
    record.update({
        "v": SCHEMA, "chat": str(chat_id), "n": n, "anchor": int(anchor),
        "frozen_at": _stamp(), "reason": reason,
        "blocks": [[t, k, b] for t, k, b in blocks],
        "sha": {t: s for t, s in now_sha.items()},
        "chars": sum(now_chars.values()),
    })
    record.pop("pending", None)
    try:
        _write_atomic(path_for(chat_id), json.dumps(record, ensure_ascii=False, indent=1))
    except Exception:
        # Не записалось — эпоха всё равно едет (этот ход честный), но следующий ход
        # заморозит её снова под тем же номером: это видно в расписке по reason.
        log.exception("эпоха комнаты [%s] не записалась", chat_id)
    receipt = {"n": n, "anchor": int(anchor), "frozen_at": record["frozen_at"],
               "reason": reason, "frozen_reason": reason, "drift": {},
               "chars": record["chars"]}
    return list(blocks), receipt


def flip(chat_id, reason: str = "её слово") -> int:
    """Ручная граница: следующий ход заморозит эпоху заново под номером n+1.
    Возвращает номер будущей эпохи."""
    with tape_store_guard(chat_id):
        saved = load(chat_id) or {}
        saved.update(pending=str(reason or "её слово"), pending_id=uuid.uuid4().hex)
        saved.setdefault("n", 0)
        _write_atomic(path_for(chat_id), json.dumps(saved, ensure_ascii=False, indent=1))
        return max(int(saved["n"]), int((saved.get("tape") or {}).get("n") or 0)) + 1


# --------------------------------------------------------------------------- кадр: тексты


_OPEN_RE = re.compile(r"^<praxis_epoch n=\d+ anchor=#\d+ frozen=[^>]+>$")


def head(receipt: dict) -> str:
    """Открывающая шапка E — наши строки (объявляются прибору как собранные нами)."""
    return (f"<praxis_epoch n={int(receipt.get('n') or 0)} anchor=#{int(receipt.get('anchor') or 0)} "
            f"frozen={receipt.get('frozen_at') or '?'}>\n"
            "# Эпоха этой комнаты — замороженный документ\n"
            "Собран на границе свёртки и не меняется до следующей границы; то, что стало известно "
            "позже, стоит ниже — в ленте разговора и в живом хвосте кадра. Права проверяются при "
            "вызове руки, а не этим документом.\n")


CLOSE = "</praxis_epoch>"


def message(receipt: dict, blocks: list[tuple[str, str, str]]) -> str:
    return head(receipt) + "".join(b for _t, _k, b in blocks) + CLOSE


def live_note(receipt: dict) -> str:
    """Тело живого яруса-справки: номер, якорь, время заморозки и что уехало с тех пор."""
    lines = [f"эпоха {int(receipt.get('n') or 0)} · заморожена {receipt.get('frozen_at') or '?'} UTC · "
             f"лента с сообщения #{int(receipt.get('anchor') or 0)} · "
             f"причина заморозки: {receipt.get('frozen_reason') or receipt.get('reason') or '?'}"]
    if receipt.get("selection"):
        lines[0] = (f"эпоха {receipt['n']} · заморожена {receipt['frozen_at']} UTC · "
                    "полный текущий непокрытый канон жизни (ghosts сохранены), затем "
                    "архивные occurrences после bootstrap; старый raw archive сохранён "
                    "для адресного чтения, не объявлен покрытым и автоматически не выбран. "
                    f"cursor={(receipt.get('cursor') or {}).get('offset', 0)} bytes; "
                    "message anchor — только ориентир, не фильтр.")
    drift = receipt.get("drift") or {}
    if drift:
        import frame_layout  # noqa: PLC0415
        parts = []
        for title, change in drift.items():
            headline = frame_layout.split_title(title)[0]
            delta = int(change.get("current_chars", 0)) - int(change.get("frozen_chars", 0))
            parts.append(f"{headline} ({'+' if delta >= 0 else ''}{delta} зн.)")
        lines.append("после заморозки изменились: " + "; ".join(parts)
                     + ". В эпохе — версия на момент заморозки; свежее достаётся руками "
                       "(recall, describe, group_context) и приедет со следующей эпохой.")
    else:
        lines.append("после заморозки ничего из замороженного не менялось.")
    lines.append("граница эпохи — свёртка этого места (memory_life); принудительно — "
                 "frame_epoch.flip(<комната>, причина).")
    return "\n".join(lines)


def activate(flag: bool) -> None:
    """Флаг хода «эпоха применяется»: сборщик выставляет его, решив про якорь; ярусы,
    собираемые ниже (досье), читают его отсюда, не меняя своих сигнатур."""
    _ACTIVE.set(bool(flag))


def active() -> bool:
    return bool(_ACTIVE.get())


def take(text: str | None, receipt: dict | None) -> None:
    """Сборщик кадра кладёт сюда готовое первое сообщение; голос забирает его."""
    _TAKEN.set((text, receipt) if text else None)


def taken() -> tuple[str, dict] | None:
    value = _TAKEN.get()
    return value if value and value[0] else None


def reset() -> None:
    _TAKEN.set(None)
    _ACTIVE.set(False)


# ------------------------------------------------------------------------------- CLI


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "show":
        saved = load(argv[1])
        if not saved:
            print("эпохи нет:", path_for(argv[1]))
            return 1
        print(json.dumps({k: v for k, v in saved.items() if k != "blocks"}, ensure_ascii=False, indent=1))
        for t, _k, b in saved.get("blocks") or []:
            print(f"  - {t[:70]!r}: {len(b)} зн.")
        return 0
    if len(argv) >= 2 and argv[0] == "flip":
        n = flip(argv[1], " ".join(argv[2:]) or "рычаг")
        print(f"следующая эпоха комнаты {argv[1]}: {n}")
        return 0
    print("использование: frame_epoch.py show <chat> | flip <chat> [причина]")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
