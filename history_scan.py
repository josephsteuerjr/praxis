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
import os
import secrets
import stat
import tempfile
import threading
from datetime import date, datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

log = logging.getLogger("praxis.history_scan")

GENERAL_TOPIC_ID = 1
MIN_PAGE_SIZE = 1
MIN_MAX_PAGES = 1

ORIGIN_HISTORY_OPENER = "history_opener"
ORIGIN_HISTORY_HEADER = "history_header"
SOURCE_RUNTIME_TELETHON = "runtime_telethon_history_scan"
PRIVATE_FREEZE_ROOT = Path(__file__).resolve().parent / "private" / "history_scan"
PRIVATE_MANIFEST_ROOT = PRIVATE_FREEZE_ROOT.parent
# Corpus rows deliberately use the experiment vocabulary, not Telethon's raw
# field name. Keeping this allow-list narrow prevents new message attributes
# from silently entering a private corpus.
FREEZE_FIELDS = ("message_id", "date", "text", "reply_to_message_id", "topic_id")


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
    # Allocated only for the explicit private-freeze acquisition path.
    corpus: list[dict[str, Any]] | None = field(default=None, repr=False)

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
    capture_corpus: bool = False,
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

    result = HistoryScanResult(peer_id=peer_id, range=scan_range,
                               corpus=[] if capture_corpus else None)
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
                    if result.corpus is not None:
                        result.corpus.append(_canonical_message(message))
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
            result.corpus = None
            return result
    except asyncio.CancelledError:
        result.complete = False
        result.reason = "cancelled"
        result.signals = []
        result.observed_ids = set()
        result.corpus = None
        raise
    except HistoryScanError as exc:
        log.warning("history scan [%s] failed honest pagination: %s", peer_id, exc)
        result.complete = False
        result.reason = "pagination_violation"
        result.signals = []
        result.observed_ids = set()
        result.corpus = None
        return result
    except Exception as exc:  # RPC/сеть/транспорт
        log.warning("history scan [%s] transport failure: %s", peer_id, type(exc).__name__)
        result.complete = False
        result.reason = f"rpc_error:{type(exc).__name__}"
        result.signals = []
        result.observed_ids = set()
        result.corpus = None
        return result

    result.messages_seen = len(seen)
    result.observed_ids = seen
    return result


def _eligible_corpus_row(message: Any) -> dict[str, Any] | None:
    """Privacy-minimal row for a non-empty human-visible text message.

    Service/action messages are pagination evidence, never corpus material.
    """
    if getattr(message, "action", None) is not None:
        return None
    row = _canonical_message(message)
    if not row["text"].strip():
        return None
    return row


