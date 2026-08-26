"""Forum-history scan: pure scan logic + fail-closed placement evidence.

Контракт: workspace/TRANSPORT-GATE-FORUM-HISTORY-25.08.md.

Инварианты:

1. Никакой public API этого модуля не принимает client/entity/receipt
   от caller-а. Transport closure строится ТОЛЬКО внутри mtproto_runner над
   module-global Telethon client. Тесты патчат глобальный клиент — это
   тестовая инъекция, не production-аргумент.
2. page_size/max_pages строго положительные целые; нарушение — ValueError
   до любого прохода (красный кейс: page_size<=0 выпускал history_exhausted).
3. complete=True даёт только честная пагинация: каждая страница возвращает id
   строго ниже запрошенного offset_id; пустая страница или достижение floor_id
   — исчерпание. Дубль/не-монотонность/RPC-сбой → incomplete, сигналы стираются.
4. Чистый полный скан не рождает ни TRUE, ни FALSE — только placement.
   FALSE-из-тишины запрещён: метода записать no_topic_openers_in_range тут нет.
5. General (topic_id == GENERAL_TOPIC_ID) — позитивный форумный сигнал
   (origin=history_header); MessageActionTopicCreate — origin=history_opener.
6. observe_history_floor применяет evidence только у complete-сканов,
   идемпотентен по (peer, range, scan_sha256); admission одноразовый
   in-process, рестарт процесса его не переносит.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

log = logging.getLogger("praxis.history_scan")

GENERAL_TOPIC_ID = 1
MIN_PAGE_SIZE = 1
MIN_MAX_PAGES = 1

ORIGIN_HISTORY_OPENER = "history_opener"
ORIGIN_HISTORY_HEADER = "history_header"
SOURCE_RUNTIME_TELETHON = "runtime_telethon_history_scan"


class HistoryScanError(RuntimeError):
    """Fail-closed ошибка скана: частичных записей не бывает."""


class ParameterError(HistoryScanError, ValueError):
    """Некорректные параметры — до любого прохода."""


@dataclass(frozen=True)
class ScanRange:
    floor_id: int
    ceiling_id: int

    def __post_init__(self) -> None:
        for name in ("floor_id", "ceiling_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ParameterError(f"{name} должен быть целым числом")
        if self.floor_id < 1:
            raise ParameterError("floor_id должен быть >= 1")
        if self.ceiling_id < self.floor_id:
            raise ParameterError("ceiling_id должен быть >= floor_id")

    def as_dict(self) -> dict[str, int]:
        return {"floor_id": self.floor_id, "ceiling_id": self.ceiling_id}


@dataclass(frozen=True)
class ScanSignal:
    message_id: int
    topic_id: int
    origin: str
    title: str | None = None

    @property
    def general(self) -> bool:
        return self.topic_id == GENERAL_TOPIC_ID

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "topic_id": self.topic_id,
            "origin": self.origin,
            "title": self.title,
            "general": self.general,
        }


def is_topic_opener(message: Any) -> bool:
    action = getattr(message, "action", None)
    return action is not None and type(action).__name__ == "MessageActionTopicCreate"


def message_topic_id(message: Any) -> int | None:
    """topic_id сообщения из reply_to (форумная ветка), либо None."""
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None
    top = getattr(reply_to, "reply_to_top_id", None)
    if top is not None:
        return int(top)
    if getattr(reply_to, "forum_topic", False):
        msg_id = getattr(reply_to, "reply_to_msg_id", None)
        if msg_id is not None:
            return int(msg_id)
    return None


def extract_signals(messages: Iterable[Any]) -> list[ScanSignal]:
    """Позитивные форумные сигналы страницы истории.

    Opener-ы: MessageActionTopicCreate (topic_id из reply_to или действия).
    General-заголовок: сообщение ветки topic_id=1 — ветка существует только
    в форуме, значит это позитивный сигнал без открытия темы.
    """
    signals: list[ScanSignal] = []
    seen: set[tuple[int, int, str]] = set()
    for message in messages:
        if message is None:
            continue
        mid = getattr(message, "id", None)
        if mid is None:
            continue
        mid = int(mid)
        if is_topic_opener(message):
            # id темы = id её корневого сообщения-открытия (сквозная нумерация
            # комнаты; см. telegram_routes._clean_topic_row). reply_to у
            # opener-а — это цепочка ответа, а НЕ id темы: игнорируем его,
            # иначе чужой ответ притворится созданием темы.
            topic_id = mid
            key = (mid, topic_id, ORIGIN_HISTORY_OPENER)
            if key not in seen:
                seen.add(key)
                title = getattr(getattr(message, "action", None), "title", None)
                signals.append(
                    ScanSignal(
                        message_id=mid,
                        topic_id=topic_id,
                        origin=ORIGIN_HISTORY_OPENER,
                        title=title if isinstance(title, str) and title.strip() else None,
                    )
                )
            continue
        header_topic = message_topic_id(message)
        if header_topic is not None and int(header_topic) == GENERAL_TOPIC_ID:
            key = (mid, GENERAL_TOPIC_ID, ORIGIN_HISTORY_HEADER)
            if key not in seen:
                seen.add(key)
                signals.append(
                    ScanSignal(
                        message_id=mid,
                        topic_id=GENERAL_TOPIC_ID,
                        origin=ORIGIN_HISTORY_HEADER,
                    )
                )
    return signals


@dataclass
class HistoryScanResult:
    peer_id: str
    range: ScanRange
    complete: bool = False
    reason: str = "not_run"
    pages: int = 0
    messages_seen: int = 0
    signals: list[ScanSignal] = field(default_factory=list)
    observed_ids: set[int] = field(default_factory=set)

    @property
    def general_count(self) -> int:
        return sum(1 for s in self.signals if s.general)

    @property
    def opener_count(self) -> int:
        return sum(1 for s in self.signals if s.origin == ORIGIN_HISTORY_OPENER)

    def scan_sha256(self) -> str:
        payload = {
            "peer": self.peer_id,
            **self.range.as_dict(),
            "complete": self.complete,
            "reason": self.reason,
            "pages": self.pages,
            "messages_seen": self.messages_seen,
            "general_count": self.general_count,
            "opener_count": self.opener_count,
            "signals": sorted(
                [s.message_id, s.topic_id, s.origin, s.general] for s in self.signals
            ),
            "observed_ids": sorted(self.observed_ids),
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "peer": self.peer_id,
            **self.range.as_dict(),
            "complete": self.complete,
            "reason": self.reason,
            "pages": self.pages,
            "messages_seen": self.messages_seen,
            "general_count": self.general_count,
            "opener_count": self.opener_count,
            "scan_sha256": self.scan_sha256(),
            "source": SOURCE_RUNTIME_TELETHON,
        }


async def run_history_scan(
    peer_id: str,
    scan_range: ScanRange,
    fetch_page: Callable[[int, int], Awaitable[list[Any]]],
    *,
    page_size: int = 100,
    max_pages: int = 200,
) -> HistoryScanResult:
    """Прогнать полный скан через приватный степпер fetch_page.

    fetch_page(offset_id, page_size) → страница сообщений с id < offset_id
    (семантика iter_messages). Замыкание создаётся только внутри
    mtproto_runner над runtime-owned client.

    Правила честности: страница обязана продвигаться (все её id строго меньше
    запрошенного offset_id); пустая страница — исчерпание истории; выход ниже
    floor_id — достигнут пол диапазона. Любое нарушение → incomplete без
    сигналов.
    """
    _validate_positive_int("page_size", page_size)
    _validate_positive_int("max_pages", max_pages)

    result = HistoryScanResult(peer_id=peer_id, range=scan_range)
    offset_id = scan_range.ceiling_id + 1
    seen: set[int] = set()

    try:
        while result.pages < max_pages:
            page = await fetch_page(offset_id, page_size)
            if not isinstance(page, list):
                raise HistoryScanError("страница не является списком сообщений")
            result.pages += 1

            if not page:
                result.complete = True
                result.reason = "history_exhausted"
                break

            page_ids: list[int] = []
            for message in page:
                mid = getattr(message, "id", None)
                if mid is None:
                    raise HistoryScanError("сообщение без id в странице")
                mid = int(mid)
                if mid >= offset_id:
                    raise HistoryScanError(
                        f"страница не продвинулась: id {mid} >= offset {offset_id}"
                    )
                if mid in seen:
                    raise HistoryScanError(f"дублированный id {mid}")
                seen.add(mid)
                if scan_range.floor_id <= mid <= scan_range.ceiling_id:
                    page_ids.append(mid)
            result.signals.extend(extract_signals(page))

            new_offset = min(
                int(getattr(m, "id")) for m in page if getattr(m, "id", None) is not None
            )
            if new_offset <= scan_range.floor_id:
                result.complete = True
                result.reason = "history_exhausted"
                break
            offset_id = new_offset
        else:
            result.reason = "page_budget_exhausted"
            return result
    except asyncio.CancelledError:
        result.complete = False
        result.reason = "cancelled"
        result.signals = []
        result.observed_ids = set()
        raise
    except HistoryScanError as exc:
        log.warning("history scan [%s] failed honest pagination: %s", peer_id, exc)
        result.complete = False
        result.reason = "pagination_violation"
        result.signals = []
        result.observed_ids = set()
        return result
    except Exception as exc:  # RPC/сеть/транспорт
        log.warning("history scan [%s] transport failure: %s", peer_id, type(exc).__name__)
        result.complete = False
        result.reason = f"rpc_error:{type(exc).__name__}"
        result.signals = []
        result.observed_ids = set()
        return result

    result.messages_seen = len(seen)
    result.observed_ids = seen
    return result


def _validate_positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError(f"{name} должен быть целым числом")
    if value < MIN_PAGE_SIZE:
        raise ParameterError(f"{name} должен быть >= {MIN_PAGE_SIZE}")


# --- Применение evidence: единственная точка записи -------------------------

_admission_lock = threading.Lock()
_admitted: set[tuple[str, int, int, str]] = set()


def _admission_key(result: HistoryScanResult) -> tuple[str, int, int, str]:
    return (
        result.peer_id,
        result.range.floor_id,
        result.range.ceiling_id,
        result.scan_sha256(),
    )


def reset_admission_registry() -> None:
    """Только для тестов: сбросить одноразовый in-process реестр допусков."""
    with _admission_lock:
        _admitted.clear()


def observe_history_floor(result: HistoryScanResult, *, apply: bool) -> dict[str, Any]:
    """Применить complete-скан к реестру маршрутов.

    Только позитивная семантика:
    - есть сигналы → одна эпоха topic_opener_seen от нижнего сигнала до
      ceiling (TRUE, вес 3) — ровно как живой opener в ленте;
    - чистый скан → НИ ОДНОЙ записи: placement остаётся за вызывающей
      стороной, форумного вердикта из тишины нет;
    - incomplete → отказ до любых записей;
    - повторный apply того же (peer, range, sha) идемпотентен.

    admission одноразовый in-process: рестарт процесса его не переносит.
    """
    if type(result) is not HistoryScanResult:
        raise HistoryScanError("observe_history_floor принимает только HistoryScanResult")
    if not result.complete:
        return {"applied": False, **result.summary(), "reason": f"incomplete:{result.reason}"}

    # Dry-run is observational only: it must neither mutate telegram_routes nor
    # consume the one-shot admission used by a later apply=true call.
    if not apply:
        return {
            "applied": False,
            "written": [],
            "signals": [s.as_dict() for s in result.signals],
            **result.summary(),
        }

    key = _admission_key(result)
    with _admission_lock:
        if key in _admitted:
            return {"applied": False, **result.summary(), "reason": "already_admitted"}
        _admitted.add(key)

    try:
        import telegram_routes

        written: list[str] = []
        if result.signals:
            floor_signal_id = min(s.message_id for s in result.signals)
            telegram_routes.observe(
                result.peer_id,
                kind="topic_opener_seen",
                since_message_id=floor_signal_id,
                until_message_id=result.range.ceiling_id,
                detail=(
                    f"scan {result.scan_sha256()[:12]}: openers {result.opener_count}, "
                    f"general {result.general_count}"
                ),
            )
            written.append("topic_opener_seen")
        return {
            "applied": apply,
            "written": written if apply else [],
            "signals": [s.as_dict() for s in result.signals],
            **result.summary(),
        }
    except Exception as exc:
        with _admission_lock:
            _admitted.discard(key)
        raise HistoryScanError(f"apply failed: {type(exc).__name__}") from exc