async def acquire_eligible_corpus(
    peer_id: str,
    ceiling_id: int,
    fetch_page: Callable[[int, int], Awaitable[list[Any]]],
    *,
    eligible_limit: int = 500,
    page_size: int = 100,
    max_pages: int = 200,
) -> HistoryScanResult:
    """Boundedly discover eligible rows without attesting, freezing or mutating.

    Actions and blank/deleted bodies advance the pagination cursor but never
    enter the corpus. The returned candidate range is for a separate exact
    range attestation before an explicit freeze.
    """
    _validate_positive_int("eligible_limit", eligible_limit)
    _validate_positive_int("page_size", page_size)
    _validate_positive_int("max_pages", max_pages)
    if isinstance(ceiling_id, bool) or not isinstance(ceiling_id, int) or ceiling_id < 1:
        raise ParameterError("ceiling_id должен быть целым числом >= 1")

    result = HistoryScanResult(peer_id=peer_id, range=ScanRange(1, ceiling_id), corpus=[])
    offset_id = ceiling_id + 1
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
            for message in page:
                mid = getattr(message, "id", None)
                if mid is None:
                    raise HistoryScanError("сообщение без id в странице")
                mid = int(mid)
                if mid >= offset_id:
                    raise HistoryScanError(
                        f"страница не продвинулась: id {mid} >= offset {offset_id}")
                if mid in seen:
                    raise HistoryScanError(f"дублированный id {mid}")
                seen.add(mid)
                row = _eligible_corpus_row(message)
                if row is not None:
                    result.corpus.append(row)
                    if len(result.corpus) == eligible_limit:
                        result.complete = True
                        result.reason = "eligible_limit_reached"
                        break
            if result.complete:
                break
            offset_id = min(int(getattr(m, "id")) for m in page)
        else:
            result.reason = "page_budget_exhausted"
            result.corpus = None
            return result
    except asyncio.CancelledError:
        result.corpus = None
        raise
    except HistoryScanError as exc:
        log.warning("history acquisition [%s] failed honest pagination: %s", peer_id, exc)
        result.reason = "pagination_violation"
        result.corpus = None
        return result
    except Exception as exc:
        log.warning("history acquisition [%s] transport failure: %s", peer_id, type(exc).__name__)
        result.reason = f"rpc_error:{type(exc).__name__}"
        result.corpus = None
        return result

    result.messages_seen = len(seen)
    result.observed_ids = seen
    if result.corpus:
        result.range = ScanRange(min(int(row["message_id"]) for row in result.corpus), ceiling_id)
    return result


def _canonical_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _canonical_message(message: Any) -> dict[str, Any]:
    reply = getattr(message, "reply_to", None)
    reply_id = getattr(reply, "reply_to_msg_id", None) if reply is not None else None
    text = getattr(message, "message", None)
    if text is None:
        text = getattr(message, "raw_text", None)
    return {
        "message_id": int(getattr(message, "id")),
        "date": _canonical_date(getattr(message, "date", None)),
        "text": text if isinstance(text, str) else ("" if text is None else str(text)),
        "reply_to_message_id": int(reply_id) if reply_id is not None else None,
        "topic_id": message_topic_id(message),
    }


def _canonical_jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    lines = []
    for row in sorted(records, key=lambda item: int(item["message_id"])):
        exact = {field: row.get(field) for field in FREEZE_FIELDS}
        lines.append(json.dumps(exact, ensure_ascii=False, separators=(",", ":")))
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _open_private_directory(path: Path) -> tuple[Path, int]:
    """Create/open path without following symlinks in any component."""
    base = path.absolute()
    parts = base.parts
    if not parts or not base.is_absolute():
        raise HistoryScanError("private freeze root must be absolute")
    try:
        current_fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in parts[1:]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        os.fchmod(current_fd, 0o700)
        return base, current_fd
    except OSError as exc:
        try:
            os.close(current_fd)
        except (NameError, OSError):
            pass
        raise HistoryScanError(f"cannot secure private freeze root: {exc}") from exc


def freeze_corpus(
    result: HistoryScanResult,
    *,
    root: Path | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically persist exact corpus; return a privacy-minimal receipt."""
    if type(result) is not HistoryScanResult:
        raise HistoryScanError("freeze_corpus принимает только HistoryScanResult")
    if not result.complete:
        raise HistoryScanError(f"freeze requires complete scan: {result.reason}")
    if result.corpus is None:
        raise HistoryScanError("freeze requires an explicitly captured corpus")
    requested_base = Path(root) if root is not None else PRIVATE_FREEZE_ROOT
    payload = _canonical_jsonl(result.corpus)
    digest = hashlib.sha256(payload).hexdigest()
    base, dir_fd = _open_private_directory(requested_base)

    destination_name = f"{digest}.jsonl"
    destination = base / destination_name
    temporary_name = f".freeze-{secrets.token_hex(16)}"
    temporary_created = False
    try:
        fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        temporary_created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(
                temporary_name, destination_name,
                src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            try:
                existing_fd = os.open(
                    destination_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            except OSError as exc:
                raise HistoryScanError(
                    "existing freeze destination is not a safe regular file") from exc
            with os.fdopen(existing_fd, "rb") as existing:
                existing_stat = os.fstat(existing.fileno())
                if (not stat.S_ISREG(existing_stat.st_mode)
                        or existing_stat.st_nlink != 1):
                    raise HistoryScanError(
                        "existing freeze destination is not a private regular file")
                if existing.read() != payload:
                    raise HistoryScanError("content-addressed freeze artifact mismatch")
                os.fchmod(existing.fileno(), 0o600)
        else:
            os.unlink(temporary_name, dir_fd=dir_fd)
            temporary_created = False
            published_fd = os.open(
                destination_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                published_stat = os.fstat(published_fd)
                if (not stat.S_ISREG(published_stat.st_mode)
                        or published_stat.st_nlink != 1):
                    raise HistoryScanError(
                        "published freeze artifact is not a private regular file")
                os.fchmod(published_fd, 0o600)
            finally:
                os.close(published_fd)
        os.fsync(dir_fd)
    except OSError as exc:
        raise HistoryScanError(f"cannot publish private freeze artifact: {exc}") from exc
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        os.close(dir_fd)
    return {"frozen": True, "sha256": digest, "path": str(destination),
            "messages": len(result.corpus), "bytes": len(payload),
            "format": "canonical-jsonl-v1", "manifest": dict(manifest or {})}


def freeze_attested_corpus(
    discovery: HistoryScanResult,
    attestation: HistoryScanResult,
    *,
    root: Path | None = None,
    acquired_at: str | None = None,
) -> dict[str, Any]:
    """Freeze only a discovered corpus whose exact closed range was attested."""
    if type(discovery) is not HistoryScanResult or type(attestation) is not HistoryScanResult:
        raise HistoryScanError("attested freeze принимает только HistoryScanResult")
    if not discovery.complete or discovery.corpus is None:
        raise HistoryScanError(f"attested freeze requires complete discovery: {discovery.reason}")
    if not attestation.complete:
        raise HistoryScanError(f"attested freeze requires complete attestation: {attestation.reason}")
    if discovery.peer_id != attestation.peer_id or discovery.range != attestation.range:
        raise HistoryScanError("attestation does not match discovered corpus range")
    discovered_ids = {int(row["message_id"]) for row in discovery.corpus}
    if not discovered_ids <= attestation.observed_ids:
        raise HistoryScanError("attestation did not observe every discovered corpus row")
    manifest = {
        "peer": discovery.peer_id,
        **discovery.range.as_dict(),
        "row_count": len(discovery.corpus),
        "acquired_at": acquired_at,
        "history_scan_sha256": attestation.scan_sha256(),
    }
    receipt = freeze_corpus(discovery, root=root, manifest=manifest)
    manifest_root = Path(root).parent if root is not None else PRIVATE_MANIFEST_ROOT
    base, dir_fd = _open_private_directory(manifest_root)
    try:
        encoded = (json.dumps({**manifest, "corpus_sha256": receipt["sha256"]},
                              ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        # A mutable fixed name would let a later freeze erase the attestation
        # binding for an earlier corpus. Publish the manifest by its own bytes.
        filename = f"{hashlib.sha256(encoded).hexdigest()}.manifest.json"
        temporary = f".manifest-{secrets.token_hex(16)}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temporary, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
                        follow_symlinks=False)
            except FileExistsError:
                existing_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
                with os.fdopen(existing_fd, "rb") as existing:
                    existing_stat = os.fstat(existing.fileno())
                    if (not stat.S_ISREG(existing_stat.st_mode)
                            or existing_stat.st_nlink != 1 or existing.read() != encoded):
                        raise HistoryScanError("existing manifest is not the expected private artifact")
                    os.fchmod(existing.fileno(), 0o600)
            else:
                os.unlink(temporary, dir_fd=dir_fd)
            os.fsync(dir_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(dir_fd)
    receipt["manifest_path"] = str(base / filename)
    return receipt


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
