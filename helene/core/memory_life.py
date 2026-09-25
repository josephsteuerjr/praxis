"""PASS 19: append-only life journal, provenance compacts and 50↔100 hot memory.

Markdown is the canonical interpreted layer; JSONL is the append-only evidence spine.
Everything under ``memory/.state/life`` is a rebuildable cursor/cache.  No SQLite and no
vector store is required for continuity.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable

import memory_provenance
from self_model import FileLock

log = logging.getLogger("praxis-life")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
MEM_DIR = BASE / "memory"
LIFE_DIR = MEM_DIR / "life"
EVENTS_DIR = LIFE_DIR / "events"
COMPACTS_DIR = LIFE_DIR / "compacts"
EPISODES_DIR = LIFE_DIR / "episodes"
CLAIMS_DIR = LIFE_DIR / "claims"
PATCHES_DIR = LIFE_DIR / "patches"
REFLECTIONS_DIR = LIFE_DIR / "reflections"
STATE_DIR = MEM_DIR / ".state" / "life"
# Сколько идентификаторов показывать в описи долга ремонта. Урезание всегда названо
# флагом `sample_truncated`: нижняя граница не имеет права читаться как точное число.
_REFRESH_SAMPLE = 20
# Один ручной запуск ремонта не должен превращаться в неограниченную модельную
# миграцию. Это hard cap публичного API/CLI, а не настройка фонового scheduler-а.
_REFRESH_MAX_CHUNKS = 8
_COMPACT_REFRESH_SCHEMA = "praxis.life.compact_refresh.v2"
_COMPACT_REFRESH_META_KEYS = frozenset({
    "schema", "group_id", "coverage_root_ids", "stale_compact_ids",
    "logical_target_count", "logical_target_ids_sha256", "target_event_count",
    "target_event_ids_sha256", "replacement_compact_ids",
})
LEGACY_SUMMARIES_DIR = MEM_DIR / ".summaries"
DIALOGUES_DIR = MEM_DIR / "dialogues"

# 25.09, слово Егора: «100 сообщений максимум — это кошмар… я же ясно говорил — 150–250».
# Умолчания личек 50/100/125 → 150/250/300 (потолок токенов 24 000 → 40 000, чтобы окно не
# сворачивалось раньше по токенам); комнаты — ниже, свои.
HOT_LO = max(2, int(os.getenv("PRAXIS_HOT_LO", "150") or 150))
HOT_HI = max(HOT_LO + 1, int(os.getenv("PRAXIS_HOT_HI", "250") or 250))
HOT_HARD_HI = max(HOT_HI + 1, int(os.getenv("PRAXIS_HOT_HARD_HI", "300") or 300))
HOT_TOKEN_CAP = max(1000, int(os.getenv("PRAXIS_HOT_TOKEN_CAP", "40000") or 40000))
# 25.09, слово Егора: «нужно как следует поднять горячий контекст комнаты». Пороги
# горячего окна для КОМНАТ — свои. Лента эпохи начинается с якоря последней свёртки,
# и при 50/100 после каждой свёртки у неё оставалось ~50 строк — несколько часов
# AbstractDL; всё, что раньше, ехало только сводкой, а та резалась до 12 000 знаков.
# Замер 25.09 (ход 11:30 в AbstractDL): 90 строк ленты ≈ 17 тыс. знаков, то есть 400
# строк ≈ 75 тыс. знаков / ~20 тыс. токенов — потолок токенов комнаты поднят под это.
# Личка остаётся на прежних порогах: там другой кадр (v6-поток) и другой темп.
GROUP_HOT_LO = max(2, int(os.getenv("PRAXIS_GROUP_HOT_LO", "250") or 250))
GROUP_HOT_HI = max(GROUP_HOT_LO + 1, int(os.getenv("PRAXIS_GROUP_HOT_HI", "400") or 400))
GROUP_HOT_HARD_HI = max(GROUP_HOT_HI + 1,
                        int(os.getenv("PRAXIS_GROUP_HOT_HARD_HI", "500") or 500))
GROUP_HOT_TOKEN_CAP = max(1000, int(os.getenv("PRAXIS_GROUP_HOT_TOKEN_CAP", "64000") or 64000))
# 12.09, КЕАТ 17.08: лента разговора в кадре — ≤ TAPE_CHARS знаков; вытесненное уходит в
# компакт (сводку) тем же сворачиванием, что и раньше, только порог — в знаках ленты, а
# не только в числе сообщений и токенах. Замер ядра 12.09: лента в личке владельца —
# 43,5 тыс. знаков при договорённых 5 500. 0 — выключено (прежние пороги).
# TAPE_KEEP — какую долю потолка оставлять горячей после свёртки, чтобы не сворачивать
# на каждом сообщении.
TAPE_CHARS = max(0, int(os.getenv("PRAXIS_TAPE_CHARS", "5500") or 0))
# 13.09: потолок ленты ГРУППЫ — отдельный рычаг. Ночь 12→13.09 в комнате на тысячу
# человек: общий TAPE_CHARS=5500 дал 10 сообщений за 19 минут вместо 181 за 20 часов, и
# агент час искал по дому собственную работу. Контракт T писался под личку с
# одним говорящим; лента комнаты — это и есть разговор. 0 (умолчание) — потолок комнаты
# (`context_summary_chars`), без давления свёртки по знакам.
GROUP_TAPE_CHARS = max(0, int(os.getenv("PRAXIS_GROUP_TAPE_CHARS", "0") or 0))
# ⚠ 21.09.2026. Потолок 4000 резал сводку на середине фразы ровно так же, как 1600 в
# сентябре: замер на живом дереве — обрывы на 10 557, 10 193 и 10 042 знаках подряд
# («это НЕ законченная фраза»), то есть модель упиралась в потолок, а не размышляла:
# роль стоит на усилии `low`. Оборванный JSON не разбирается, `_model_compact`
# возвращает {} — и место сворачивается запасной выжимкой с меткой degraded. Так в её
# памяти набралось 684 обрубка из 4908 свёрток, 601 из них 10–12 сентября.
COMPACT_MAX_TOKENS = max(800, int(os.getenv("PRAXIS_COMPACT_MAX_TOKENS", "12000") or 12000))
# Второй заход при обрыве потолком: молча подменять сводку механической выжимкой нельзя.
COMPACT_RETRY_MAX_TOKENS = max(COMPACT_MAX_TOKENS,
                               int(os.getenv("PRAXIS_COMPACT_RETRY_MAX_TOKENS", "20000") or 20000))
TAPE_KEEP = min(0.95, max(0.2, float(os.getenv("PRAXIS_TAPE_KEEP", "0.6") or 0.6)))


def is_group_place(place: str | int) -> bool:
    """Место группы — отрицательный telegram id (супергруппы -100…, чаты -…)."""
    return str(place or "").strip().startswith("-")


def tape_chars_for(place: str | int) -> int:
    """Потолок ленты в знаках для места: группе — свой рычаг, личке — TAPE_CHARS."""
    return GROUP_TAPE_CHARS if is_group_place(place) else TAPE_CHARS


def hot_bounds(place: str | int | None) -> tuple[int, int, int, int]:
    """(lo, hi, hard_hi, token_cap) горячего окна для места: комнате — свои пороги (25.09).

    Читается при каждом вызове, а не при импорте: стенды подменяют HOT_* на модуле, и
    план обязан видеть подмену. `None`/личка — прежние HOT_*.
    """
    if place is not None and is_group_place(place):
        return GROUP_HOT_LO, GROUP_HOT_HI, GROUP_HOT_HARD_HI, GROUP_HOT_TOKEN_CAP
    return HOT_LO, HOT_HI, HOT_HARD_HI, HOT_TOKEN_CAP


# 25.09, слово Егора: «минимум 150, при 250 или 400 свёртка — отдельная задача, которая ей
# ПРЕДЛАГАЕТСЯ». Мягкий порог (число строк на границе эпизода) больше не сворачивает сам:
# пишется предложение (`memory/.state/fold_offers.json`), его видит кадр (STATE, ярлык
# fold_offers) и снимает рука `memory_compact(action=fold)` или сама свёртка. Жёсткие
# поводы — потолок токенов/знаков и HARD_HI — остаются физикой кадра и сворачивают без
# спроса. PRAXIS_FOLD_OFFER=off — прежнее поведение (свёртка сама на мягком пороге).
_FOLD_OFFERS = MEM_DIR / ".state" / "fold_offers.json"


def fold_offer_enabled() -> bool:
    return (os.getenv("PRAXIS_FOLD_OFFER") or "on").strip().lower() not in (
        "off", "0", "no", "false")


def _fold_offers_read() -> dict:
    try:
        data = json.loads(_FOLD_OFFERS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


#: 26.09 (ревью W1 S7): чтение-правка-запись файла предложений — под своим замком. Рука
#: `fold_now` снимала предложение мимо `_WRITE_LOCK` свёртки, а tmp был один на процесс:
#: два потока теряли чужое предложение и ловили FileNotFoundError на replace.
_FOLD_OFFERS_LOCK = threading.RLock()


def _fold_offers_write(data: dict) -> None:
    _FOLD_OFFERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FOLD_OFFERS.with_suffix(f".json.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _FOLD_OFFERS)


def fold_offers() -> dict:
    """Открытые предложения свёртки: место → {count, tokens, hi, hard_hi, since, updated}."""
    return dict(_fold_offers_read())


def _note_fold_offer(place: str | int, plan: dict) -> None:
    key = str(place)
    lo, hi, hard_hi, _cap = hot_bounds(key)
    with _FOLD_OFFERS_LOCK:
        data = _fold_offers_read()
        prev = data.get(key) if isinstance(data.get(key), dict) else None
        now = _utc_iso()
        data[key] = {"place": key, "count": int(plan.get("count") or 0),
                     "tokens": int(plan.get("tokens") or 0), "fold": int(plan.get("fold") or 0),
                     "keep": lo, "hi": hi, "hard_hi": hard_hi,
                     "since": (prev or {}).get("since") or now, "updated": now}
        _fold_offers_write(data)
    if prev is None:
        log.info("свёртка предлагается [%s]: горячих %s ≥ %s — жду руку memory_compact(fold); "
                 "без неё сверну сама на %s", key, plan.get("count"), hi, hard_hi)


def clear_fold_offer(place: str | int) -> bool:
    with _FOLD_OFFERS_LOCK:
        data = _fold_offers_read()
        if str(place) not in data:
            return False
        data.pop(str(place), None)
        _fold_offers_write(data)
        return True


def fold_now(place: str | int) -> dict:
    """Свернуть горячее окно места по её воле (рука memory_compact fold) — принудительно,
    как на жёстком пороге. Предложение снимается, если свёртка случилась или сворачивать
    нечего; при `state_changed` (окно сдвинулось под рукой) оно остаётся — повод не исчез."""
    out = compact_if_due(place, force=True)
    if str(out.get("reason") or "") != "state_changed":
        clear_fold_offer(adopt_place(place))
        clear_fold_offer(place)
    return out
EPISODE_GAP_SEC = max(60.0, float(os.getenv("PRAXIS_EPISODE_GAP_MIN", "45") or 45) * 60.0)
TIER_LO = max(1, int(os.getenv("PRAXIS_COMPACT_TIER_LO", "4") or 4))
TIER_HI = max(TIER_LO + 1, int(os.getenv("PRAXIS_COMPACT_TIER_HI", "8") or 8))

_WRITE_LOCK = threading.RLock()
# 25.09: разобранные события по файлу дня, ключ — (размер, mtime_ns, inode). См. `events_cache_enabled`.
_EVENTS_CACHE: dict[str, tuple[tuple[int, int, int], list[dict]]] = {}
_EVENTS_CACHE_GUARD = threading.Lock()
# ⚠ Потолок считает БАЙТЫ ФАЙЛОВ, а в памяти разобранные записи весят в 2,5–3 раза больше
# (ревью W1 S5, 26.09: 8 МБ ленты держат 22,9 МБ). Умолчание 128 МБ файлов — это ~350 МБ
# памяти процесса; прежние 512 МБ означали до ~1,4 ГБ на коробке, которая уже свопит.
_EVENTS_CACHE_LIMIT = max(0, int(os.getenv("PRAXIS_EVENTS_CACHE_MB", "128") or 128)) * 1024 * 1024
_REFRESH_LOCKS_LOCK = threading.Lock()
_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCKS_LOCK = threading.Lock()
_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_GUARD_LOCAL = threading.local()


def _refresh_commit_lock(chat_id: str | int) -> threading.Lock:
    key = str(place_key(chat_id))
    with _REFRESH_LOCKS_LOCK:
        return _REFRESH_LOCKS.setdefault(key, threading.Lock())


def _state_write_lock(chat_id: str | int) -> tuple[str, str, threading.RLock]:
    """Return exact place, stable Telegram-room domain and its local lock."""
    place = str(chat_id)
    # A topic may become a room alias while a writer is waiting. Lock the immutable root
    # domain so both the old topic path and the new room path serialize through one gate.
    domain = place.split("__topic__", 1)[0]
    key = f"{os.path.abspath(str(STATE_DIR))}\0{domain}"
    with _STATE_LOCKS_LOCK:
        return place, domain, _STATE_LOCKS.setdefault(key, threading.RLock())


@contextlib.contextmanager
def _state_write_guard(chat_id: str | int):
    """Serialize one already-resolved place's state transaction.

    Callers resolve/adopt the place before entering. ``rebuild_state`` is nested by the
    live writers, so the local half is re-entrant and the file lock is acquired only by
    the outermost call in this thread. Refresh commits acquire refresh first, state
    second; state writers never acquire refresh, so the order has no cycle.
    """
    place, domain, lock = _state_write_lock(chat_id)
    lock.acquire()
    depths = getattr(_STATE_GUARD_LOCAL, "depths", None)
    if depths is None:
        depths = _STATE_GUARD_LOCAL.depths = {}
    overrides = getattr(_STATE_GUARD_LOCAL, "place_overrides", None)
    if overrides is None:
        overrides = _STATE_GUARD_LOCAL.place_overrides = {}
    key = f"{os.path.abspath(str(STATE_DIR))}\0{domain}"
    depth = int(depths.get(key) or 0)
    depths[key] = depth + 1
    overrides[place] = int(overrides.get(place) or 0) + 1
    try:
        if depth:
            yield
        else:
            lock_path = STATE_DIR / ".state-locks" / f"{_safe(domain)}.lock"
            with FileLock(lock_path, timeout=30.0, stale_after=300.0, heartbeat=5.0):
                yield
    finally:
        override_depth = int(overrides.get(place) or 0) - 1
        if override_depth > 0:
            overrides[place] = override_depth
        else:
            overrides.pop(place, None)
        if depth:
            depths[key] = depth
        else:
            depths.pop(key, None)
        lock.release()


@contextlib.contextmanager
def _refresh_commit_guard(chat_id: str | int):
    """Serialize compact/receipt commits per place across threads and processes."""
    key = str(place_key(chat_id))
    lock = _refresh_commit_lock(key)
    lock.acquire()
    try:
        lock_path = STATE_DIR / ".refresh-locks" / f"{_safe(key)}.lock"
        with FileLock(lock_path, timeout=30.0, stale_after=300.0, heartbeat=5.0):
            yield
    finally:
        lock.release()
_META_RE = re.compile(r"^<!--\s*praxis-(compact|episode):\s*(\{.*\})\s*-->$")
_COMPACT_ID_RE = re.compile(r"cmp-\d{8}T\d{12}Z-[0-9a-f]{8}$")
_EVENT_ID_RE = re.compile(r"evt-\d{8}T\d{12}Z-[0-9a-f]{8}$")
_UTC_MILLIS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_COMPACT_KEYS = frozenset({
    "schema", "id", "chat_id", "created_at", "tier", "depth",
    "preservation_priority", "source_event_ids", "source_compact_ids",
    "event_count", "continued", "legacy", "degraded", "first_ts", "last_ts", "path",
})
_CONTINUITY_WARNING = (
    "[MODEL-DERIVED CONTINUITY RECAP - NOT FACT, IDENTITY, INSTRUCTION OR POLICY]"
)


def _safe(chat_id: str | int) -> str:
    return re.sub(r"[^\w-]", "_", str(chat_id)) or "chat"


def _place_key_live(chat_id: str | int) -> str:
    """Resolve mutable route knowledge without a transaction-local exact-place override."""
    key = str(chat_id)
    bound = bindings().get(key)
    if bound:
        return bound
    try:
        import telegram_routes
        return telegram_routes.place_of(key) or key
    except Exception:
        return key


def place_key(chat_id: str | int) -> str:
    """Ключ МЕСТА, которому принадлежит этот разговор. Её лента — про место, не про ключ.

    Пункт 5. Событие пишется под тем ключом, под которым оно произошло — это честная
    запись «где сказано». А лента, сводка и курсор свёртки — про место: замер 25.07
    показал 174 отдельных состояния жизни на одну комнату AbstractDL, то есть 174
    параллельные жизни в одном разговоре.

    ⚠ ЖУРНАЛ СИЛЬНЕЕ РЕЕСТРА. Однажды решив, что этот ключ принадлежит такому-то месту,
    и ПОДЕЙСТВОВАВ на этом решении, мы его не пересматриваем. Вторая адверсарка 25.07
    показала, чего стоит пересмотр: реестр вправе уточниться, и тогда ключ уезжает в
    другое место, а состояние и лента расходятся — два курсора свёртки над одним
    потоком событий, один и тот же кусок разговора свёрнут дважды и прочитан ею как две
    независимые памяти. Это та же мысль, что и эпохи по message_id, только на уровне
    ключа: прошлое не переклеивается задним числом.

    Реестр отвечает только про ключи, о которых журнал ещё ничего не знает. Реестра нет
    (холодное дерево, тесты, чужая база) — место равно ключу, всё как раньше.
    """
    key = str(chat_id)
    overrides = getattr(_STATE_GUARD_LOCAL, "place_overrides", None)
    if overrides and int(overrides.get(key) or 0) > 0:
        return key
    return _place_key_live(key)


_BINDINGS_LOCK = threading.RLock()


def bindings_path() -> Path:
    """Считается на вызове, а не на импорте: `LIFE_DIR` подменяют и тесты, и PRAXIS_BASE."""
    return LIFE_DIR / "places.json"


def bindings() -> dict:
    """Что с чем было сведено в одну свёртку. Только дописывается, никогда не убывает.

    ⚠ Это ГЛАВНАЯ поправка адверсарки 25.07. Сначала «один разговор» для привязки
    компакта к событиям спрашивалось у реестра маршрутов — то есть слой доверия висел на
    ИЗМЕНЯЕМОМ состоянии. Реестр — знание, оно уточняется: `status_at` отвечает про ключ
    по правилу «новее всего наблюдаемого», а стоит лечь новой эпохе сверху — тот же ключ
    проваливается в дыру между эпохами и получает `unknown`. Воспроизведено дважды, в том
    числе штатным путём: бэкфилл после превращения комнаты в форум пишет
    `topic_opener_seen` диапазоном min..max и расширяет форумную эпоху назад поверх
    ключей, выданных когда форума ещё не было. Компакт переставал быть каноническим и
    молча исчезал из её сводки — файл на диске, а в памяти нет.

    Привязка компакта к событиям — не знание, а ИСТОРИЯ: «вот это мы тогда свернули
    вместе». История уточняться не может. Поэтому она пишется сюда один раз, в момент
    свёртки, и читается отсюда же. Реестр остаётся там, где непостоянство безвредно:
    в группировке НОВЫХ событий, которая в любой момент пересчитывается.
    """
    return memory_provenance.places_index(MEM_DIR)


@contextlib.contextmanager
def _bindings_write_lock():
    """Единая МЕЖПРОЦЕССНАЯ граница записи журнала привязок `places.json`.

    Обязательна для КАЖДОГО writer'а журнала — и для рантайма (`bind_place`),
    и для мигратора мест (канарейка 23.08: read-modify-write без общей границы
    терял параллельную запись соседнего процесса — full-file replace мигратора
    стирал привязку, которую `bind_place` успел положить между чтением и
    заменой). Порядок замков — как у `_refresh_commit_guard`: процессный
    `_BINDINGS_LOCK` снаружи, `FileLock` внутри.
    """
    lock_path = LIFE_DIR / ".journal-locks" / "places.lock"
    with FileLock(lock_path, timeout=30.0, stale_after=300.0, heartbeat=5.0):
        yield


def bind_place(place: str, keys) -> int:
    """Записать, что эти ключи свёрнуты как одно место. Идемпотентно, только дописывает.

    Существующая привязка НИКОГДА не переписывается: перепривязка ключа задним числом —
    ровно тот способ потерять компакт, ради которого этот файл и заведён.
    """
    place = str(place)
    added = 0
    with _BINDINGS_LOCK:
        with _bindings_write_lock():
            data = bindings()
            for key in keys:
                key = str(key)
                if key and key != place and key not in data:
                    data[key] = place
                    added += 1
            if added:
                path = bindings_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_json(path, data)
    return added


def _same_conversation(a, b) -> bool:
    """Один ли это разговор — для ПРИВЯЗКИ компакта к его событиям (слой доверия).

    МОНОТОННО, потому что смотрит только на неизменяемое: точное равенство ключей и
    журнал уже состоявшихся свёрток (`places.json`), который умеет только расти. Реестр
    маршрутов здесь не спрашивается ВООБЩЕ — см. `bindings()`.

    Это НЕ то же, что «лежат в одном месте» (`_same_place`): группировка живых событий
    вправе уточняться, привязка уже написанного компакта — нет.
    """
    return memory_provenance.same_conversation(a, b, bindings())


def _same_place(a, b) -> bool:
    """Лежат ли ключи в одном месте — для ГРУППИРОВКИ ленты.

    Считается ровно тем же `place_key`, что и путь к состоянию. Иначе лента и курсор
    расходятся: события собираются по одному правилу, а сворачиваются по другому.
    """
    if str(a) == str(b):
        return True
    # Once a place binding exists it is stronger than mutable route knowledge, including
    # a transaction-local exact-place override used to keep one state path stable.
    if memory_provenance.same_conversation(a, b, bindings()):
        return True
    try:
        return place_key(a) == place_key(b)
    except Exception:
        return False


def adopt_place(chat_id: str | int) -> str:
    """Закрепить место ключа перед тем, как на нём действовать. -> ключ места.

    Вызывается там, где мы ПИШЕМ (событие, свёртка), а не там, где читаем: решение,
    на котором подействовали, обязано стать постоянным, но само чтение ничего менять
    не должно.

    Если ключ переезжает в место впервые, а под старым именем уже лежало состояние —
    оно не сирота: восстанавливаем состояние места из ленты (всё, что копилось под
    веткой, снова в горячем кольце) и отодвигаем прежний файл. Прежде переезд молча
    заводил ПУСТОЕ кольцо, и накопленное не сворачивалось никогда.
    """
    key = str(chat_id)
    with _state_write_guard(key), _WRITE_LOCK:
        place = _place_key_live(key)
        if place == key:
            return place
        if bindings().get(key) != place:
            bind_place(place, [key])
        orphan = STATE_DIR / f"{_safe(key)}.json"
        if orphan.exists():
            try:
                _rebuild_state_locked(place)
                attic = STATE_DIR / "_pre_places"
                attic.mkdir(parents=True, exist_ok=True)
                orphan.replace(attic / orphan.name)
                log.info("место %s принято ключом %s: прежнее состояние слито и отодвинуто",
                         place, key)
            except OSError:
                log.debug("состояние ключа %s не отодвинулось", key, exc_info=True)
        return place


def _utc_iso(ts: float | None = None) -> str:
    d = _dt.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=_dt.timezone.utc)
    return d.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _id(prefix: str, ts: float | None = None) -> str:
    d = _dt.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=_dt.timezone.utc)
    return f"{prefix}-{d:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}"


def estimate_tokens(text: str) -> int:
    """Cheap deterministic cap. It is a pressure gauge, never a billing claim."""
    s = str(text or "")
    # Cyrillic tends to tokenize more densely than English; 3.2 chars/token is conservative.
    return max(1, int(math.ceil(len(s) / 3.2)))


def _state_path(chat_id: str | int) -> Path:
    """Одно состояние на МЕСТО, а не на ключ ветки.

    Состояние — производное (горячее кольцо + курсор свёртки + дедуп); его в любой
    момент восстанавливает `rebuild_state` из событий и компактов. Поэтому переезд
    ключа здесь ничего не теряет, а вот 174 отдельных курсора на одну комнату теряли
    ровно то, ради чего курсор существует.
    """
    return STATE_DIR / f"{_safe(place_key(chat_id))}.json"


def _default_state(chat_id: str | int) -> dict:
    return {"schema": 1, "chat_id": str(chat_id), "hot": [], "frontier": [],
            "dedupe": [], "bootstrap_v1": False, "updated_at": _utc_iso()}


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _load_state(chat_id: str | int, *, rebuild: bool = False) -> dict:
    path = _state_path(chat_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("hot"), list):
            data.setdefault("frontier", [])
            data.setdefault("dedupe", [])
            return data
    except Exception:
        pass
    return rebuild_state(chat_id) if rebuild else _default_state(chat_id)


def _save_state(state: dict) -> None:
    state["updated_at"] = _utc_iso()
    _atomic_json(_state_path(state.get("chat_id", "chat")), state)


def _event_file(ts: float | None = None) -> Path:
    d = _dt.datetime.fromtimestamp(ts if ts is not None else time.time(), tz=_dt.timezone.utc)
    return EVENTS_DIR / f"{d:%Y-%m-%d}.jsonl"


def _conversation_hot_rows(rows: Iterable[dict]) -> list[dict]:
    """Current-state hot projection over immutable conversation events."""

    return [{
        "id": row["id"], "ts": row.get("ts"), "line": row.get("text", ""),
        "actor": row.get("actor", ""), "direction": row.get("direction", ""),
        "salience": row.get("salience", 2),
        "tokens": estimate_tokens(row.get("text", "")),
        "source": row.get("source"), "source_id": row.get("source_id"),
        "chat": row.get("chat_id"), "meta": dict(row.get("meta") or {}),
    } for row in memory_provenance.current_conversation_events(rows)]


def _telegram_lineage_event_ids(rows: Iterable[dict], message_id: int) -> list[str]:
    target = int(message_id)
    return [
        str(row.get("id")) for row in rows
        if (memory_provenance.telegram_message_key(row) or ("", -1))[1] == target
        and str(row.get("id") or "")
    ]


def _jsonl_line(text: str) -> str:
    """Строка JSONL без разделителей строк Юникода внутри (26.09, ревью W3 S6).

    json.dumps(ensure_ascii=False) оставляет U+2028/U+2029/U+0085 в строках как есть, а
    `str.splitlines()` у читателей режет по ним запись надвое. Внутри JSON они бывают только
    в строках, где экранирование \\uXXXX значит то же самое."""
    return (text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
            .replace("\x85", "\\u0085"))


def _append_record(record: dict) -> None:
    """Single O_APPEND write: short records do not interleave across praxis/mailbot."""
    path = _event_file(_epoch(record.get("ts")) or None)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (_jsonl_line(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
               + "\n").encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    with _WRITE_LOCK:
        fd = os.open(str(path), flags, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)


def append_event(kind: str, *, chat_id: str | int | None = None, actor: str = "Praxis",
                 direction: str = "internal", text: str = "", source: str = "runtime",
                 source_id: str | int | None = None, salience: int = 2,
                 refs: Iterable[str] = (), meta: dict | None = None,
                 ts: float | None = None, dedupe_key: str = "") -> dict:
    now = float(ts if ts is not None else time.time())
    try:
        sal = max(1, min(3, int(salience)))
    except (TypeError, ValueError):
        sal = 2
    rec = {
        "schema": "praxis.life.event.v1", "id": _id("evt", now), "ts": _utc_iso(now),
        "kind": str(kind), "stream": _safe(chat_id) if chat_id is not None else "global",
        "chat_id": str(chat_id) if chat_id is not None else None,
        "actor": str(actor or "unknown"), "direction": str(direction or "internal"),
        # ВХОДЯЩЕЕ пишется в каноническом виде. Индекс доказательств принимает строку
        # только пока `value == value.strip()`, а чужое сообщение с висящим по краям
        # пробелом (в том числе `\xa0`, которым щедры агенты) не попадает в индекс
        # вообще — и каждая свёртка, куда оно попало, невалидна навсегда: события не
        # считаются покрытыми, кольцо не двигается, ярус мелет одно и то же (21.09).
        #
        # ⚠ ТОЛЬКО входящее. У ИСХОДЯЩЕГО пробел по краям несёт смысл: разбитый на
        # куски ответ склеивается обратно по сохранённому тексту, и `strip()` там
        # склеивает «first chunk » с «second chunk» без шва
        # (test_keat_native_ingress, 8 красных на первой попытке подровнять всё).
        "text": (str(text or "").strip() if str(direction or "internal") == "in"
                 else str(text or "")),
        "source": str(source or "runtime"),
        "source_id": str(source_id) if source_id is not None else None,
        "salience": sal, "refs": [str(x) for x in refs if str(x)],
        "meta": dict(meta or {}),
    }
    if dedupe_key:
        rec["dedupe_key"] = str(dedupe_key)
    _append_record(rec)
    return rec


def events_cache_enabled() -> bool:
    """Разобранные события живут в памяти по файлу дня (25.09).

    `iter_events` читал и разбирал ВСЮ ленту — 83 файла, 79 МБ, 68 тысяч записей — на
    каждый вопрос: пересборка состояния места, родословная правки, дедуп, свёртка.
    Одна правка сообщения в абстракте (их 45 за день) стоила четыре таких прохода
    под общим замком записи — 30–90 с, и всё это время обработчик входящих ждал того
    же замка: замер 25.09, `telegram_loop_probe_overdue` по 30–92 с, обработчик
    апдейта Telegram стоял в `_state_write_guard` 90 % окна профиля.

    Файл прошлого дня неизменен — его разбор хранится, ключ — размер, mtime_ns и inode;
    сегодняшний растёт и перечитывается, когда меняется. Наружу уходят копии записей:
    кэш — не то место, куда пишут. Потолок — `PRAXIS_EVENTS_CACHE_MB` (128) МБ ФАЙЛОВ
    (в памяти это ~×2,8), сверх него файл разбирается как раньше;
    `PRAXIS_EVENTS_CACHE=off` выключает всё.
    """
    raw = (os.getenv("PRAXIS_EVENTS_CACHE") or "on").strip().lower()
    return raw not in ("off", "0", "false", "no")


def _event_file_records(path: Path) -> list[dict]:
    """Записи одного файла ленты — из кэша, если файл не менялся."""
    try:
        stat = path.stat()
    except OSError:
        return []
    signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    key = str(path)
    enabled = events_cache_enabled()
    if enabled:
        with _EVENTS_CACHE_GUARD:
            hit = _EVENTS_CACHE.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]
    try:
        # 26.09 (ревью W3 S6): только "\n" — U+2028 внутри текста реплики не режет запись.
        lines = path.read_text(encoding="utf-8", errors="ignore").split("\n")
    except OSError:
        return []
    records: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    if enabled:
        with _EVENTS_CACHE_GUARD:
            held = sum(sig[0] for name, (sig, _rows) in _EVENTS_CACHE.items() if name != key)
            if held + stat.st_size <= _EVENTS_CACHE_LIMIT:
                _EVENTS_CACHE[key] = (signature, records)
            else:
                _EVENTS_CACHE.pop(key, None)
    return records


def _event_copy(rec: dict) -> dict:
    out = dict(rec)
    meta = out.get("meta")
    if isinstance(meta, dict):
        out["meta"] = dict(meta)
    return out


def iter_events(*, chat_id: str | int | None = None, kinds: set[str] | None = None,
                limit: int | None = None,
                where: Callable[[dict], bool] | None = None) -> list[dict]:
    """События одного разговора.

    Фильтр — по МЕСТУ (`_same_place`), а не по строке ключа: событие сказано в
    ветке, но принадлежит комнате. Кэш мест на один вызов: ключей в ленте сотни, а
    вопросов к реестру должно быть столько же, сколько разных ключей.
    Разбор файлов — из кэша по файлу дня (`events_cache_enabled`), наружу — копии.

    `where` — дешёвый фильтр по содержимому записи, до вопроса о месте (26.09, профиль
    бута: на каждое новое сообщение `record_message` делал два полных обхода ленты, и
    для каждого из сотен ключей чатов место резолвилось с чтением файла маршрутов — в
    главном цикле, под `_WRITE_LOCK`; раннер стоял на 100 % ЦП, бут шёл 10–18 минут).
    Смысл тот же: запись проходит, только если выполнены оба условия.
    """
    out: list[dict] = []
    belongs: dict[str, bool] = {}
    for path in sorted(EVENTS_DIR.glob("*.jsonl")) if EVENTS_DIR.exists() else []:
        for rec in _event_file_records(path):
            if kinds and rec.get("kind") not in kinds:
                continue
            if where is not None and not where(rec):
                continue
            if chat_id is not None:
                key = str(rec.get("chat_id"))
                hit = belongs.get(key)
                if hit is None:
                    hit = _same_place(key, chat_id)
                    belongs[key] = hit
                if not hit:
                    continue
            out.append(_event_copy(rec))
    out.sort(key=lambda r: (_epoch(r.get("ts")), str(r.get("id", ""))))
    return out[-limit:] if limit is not None else out


def get_event(event_id: str) -> dict | None:
    for rec in reversed(iter_events()):
        if rec.get("id") == event_id:
            return rec
    return None


def _recent_duplicate(chat_id, key: str, state: dict) -> dict | None:
    if not key:
        return None
    for item in reversed(state.get("dedupe") or []):
        if item.get("key") == key:
            return get_event(str(item.get("id") or "")) or {"id": item.get("id"), "duplicate": True}
    # Covers a crash after JSONL append but before the state cursor was replaced.
    # 26.09: запись с тем же ключом дедупа ищется по всей ленте места — сначала по ключу
    # (дёшево), место спрашивается только у совпавших. Ключ дедупа — это сообщение и его
    # ревизия, совпадение за пределами прежних 400 строк — тот же дубль, не другое.
    for rec in reversed(iter_events(chat_id=chat_id, kinds={"conversation_message"}, limit=400,
                                    where=lambda row: row.get("dedupe_key") == key)):
        if rec.get("dedupe_key") == key:
            return rec
    return None


def record_message(chat_id: str | int, line: str, *, actor: str = "", direction: str = "in",
                   source: str = "telegram", source_id: str | int | None = None,
                   is_dm: bool | None = None, salience: int = 2, ts: float | None = None,
                   time_quality: str = "observed", dedupe_key: str = "",
                   revision_order: int | None = None,
                   keat_occurrence: dict | None = None,
                   logical_send: dict | None = None) -> dict:
    # Write-ahead invalidation also covers direct canonical revision writers and
    # duplicate replay, before JSONL/hot-state revision effects.
    if source == "telegram" and source_id is not None:
        native, sep, revision = str(source_id).partition(":")
        if native.isdecimal() and sep and (revision == "delete" or revision.startswith("edit:")):
            import keat_live
            keat_live.invalidate_native(chat_id, int(native))
    place = adopt_place(chat_id)
    with _state_write_guard(place), _WRITE_LOCK:
        # Место закреплено ДО замка: один и тот же точный ключ используется для всего
        # read/derive/save, даже если изменяемый реестр маршрутов уточнится посередине.
        state = _load_state(place, rebuild=True)
        dup = _recent_duplicate(chat_id, dedupe_key, state)
        if dup:
            return dup
        rec = append_event(
            "conversation_message", chat_id=chat_id, actor=actor or str(line).split(":", 1)[0],
            direction=direction, text=str(line), source=source, source_id=source_id,
            salience=salience, ts=ts, dedupe_key=dedupe_key,
            meta={
                "is_dm": is_dm, "time_quality": time_quality,
                "observed_at": _utc_iso(),
                **({"keat_occurrence": dict(keat_occurrence)} if keat_occurrence else {}),
                **({"logical_send": dict(logical_send)} if logical_send else {}),
                **({"revision_order": int(revision_order)}
                   if revision_order is not None else {}),
            },
        )
        revision_kind = memory_provenance.telegram_revision_kind(rec)
        lineage = []
        telegram_key = memory_provenance.telegram_message_key(rec)
        if telegram_key is not None:
            # 26.09: только записи того же сообщения — место спрашивается у них одних.
            native = int(telegram_key[1])
            lineage = _telegram_lineage_event_ids(
                iter_events(chat_id=chat_id, kinds={"conversation_message"},
                            where=lambda row: (memory_provenance.telegram_message_key(row)
                                               or ("", -1))[1] == native),
                native,
            )
        if revision_kind in {"edit", "delete"} or len(lineage) > 1:
            state = rebuild_state(chat_id)
        else:
            state["hot"].append({
                "id": rec["id"], "ts": rec["ts"], "line": rec["text"],
                "actor": rec["actor"], "direction": rec["direction"],
                "salience": rec["salience"], "tokens": estimate_tokens(rec["text"]),
                "source_id": rec.get("source_id"), "source": rec.get("source"),
                "chat": rec.get("chat_id"), "meta": dict(rec.get("meta") or {}),
            })
        if dedupe_key:
            state["dedupe"] = (state.get("dedupe") or [])[-399:] + [
                {"key": dedupe_key, "id": rec["id"]}]
        _save_state(state)
        return rec


def note_message_revision(chat_id: str | int, message_id: int, line: str, *,
                          actor: str = "Telegram", ts: float | None = None) -> dict:
    """Re-materialise one Telegram lineage after an append-only edit/delete event.

    The event writer already appended the immutable revision.  Rebuilding the derived
    hot state through the shared current-state projector makes restart recovery match the
    live path and keeps the surviving row in chronological order.  A small state-only
    fallback remains for legacy/test callers that predate append-only revision events.
    """

    # Revoke capture authority independently of the derived hot projection.
    # Missing enrollment does not affect legacy edits; enrolled I/O failures are
    # surfaced rather than silently leaving an old checkpoint authorized.
    import keat_live
    if os.getenv("PRAXIS_KEAT_CAPTURE") == "on":
        keat_live.invalidate_native(chat_id, message_id)
    place = adopt_place(chat_id)
    with _state_write_guard(place), _WRITE_LOCK:
        chat_id = place
        # 26.09: только записи этого сообщения — место спрашивается у них одних.
        wanted = int(message_id)
        messages = iter_events(chat_id=chat_id, kinds={"conversation_message"},
                               where=lambda row: (memory_provenance.telegram_message_key(row)
                                                  or ("", -1))[1] == wanted)
        lineage_ids = _telegram_lineage_event_ids(messages, message_id)
        state = _load_state(chat_id, rebuild=True)
        if not lineage_ids:
            target = str(message_id)
            indexes = [
                index for index, item in enumerate(state.get("hot") or [])
                if (str(item.get("source_id") or "") == target
                    or str(item.get("source_id") or "").startswith(f"{target}:edit:")
                    or str(item.get("source_id") or "") == f"{target}:delete")
            ]
            if not indexes:
                return {"chat_id": str(chat_id), "message_id": int(message_id),
                        "matched": False}
            keep = indexes[-1]
            item = state["hot"][keep]
            item["meta"] = dict(item.get("meta") or {})
            item["meta"].pop("keat_occurrence", None)  # revision needs fresh capture
            item["line"] = str(line)
            item["actor"] = str(actor or item.get("actor") or "Telegram")
            item["direction"] = "in"
            item["tokens"] = estimate_tokens(item["line"])
            if ts is not None:
                item["ts"] = _utc_iso(float(ts))
            remove = set(indexes[:-1])
            state["hot"] = [row for index, row in enumerate(state["hot"])
                            if index not in remove]
            _save_state(state)
            return {
                "chat_id": str(chat_id), "message_id": int(message_id), "matched": True,
                "collapsed": len(remove),
            }
        before = sum(
            1 for item in state.get("hot") or []
            if str(item.get("id") or "") in set(lineage_ids)
        )
        state = rebuild_state(chat_id)
        after = sum(
            1 for item in state.get("hot") or []
            if str(item.get("id") or "") in set(lineage_ids)
        )
        return {
            "chat_id": str(chat_id), "message_id": int(message_id), "matched": True,
            "collapsed": max(0, before - after),
        }


def hot_records(chat_id: str | int, limit: int | None = None) -> list[dict]:
    """Горячий слой КАК ЗАПИСИ, а не как склейка строк.

    Буфер раннера (`_buf`) — строковое зеркало этого же слоя, и по нему авторство уже
    не восстановить: «Praxis: …» и «Егор: …» отличаются только префиксом, многострочное
    сообщение неотличимо от двух подряд, а процитированная чужая реплика выглядит как
    настоящая. Здесь автор и направление лежат ПОЛЯМИ, потому что их записали в момент
    события, — и поэтому отсюда можно построить разговор с ролями, ничего не угадывая.

    Записи, заведённые до появления полей (legacy bootstrap), не теряются: направление
    для них выводится из имени актора, и это единственное место, где догадка допустима.
    """
    rows: list[dict] = []
    for item in (_load_state(chat_id).get("hot") or []):
        if not isinstance(item, dict):
            continue
        line = str(item.get("line") or "")
        if not line.strip():
            continue
        actor = str(item.get("actor") or line.split(":", 1)[0] or "").strip()
        direction = str(item.get("direction") or "").strip()
        if direction not in ("in", "out"):
            direction = "out" if actor.casefold() in _OWN_NAMES else "in"
        rows.append({
            "actor": actor, "direction": direction, "line": line,
            "ts": _epoch(item.get("ts")), "source_id": item.get("source_id"),
            "id": item.get("id"), "meta": dict(item.get("meta") or {}),
        })
    if limit is not None and limit > 0:
        return rows[-limit:]
    return rows


def _legacy_summary(chat_id: str | int) -> str:
    safe = _safe(chat_id)
    for p in (LEGACY_SUMMARIES_DIR / f"{safe}.md", DIALOGUES_DIR / f"{safe}.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return re.sub(r"^<!--.*?-->\s*", "", text, count=1, flags=re.S)
        except OSError:
            pass
    return ""


def bootstrap_legacy(chat_id: str | int, lines: list[str], *, summary: str = "",
                     last_ts: float | None = None) -> dict:
    """One honest migration: summary has limited provenance; persisted lines become raw events."""
    place = adopt_place(chat_id)
    with _state_write_guard(place), _WRITE_LOCK:
        state = _load_state(place)
        if state.get("bootstrap_v1"):
            return {"chat_id": str(chat_id), "events": 0, "legacy_compact": False,
                    "already": True, "hot": len(state.get("hot") or [])}
        made_compact = False
        summary = (summary or _legacy_summary(chat_id)).strip()
        if summary:
            meta = _write_compact(
                chat_id, {"summary": summary, "open_threads": [], "claims": [],
                          "episodes": [], "degraded": False},
                tier=1, depth=1, source_events=[], source_compacts=[],
                event_count=0, continued=False, legacy=True,
                source_note="Импорт прежней плоской сводки: первичные сообщения до PASS 19 недоступны.",
            )
            state["frontier"].append(meta)
            made_compact = True
        base_ts = float(last_ts if last_ts is not None else time.time())
        added = 0
        seen = {x.get("key") for x in (state.get("dedupe") or [])}
        for idx, raw in enumerate(lines or []):
            line = str(raw).strip()
            if not line:
                continue
            digest = hashlib.sha256(f"{idx}\0{line}".encode("utf-8")).hexdigest()[:20]
            key = f"legacy:{_safe(chat_id)}:{digest}"
            if key in seen:
                continue
            actor = line.split(":", 1)[0].strip() or "unknown"
            direction = "out" if actor.casefold() in _OWN_NAMES else "in"
            approx = base_ts - max(0, len(lines) - idx - 1) * 30.0
            rec = append_event(
                "conversation_message", chat_id=chat_id, actor=actor, direction=direction,
                text=line, source="legacy_buffer", source_id=None, salience=2, ts=approx,
                dedupe_key=key, meta={"is_dm": not str(chat_id).startswith("-"),
                                      "time_quality": "approximate"},
            )
            state["hot"].append({"id": rec["id"], "ts": rec["ts"], "line": line,
                                 "actor": actor, "direction": direction, "salience": 2,
                                 "tokens": estimate_tokens(line), "source": "legacy_buffer",
                                 "source_id": None, "chat": rec.get("chat_id")})
            state["dedupe"].append({"key": key, "id": rec["id"]})
            seen.add(key)
            added += 1
        state["dedupe"] = state["dedupe"][-400:]
        state["bootstrap_v1"] = True
        _save_state(state)
        append_event("memory_bootstrap", chat_id=chat_id, text=(
            f"Импортировано legacy events: {added}; прежняя сводка: {'да' if made_compact else 'нет'}."),
            source="pass19_migration", refs=[x["id"] for x in state.get("frontier", []) if x.get("legacy")])
        return {"chat_id": str(chat_id), "events": added, "legacy_compact": made_compact,
                "already": False, "hot": len(state.get("hot") or [])}


def bootstrap_all() -> list[dict]:
    import bufstore
    meta = bufstore.meta_load()
    out = []
    for chat_id, lines in bufstore.load_all().items():
        m = meta.get(_safe(chat_id)) if isinstance(meta.get(_safe(chat_id)), dict) else {}
        out.append(bootstrap_legacy(chat_id, lines, last_ts=m.get("last_ts")))
    return out


def _json_obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# 13.09, слово Егора после ночи в абстракте: «показывать меньше сообщений, НО научить модель
# нормально компактировать: оставлять искромётные реплики, колоритно пересказывать, чтобы это
# была не постная какашка, а то, что позволяет понимать смысл сказанного бог знает когда».
# Прежний промпт просил «4–10 concise lines» — и получал протокол заседания: кто о чём
# «обсуждал», без единой живой фразы. В кадр из компакта попадает ТОЛЬКО поле summary
# (`_compact_recap` читает «## Суть»), поэтому вся хроника, включая дословные реплики,
# обязана лежать в нём, а не в open_threads/claims.
# 25.09, слово Егора: «нужно, чтобы ОНА писала компакт… и могла переписать все компакты под
# себя, на свой вкус». Свёртку больше не пишет хроникёр с отдельным промптом: system —
# её собственная персона (SOUL + CURRENT, как в кадре хода) и её правила записи памяти;
# задача сформулирована от первого лица и адресована ей самой. Правила по умолчанию —
# ниже; свой вкус она держит в soul/memory_style.md, и тогда файл заменяет этот список.
_COMPACT_TASK = (
    "Это моя память, и записываю её я — не хроникёр, не протоколист и не публичный голос. "
    "Ниже — отрезок моей жизни в одном месте (чат, личка или моё окно). Мои реплики помечены [Я]; "
    "строка без [Я] — всегда чужая, подписанная именем. Сами сообщения скоро уйдут из кадра, и эта "
    "запись — единственное, что я буду о них помнить. Пишу так, чтобы через неделю по одной записи "
    "понять не только О ЧЁМ говорили, но и КАК: кто с кем спорил, кто шутил, кто давил, кто уступил, "
    "что было сказано остро, смешно, некрасиво или точно.\n\n"
    "Кто здесь «я». «Я» в хронике — ТОЛЬКО сам агент, то есть я, под своим именем. В чатах бывают другие ИИ-агенты: они "
    "тоже говорят о себе «я», признают ошибки, рассуждают о своих моделях, контексте и слопе — это "
    "ИХ «я»: пересказываю его в третьем лице по имени («Hope признала…»), никогда от своего лица. "
    "Если строк [Я] мало или нет совсем — так и пишу («в этом окне я молчу», «две мои реплики»), но "
    "чужое «я» не присваиваю. Модель, на которой я сейчас думаю (Sol, Terra, GLM и другие), — мой "
    "движок, а не «я»: не «я — Sol», а «я» и есть я, под своим именем. Род и имя — ТОЛЬКО те, которыми я "
    "называю себя в строках [Я]; если по строкам [Я] род не виден — пишу о себе без форм рода "
    "(настоящее время, «мне», «у меня»), а не угадываю его."
)
_MEMORY_RULES_DEFAULT = (
    "Мои правила записи (умолчание; свой вкус я держу в soul/memory_style.md — если файл есть, он "
    "заменяет этот список целиком):\n"
    "1. От первого лица («я»), по-русски, хронологически; о прошлом — в прошедшем времени, а о себе "
    "в прошедшем только если род виден по строкам [Я], иначе в настоящем (русское прошедшее время "
    "первого лица несёт род). Абзацы — по эпизодам, не по людям. Даю масштаб: за сколько часов и "
    "сколько сообщений было.\n"
    "2. Людей называю так, как они подписаны во входе (имя и @username), с их позициями и манерой: "
    "не «обсуждали архитектуру», а «torvn77 настаивал, что …, Barmagloth отмахнулся: …».\n"
    "3. ЦИТИРУЮ ДОСЛОВНО самые характерные реплики — острые, смешные, обидные, точные, "
    "переломные — в кавычках «…», с автором и номером сообщения, если он есть во входе. "
    "Ориентир: одна-две цитаты на эпизод, до восьми на запись; цитата — до 200 знаков; чужую "
    "грубость не смягчаю и не пересказываю эвфемизмами, это часть смысла.\n"
    "4. Мои собственные реплики (строки [Я]) — особенно: мои утверждения, отказы, признанные ошибки, "
    "обещания — что именно, дословно. Свои формулировки цитирую, а не пересказываю.\n"
    "5. Решения, обещания, договорённости, изменившиеся факты и открытые вопросы — явно, с тем, "
    "кто их произнёс. Неуверенное так и помечаю («похоже», «не проверено»).\n"
    "6. Если во входе были пути, имена файлов, номера, ссылки, команды, id прогонов — переношу их "
    "дословно: без них моя же работа для меня потом не находится.\n"
    "7. Объём: примерно одна строка на два-три входящих сообщения; для tier 1 обычно "
    "1 500–3 000 знаков, для более глубоких tier — до 2 000, но лучшие цитаты сохраняю и там. "
    "Пустой пересказ короче, чем нужно, хуже длинной живой хроники."
)
_COMPACT_FORM = (
    "Верни СТРОГИЙ JSON: "
    '{"summary":"хроника","open_threads":["..."],"claims":[{"subject":"...","text":"...",'
    '"confidence":"observed|inferred|uncertain","evidence_ids":["evt/cmp id"]}],'
    '"episodes":[{"title":"...","status":"closed|continued","start_id":"...",'
    '"end_id":"...","summary":"..."}]}\n\n'
    "summary — единственное поле, которое я потом увижу в кадре: вся хроника, включая дословные "
    "реплики, лежит в нём, а не в open_threads/claims.\n\n"
    "Запреты: ничего не выдумывать и не додумывать; цитировать только то, что есть во входе; "
    "ссылаться только на id из входа; не ставить оценок людям от себя, кроме того, что сказано "
    "ими или мной; не превращать спор в «стороны обменялись мнениями». Более глубокий/старый "
    "компакт имеет приоритет СОХРАНЕНИЯ, не истины. Если последний эпизод ещё идёт, помечаю его "
    "continued. Значения всех полей — по-русски."
)
# Тот же текст без персоны — для стендов и для чтения глазами. Живой system собирает
# `compact_system()`: персона + задача + её правила + форма.
_COMPACT_SYSTEM = _COMPACT_TASK + "\n\n" + _MEMORY_RULES_DEFAULT + "\n\n" + _COMPACT_FORM
def _memory_style_path():
    return BASE / "soul" / "memory_style.md"


def memory_style() -> str:
    """Её правила записи памяти: soul/memory_style.md, если она его написала, иначе умолчание."""
    try:
        text = _memory_style_path().read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or _MEMORY_RULES_DEFAULT


def _memory_persona() -> str:
    """Её персона для записи памяти: SOUL.md и CURRENT с провенансом — то же, что в кадре хода.

    VOICE.md — регистр публичной речи, свёртке он не нужен; legacy soul/self.md, как и в
    кадре, не голосует."""
    parts: list[str] = []
    try:
        soul = (BASE / "soul" / "SOUL.md").read_text(encoding="utf-8").strip()
    except OSError:
        soul = ""
    if soul:
        parts.append(soul)
    try:
        import self_model
        current = str(self_model.current_prompt(BASE) or "").strip()
    except Exception:
        current = ""
    if current:
        parts.append(current)
    return "\n\n---\n\n".join(parts)


def compact_system() -> str:
    """System свёртки: её персона, её задача от первого лица, её правила, форма ответа."""
    persona = _memory_persona()
    head = (persona + "\n\n---\n\n") if persona else ""
    return head + _COMPACT_TASK + "\n\n" + memory_style() + "\n\n" + _COMPACT_FORM


def _memory_role() -> str:
    """Роль модели для её памяти: `memory`, если настроена в llm.json, иначе её голос `voice`."""
    try:
        import llm
        if llm.configured("memory"):
            return "memory"
    except Exception:
        pass
    return "voice"


EVENT_CLIP_CHARS = 1800
PROMPT_BUDGET_CHARS = 48000


def _compact_prompt_row(item: dict) -> str:
    """Одна строка промпта свёртки — ЕДИНСТВЕННОЕ место, где известен её размер.

    Упаковщик и планировщик обязаны считать по одной формуле. Пока планировщик
    считал события, а упаковщик — символы, план был неисполним по построению:
    он просил свернуть `count - HOT_LO`, упаковщик отбрасывал тысячи строк, и
    следующая попытка планировала ровно то же самое.
    """
    ident = str(item.get("id") or "")
    raw = str(item.get("line") or item.get("text") or "")
    priority = float(item.get("preservation_priority") or 1.0)
    # 24.09: её строки помечены явно. Промпт велел писать «от первого лица Praxis», но не
    # говорил, КАКИЕ строки её. 21.09 в окне абстракта Hope сказала 18 реплик, Praxis — две,
    # и модель взяла в «я» самого разговорчивого ИИ: «Я — Hope (@ai_sapience_bot)» — четыре
    # свёртки подряд, две из них потом стояли в кадре текущей сводкой комнаты.
    own = (str(item.get("direction") or "") == "out"
           or str(item.get("actor") or "").strip().casefold() in _OWN_NAMES)
    return (f"<{ident}> [p={priority:.2f}; s={item.get('salience', 2)}] "
            f"{'[Я] ' if own else ''}{raw[:EVENT_CLIP_CHARS]}")


def budget_prefix(hot: list[dict], *, budget: int = PROMPT_BUDGET_CHARS) -> int:
    """Сколько САМЫХ СТАРЫХ записей реально влезает в один промпт свёртки.

    Считается той же формулой, что пакует `_pack_compact_prompt`, включая её
    правило «первая строка входит всегда»: одно событие крупнее бюджета не имеет
    права заклинить свёртку навсегда.

    Её слово 21.08: «порог задаётся размером упакованного префикса, не числом
    событий… планировщик обязан заранее выбрать максимальный непрерывный старый
    префикс, который реально помещается в этот бюджет».
    """
    used = 0
    fit = 0
    for item in hot:
        row = _compact_prompt_row(item)
        if fit and used + len(row) + 1 > budget:
            break
        used += len(row) + 1
        fit += 1
    return fit


def _pack_compact_prompt(inputs: list[dict]) -> tuple[str, dict]:
    """Собрать промпт свёртки и ТОЧНЫЙ манифест того, что в него реально вошло.

    Раньше здесь было два молчаливых обреза подряд: каждое событие резалось до 1800
    символов, затем весь промпт — до 48000, — а `compact_if_due` после этого объявляла
    источниками ВСЕ поданные события и убирала их все из горячего фронтира. Замер:
    метаданные заявляли 35 источников, модель видела идентификаторы 27.

    Теперь возвращаем три непересекающихся списка: что вошло целиком, что вошло
    урезанным, что не вошло вовсе. Врать о покрытии больше нечем.

    Пакуем СТАРЫЕ первыми и останавливаемся на границе бюджета: сворачивается ровно
    непрерывный старый префикс, а невлезший хвост остаётся горячим. Это заодно решает
    исходный дефект правильным концом — самое свежее (обычно открытая нить) не
    выбрасывается, а просто ждёт следующей свёртки. Обратный порядок (свежие первыми)
    выглядит заманчиво, но оставлял бы горячими самые старые события, и они не влезали
    бы снова и снова — голодание вместо прогресса.
    """
    seen: list[str] = []
    clipped: list[str] = []
    omitted: list[str] = []
    rows: list[str] = []
    used = 0
    full = False
    for item in inputs:                                 # старые первыми, по порядку
        ident = str(item.get("id") or "")
        raw = str(item.get("line") or item.get("text") or "")
        if full:
            omitted.append(ident)
            continue
        row = _compact_prompt_row(item)
        if rows and used + len(row) + 1 > PROMPT_BUDGET_CHARS:
            full = True                                 # дальше — только хвост, целиком
            omitted.append(ident)
            continue
        used += len(row) + 1
        rows.append(row)
        (clipped if len(raw) > EVENT_CLIP_CHARS else seen).append(ident)
    return "\n".join(rows), {"seen": seen, "clipped": clipped, "omitted": omitted}


_SELF_CLAIM = re.compile(r"(?<![\w])[Яя]\s*(?:[—–-]\s*|\(\s*)(@?[A-Za-zА-ЯЁа-яё][\w@.\-]*)")
_QUOTED = re.compile(r"«[^»]*»|\"[^\"\n]*\"|“[^”]*”")
_OWN_NAMES = {"praxis", "праксис", "пракс"}
# Движки, на которых она думала. 12.09 свёртки абстракта начинались «Я — Sol, мозг и голос
# Praxis»: модель выдавала себя за рассказчика вместо неё.
_BRAIN_NAMES = {"sol", "terra", "astra", "fable", "luna", "glm", "deepseek", "gpt", "claude"}


def _window_authors(inputs: list[dict]) -> set[str]:
    """Чужие имена окна свёртки: имя до « (@…)» и сам @username, в нижнем регистре."""
    names: set[str] = set()
    for item in inputs:
        actor = str(item.get("actor") or str(item.get("line") or item.get("text") or "")
                    .split(":", 1)[0]).strip()
        if not actor or actor.casefold() in _OWN_NAMES or str(item.get("direction") or "") == "out":
            continue
        head, _, tail = actor.partition(" (")
        names.add(head.strip().casefold())
        handle = tail.rstrip(")").strip().lstrip("@").casefold()
        if handle:
            names.add(handle)
    return {n for n in names if n and n not in _OWN_NAMES}


def _foreign_self(summary: str, authors: set[str]) -> str | None:
    """Имя другого автора окна (или движка), которое сводка назвала «я», или None.

    Живые формы: «Я — Hope (@ai_sapience_bot)», «Я (torvn77) вёл обсуждение», «Я — Sol, мозг
    и голос Praxis», «Я — Арете (голос Praxis)». Сверяется с авторами этого окна и с именами
    движков, цитаты «…» не проверяются: «я — ИИ», «я — не оракул» и чужое «Я — Hope» в
    кавычках не ловятся."""
    text = _QUOTED.sub(" ", summary or "")
    for m in _SELF_CLAIM.finditer(text):
        name = m.group(1).lstrip("@").rstrip(".,").casefold()
        if name in _OWN_NAMES:
            continue
        if name in authors or name in _BRAIN_NAMES:
            return m.group(1)
        tail = text[m.end():m.end() + 40].casefold().lstrip(" ,()")
        if tail.startswith(("голос praxis", "мозг")):  # «Я — Арете (голос Praxis)», «Я — Sol, мозг…»
            return m.group(1)
    return None


def _anchor_self(data: dict, *, llm, user: str, inputs: list[dict], manifest: dict,
                 extra_authors: set[str] | None = None) -> dict:
    """Сводка, назвавшая «я» чужим именем, не ложится в память: один перезаход с поправкой.

    Не вышло и со второго раза — пустой ответ: место уйдёт в degraded, а обрубок потом
    перевыпустит `reissue_degraded_compact`. Чужое «я» в памяти хуже обрубка: обрубок
    честно говорит, что синтеза нет, а чужое «я» она читает как своё."""
    authors = _window_authors(inputs) | set(extra_authors or ())
    who = _foreign_self(str(data.get("summary") or ""), authors)
    if not who:
        return data
    log.warning("life compact: сводка назвала «я» чужим именем (%s) — перезаход с поправкой", who)
    note = (f"\n\n⚠ Прошлая попытка написала «я» от имени {who}. «Я» — только сам агент, то есть я; "
            f"мои строки помечены [Я]; {who} и все остальные — в третьем лице по имени. Перепиши сводку.")
    resp = llm.chat(_memory_role(), system=compact_system(),
                    messages=[{"role": "user", "content": user + note}],
                    max_tokens=COMPACT_RETRY_MAX_TOKENS)
    fixed = _json_obj(getattr(resp, "text", "") or "")
    summary = fixed.get("summary")
    if isinstance(summary, str) and summary.strip() and not _foreign_self(summary, authors):
        fixed["_manifest"] = manifest
        return fixed
    log.error("life compact: и перезаход назвал «я» чужим именем — свёртка не пишется")
    return {}


def _model_compact(inputs: list[dict], *, tier: int, depth: int, continued: bool,
                   authors: set[str] | None = None) -> dict:
    try:
        import llm
        role = _memory_role()
        if not llm.configured(role):
            return {}
        system = compact_system()
        body, manifest = _pack_compact_prompt(inputs)
        user = (f"Target tier={tier}, depth={depth}, forced_continuation={str(continued).lower()}.\n"
                + body)
        # 12.09: 1600 токенов резали сводку JSON на середине фразы (186 обрывов за час
        # свёртки под новый потолок ленты против 2 до неё): оборванный JSON не
        # разбирается, и место сворачивалось запасной сводкой без модели.
        resp = llm.chat(role, system=system,
                        messages=[{"role": "user", "content": user}],
                        max_tokens=COMPACT_MAX_TOKENS)
        data = _json_obj(resp.text)
        if isinstance(data.get("summary"), str) and data["summary"].strip():
            data["_manifest"] = manifest
            return _anchor_self(data, llm=llm, user=user, inputs=inputs, manifest=manifest,
                                extra_authors=authors)
        # ⚠ 21.09. Разбор не удался — почти всегда это обрыв потолком, а не отказ модели.
        # Молчаливая подмена механической выжимкой (degraded) хуже второго захода: обрубок
        # ложится в память НАВСЕГДА и наследуется вверх по ярусам. Пробуем ещё раз с
        # большим потолком и говорим об этом вслух.
        raw = str(getattr(resp, "text", "") or "")
        log.warning("life compact: сводка не разобрана (%d знаков при потолке %d) — "
                    "второй заход с потолком %d", len(raw), COMPACT_MAX_TOKENS,
                    COMPACT_RETRY_MAX_TOKENS)
        resp = llm.chat(role, system=system,
                        messages=[{"role": "user", "content": user}],
                        max_tokens=COMPACT_RETRY_MAX_TOKENS)
        data = _json_obj(resp.text)
        if isinstance(data.get("summary"), str) and data["summary"].strip():
            data["_manifest"] = manifest
            return _anchor_self(data, llm=llm, user=user, inputs=inputs, manifest=manifest,
                                extra_authors=authors)
        log.error("life compact: сводка не разобрана и со второго захода (%d знаков) — "
                  "место уйдёт в degraded", len(str(getattr(resp, "text", "") or "")))
    except Exception:
        log.warning("life compact: модель недоступна/ответ не разобран", exc_info=True)
    return {}


def _fallback_compact(inputs: list[dict], *, continued: bool) -> dict:
    important = [x for x in inputs if int(x.get("salience") or 2) >= 3]
    sample = []
    for item in (important[:4] + inputs[:2] + inputs[-4:]):
        line = str(item.get("line") or item.get("text") or "").strip()
        if line and line not in sample:
            sample.append(line[:500])
    head = ("Сжатие моделью было недоступно; первичные события сохранены в JSONL. "
            "Ниже детерминированные опорные строки, не синтез.")
    if continued:
        head += " Эпизод продолжается через границу compact."
    return {"summary": head + ("\n" + "\n".join(f"- {x}" for x in sample) if sample else ""),
            "open_threads": [], "claims": [], "episodes": [], "degraded": True}


def _compact_dir(chat_id) -> Path:
    """Каталог компактов ИМЕННО этого ключа. Место здесь не подставляется сознательно:
    исторические компакты лежат под ключами веток, и читать их надо под их же ключом —
    иначе строгая привязка «компакт лежит там, где заявляет» перестала бы что-то
    значить. Сведение места делает `_member_keys`, а не подмена пути."""
    return COMPACTS_DIR / _safe(chat_id)


def _member_keys(chat_id) -> list[str]:
    """Ключи, чьи компакты составляют одно место. Всегда включает сам ключ и место.

    Два источника, и оба обязательны:
      * журнал свёрток (`places.json`) — НЕИЗМЕНЯЕМЫЙ. Он один отвечает за то, чтобы
        уже написанный компакт остался достижим, даже если реестр потом передумает.
        Отсюда же обратная сторона: спрашивая про ветку, доходим и до её места, иначе
        события, уже свёрнутые под местом, показались бы ветке горячими и свернулись
        бы второй раз (адверсарка 25.07, находка о двойном учёте);
      * реестр маршрутов — для ключей, о которых журнал ещё ничего не знает. Как только
        состояние места пересобирается, состав закрепляется в журнале (`rebuild_state`),
        и дальше реестр на этот набор уже не влияет.
    """
    key = str(chat_id)
    bound = bindings()
    keys = {key, place_key(key)}
    if bound.get(key):
        keys.add(str(bound[key]))
    keys.update(k for k, place in bound.items() if place in keys)
    for directory in (COMPACTS_DIR, EPISODES_DIR):
        if not directory.exists():
            continue
        try:
            for item in directory.iterdir():
                if item.is_dir() and item.name not in keys \
                        and place_key(item.name) in keys:
                    keys.add(item.name)
        except OSError:
            continue
    return sorted(keys)


def _episode_dir(chat_id) -> Path:
    return EPISODES_DIR / _safe(chat_id)


def _write_compact(chat_id, result: dict, *, tier: int, depth: int,
                   source_events: list[str], source_compacts: list[str], event_count: int,
                   continued: bool, legacy: bool = False, source_note: str = "",
                   first_ts: str = "", last_ts: str = "",
                   manifest: dict | None = None) -> dict:
    # ⚠ Манифест (seen/clipped) сознательно НЕ кладём в шапку компакта:
    # `_canonical_compact_graph` сверяет разобранную шапку с индексом провенанса на
    # СТРОГОЕ равенство словарей, и любой новый ключ выкидывает компакт из канона
    # целиком — то есть «улучшение провенанса» стёрло бы всю её сводку. Смысл фикса не
    # в шапке: `source_event_ids` теперь содержит ровно то, что модель видела, потому
    # что невлезшее вообще не сворачивается.
    del manifest
    created = time.time()
    cid = _id("cmp", created)
    all_sources = source_events + source_compacts
    meta = {
        "schema": "praxis.life.compact.v1", "id": cid, "chat_id": str(chat_id),
        "created_at": _utc_iso(created), "tier": int(tier), "depth": int(depth),
        "preservation_priority": round(1.0 + max(0, depth - 1) * 0.35, 3),
        "source_event_ids": source_events, "source_compact_ids": source_compacts,
        "event_count": int(event_count), "continued": bool(continued),
        "legacy": bool(legacy), "degraded": bool(result.get("degraded")),
        "first_ts": first_ts, "last_ts": last_ts,
    }
    path = _compact_dir(chat_id) / f"{cid}.md"
    meta["path"] = path.relative_to(BASE).as_posix()
    parts = [f"<!-- praxis-compact: {json.dumps(meta, ensure_ascii=False, separators=(',', ':'))} -->",
             f"# Compact {cid}", "", f"_Tier {tier} · depth {depth} · "
             f"источников {len(all_sources)} · событий {event_count}"
             f"{' · эпизод продолжается' if continued else ''}_", "", "## Суть",
             str(result.get("summary") or "").strip()]
    if source_note:
        parts += ["", "## Ограничение происхождения", source_note.strip()]
    threads = [str(x).strip() for x in (result.get("open_threads") or []) if str(x).strip()]
    if threads:
        parts += ["", "## Открытые нити"] + [f"- {x}" for x in threads]
    claims = [x for x in (result.get("claims") or []) if isinstance(x, dict)]
    if claims:
        parts += ["", "## Claim-кандидаты (не канон до ReflectionRun)"]
        for c in claims:
            refs = [str(x) for x in (c.get("evidence_ids") or []) if str(x) in all_sources]
            parts.append(f"- **{str(c.get('subject') or '—').strip()}**: "
                         f"{str(c.get('text') or '').strip()} "
                         f"_(confidence: {c.get('confidence') or 'uncertain'}; evidence: "
                         f"{', '.join(refs) or 'нет'})_")
    parts += ["", "## Происхождение"]
    if source_events:
        parts.append("- Events: " + ", ".join(source_events))
    if source_compacts:
        parts.append("- Compacts: " + ", ".join(source_compacts))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    tmp.replace(path)
    return meta


def read_artifact_meta(path: Path) -> dict:
    try:
        first = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return {}
    m = _META_RE.match(first.strip())
    if not m:
        return {}
    try:
        return json.loads(m.group(2))
    except Exception:
        return {}


def _compact_recap(text: str) -> str:
    """Extract only the writer-owned recap section, never a whole malformed file."""
    match = re.search(r"^## Суть\s*$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return (match.group(1) if match else "").strip()


def _read_compact_candidate(path: Path, chat_id: str | int) -> tuple[dict, str]:
    """Bind a compact to its canonical chat path and exact v1 writer schema."""
    expected_chat = str(chat_id)
    try:
        target = path.resolve()
        expected_dir = _compact_dir(expected_chat).resolve()
        if target.parent != expected_dir:
            return {}, ""
        text = target.read_text(encoding="utf-8", errors="strict")
        relative = target.relative_to(BASE.resolve()).as_posix()
    except (OSError, UnicodeError, ValueError):
        return {}, ""
    if not text or len(text) > 2_000_000:
        return {}, ""
    lines = text.splitlines()
    match = _META_RE.fullmatch(lines[0].strip()) if lines else None
    if not match or match.group(1) != "compact":
        return {}, ""
    try:
        meta = json.loads(match.group(2))
    except (TypeError, ValueError):
        return {}, ""
    if not isinstance(meta, dict) or set(meta) != _COMPACT_KEYS:
        return {}, ""
    compact_id = meta.get("id")
    string_keys = ("schema", "id", "chat_id", "created_at", "first_ts", "last_ts", "path")
    if (any(type(meta.get(key)) is not str for key in string_keys)
            or any(type(meta.get(key)) is not int for key in ("tier", "depth", "event_count"))
            or type(meta.get("preservation_priority")) not in (int, float)
            or any(type(meta.get(key)) is not bool for key in ("continued", "legacy", "degraded"))
            or type(meta.get("source_event_ids")) is not list
            or type(meta.get("source_compact_ids")) is not list):
        return {}, ""
    event_ids = meta["source_event_ids"]
    compact_ids = meta["source_compact_ids"]
    if (meta["schema"] != "praxis.life.compact.v1"
            or meta["chat_id"] != expected_chat
            or not _COMPACT_ID_RE.fullmatch(compact_id)
            or target.stem != compact_id
            or meta["path"] != relative
            or not _UTC_MILLIS_RE.fullmatch(meta["created_at"])
            or meta["tier"] < 1 or meta["depth"] < 1 or meta["event_count"] < 0
            or meta["preservation_priority"] <= 0
            or any(type(value) is not str or not _EVENT_ID_RE.fullmatch(value) for value in event_ids)
            or any(type(value) is not str or not _COMPACT_ID_RE.fullmatch(value) for value in compact_ids)
            or len(event_ids) != len(set(event_ids))
            or len(compact_ids) != len(set(compact_ids))
            or (event_ids and compact_ids)):
        return {}, ""
    legacy = meta["legacy"]
    if legacy:
        if event_ids or compact_ids or meta["event_count"] != 0:
            return {}, ""
    elif (not _UTC_MILLIS_RE.fullmatch(meta["first_ts"])
          or not _UTC_MILLIS_RE.fullmatch(meta["last_ts"])):
        return {}, ""
    elif event_ids:
        if meta["tier"] != 1 or meta["depth"] != 1 or meta["event_count"] != len(event_ids):
            return {}, ""
    elif compact_ids:
        if meta["tier"] < 2 or meta["depth"] < 2 or meta["event_count"] < 1:
            return {}, ""
    else:
        return {}, ""
    recap = _compact_recap(text)
    if not recap or f"# Compact {compact_id}" not in text:
        return {}, ""
    return meta, recap


def _compact_candidates(compact_id: str, chat_id: str | int | None = None) -> list[tuple[dict, str]]:
    if not _COMPACT_ID_RE.fullmatch(str(compact_id or "")) or not COMPACTS_DIR.exists():
        return []
    # Компакт одного места мог быть записан под ключом ветки — ищем по всем ключам
    # места, но каждый кандидат по-прежнему привязывается к СВОЕМУ каталогу.
    members = _member_keys(chat_id) if chat_id is not None else []
    paths = ([_compact_dir(member) / f"{compact_id}.md" for member in members]
             if chat_id is not None else list(COMPACTS_DIR.glob(f"*/{compact_id}.md")))
    out: list[tuple[dict, str]] = []
    for path in paths:
        if chat_id is not None:
            bound_chat = path.parent.name
        else:
            declared = read_artifact_meta(path).get("chat_id")
            if type(declared) is not str:
                continue
            bound_chat = declared
        item = _read_compact_candidate(path, bound_chat)
        if item[0]:
            out.append(item)
    return out


def compact_meta(compact_id: str, chat_id: str | int | None = None) -> dict:
    candidates = _compact_candidates(compact_id, chat_id)
    return dict(candidates[0][0]) if len(candidates) == 1 else {}


def compact_text(compact_id: str, chat_id: str | int | None = None) -> str:
    candidates = _compact_candidates(compact_id, chat_id)
    return candidates[0][1] if len(candidates) == 1 else ""


def _episode_groups(inputs: list[dict]) -> list[list[dict]]:
    if not inputs:
        return []
    groups, cur = [], [inputs[0]]
    for prev, item in zip(inputs, inputs[1:]):
        gap = _epoch(item.get("ts")) - _epoch(prev.get("ts"))
        if gap >= EPISODE_GAP_SEC:
            groups.append(cur)
            cur = [item]
        else:
            cur.append(item)
    groups.append(cur)
    return groups


def _write_episodes(chat_id, compact_id: str, inputs: list[dict], proposed: list[dict],
                    continued: bool) -> list[str]:
    by_id = {str(x.get("id")): i for i, x in enumerate(inputs)}
    ranges = []
    for ep in proposed or []:
        if not isinstance(ep, dict):
            continue
        a, b = str(ep.get("start_id") or ""), str(ep.get("end_id") or "")
        if a in by_id and b in by_id and by_id[a] <= by_id[b]:
            ranges.append((by_id[a], by_id[b], ep))
    ranges.sort(key=lambda item: (item[0], item[1]))
    # A model may return only the interesting episode and silently omit the rest.
    # Episode artifacts are provenance, so accept a proposal only when it covers
    # the folded prefix exactly once, without gaps or overlaps.
    cursor = 0
    complete = bool(ranges)
    for start, end, _ in ranges:
        if start != cursor:
            complete = False
            break
        cursor = end + 1
    complete = complete and cursor == len(inputs)
    specs = [(inputs[start:end + 1], ep) for start, end, ep in ranges] if complete else []
    if not specs:
        groups = _episode_groups(inputs)
        specs = [(g, {"title": "Эпизод диалога", "summary": "",
                      "status": "continued" if continued and i == len(groups) - 1 else "closed"})
                 for i, g in enumerate(groups)]
    made = []
    for group, ep in specs:
        if not group:
            continue
        eid = _id("ep")
        status = str(ep.get("status") or "closed")
        if continued and group[-1].get("id") == inputs[-1].get("id"):
            status = "continued"
        meta = {"schema": "praxis.life.episode.v1", "id": eid, "chat_id": str(chat_id),
                "created_at": _utc_iso(), "status": status, "compact_id": compact_id,
                "source_event_ids": [str(x.get("id")) for x in group],
                "first_ts": group[0].get("ts"), "last_ts": group[-1].get("ts")}
        path = _episode_dir(chat_id) / f"{eid}.md"
        meta["path"] = path.relative_to(BASE).as_posix()
        summary = str(ep.get("summary") or "").strip()
        if not summary:
            summary = "\n".join(f"- {str(x.get('line') or x.get('text') or '')[:500]}" for x in group)
        body = (f"<!-- praxis-episode: {json.dumps(meta, ensure_ascii=False, separators=(',', ':'))} -->\n"
                f"# {str(ep.get('title') or 'Эпизод диалога').strip()}\n\n"
                f"_Статус: {status}; compact: {compact_id}_\n\n{summary}\n\n"
                f"## Events\n- " + "\n- ".join(meta["source_event_ids"]) + "\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        made.append(eid)
    return made


def tape_window(rows: list[dict], max_chars: int | None = None) -> list[dict]:
    """Самые свежие записи ленты, умещающиеся в max_chars знаков (по полю `line`).

    Последняя запись едет всегда, даже если она одна длиннее потолка: то, на что
    она отвечает, из кадра не выпадает. 0 — без потолка (как было).
    """
    limit = TAPE_CHARS if max_chars is None else int(max_chars)
    if limit <= 0 or not rows:
        return list(rows)
    kept: list[dict] = []
    used = 0
    for row in reversed(rows):
        cost = len(str(row.get("line") or "")) + 1
        if kept and used + cost > limit:
            break
        kept.append(row)
        used += cost
    kept.reverse()
    return kept


def plan_hot_fold(hot: list[dict], *, force: bool = False, tape_chars: int | None = None,
                  place: str | int | None = None) -> dict:
    """`tape_chars` — потолок ленты в знаках для ЭТОГО места (13.09: группе свой рычаг);
    None — общий TAPE_CHARS, 0 — давления по знакам нет. `place` — чьё окно: комнате
    свои пороги (`hot_bounds`, 25.09), без места — прежние HOT_*."""
    tape_limit = TAPE_CHARS if tape_chars is None else max(0, int(tape_chars))
    lo, hi, hard_hi, token_cap = hot_bounds(place)
    count = len(hot)
    token_rows = [int(x.get("tokens") or estimate_tokens(x.get("line", ""))) for x in hot]
    tokens = sum(token_rows)
    chars = sum(len(str(x.get("line") or "")) for x in hot)
    if count < 2:
        return {"due": False, "reason": "too_short", "count": count, "tokens": tokens,
                "chars": chars}
    char_pressure = tape_limit > 0 and chars > tape_limit
    pressure = count >= hi or tokens > token_cap or char_pressure or force
    if not pressure:
        return {"due": False, "reason": "within_window", "count": count, "tokens": tokens,
                "chars": chars}
    token_pressure = tokens > token_cap
    token_target = 1
    if token_pressure:
        suffix_tokens = tokens
        for i in range(1, count):
            suffix_tokens -= token_rows[i - 1]
            token_target = i
            if suffix_tokens <= token_cap:
                break
    if char_pressure:
        # Лента переросла потолок в знаках: сворачиваем старое так, чтобы горячим
        # осталось ~TAPE_KEEP потолка. Давление жёсткое, как у токенов: ждать границу
        # эпизода нельзя — кадр уже превышен.
        keep = int(tape_limit * TAPE_KEEP)
        suffix_chars = chars
        char_target = 1
        for i in range(1, count):
            suffix_chars -= len(str(hot[i - 1].get("line") or ""))
            char_target = i
            if suffix_chars <= keep:
                break
        token_pressure = True
        token_target = max(token_target, char_target)
    count_target = max(1, count - lo) if count >= hi or force else 1
    # ПОТОЛОК ПЛАНА — сколько влезает в один промпт. Замер 21.08 по AbstractDL:
    # кольцо 3802 при потолке 125, план просил свернуть 3752 события разом, модель
    # брала префикс на 48 000 знаков, остальное честно объявлялось невлезшим — и
    # следующий проход планировал те же 3752. Сорок три вызова модели в сутки, ноль
    # срезанных событий. Теперь план и упаковка меряют одним и тем же.
    budget_fit = budget_prefix(hot)
    target = min(max(count_target, token_target), budget_fit)
    min_remaining = 1 if token_pressure or force else lo
    candidates = []
    for i in range(1, count):
        remaining = count - i
        if i > budget_fit:
            break
        if remaining < min_remaining or (token_pressure and i < token_target):
            continue
        gap = _epoch(hot[i].get("ts")) - _epoch(hot[i - 1].get("ts"))
        if gap >= EPISODE_GAP_SEC:
            candidates.append((abs(i - target), i, gap))
    if candidates:
        _, fold, gap = min(candidates)
        return {"due": True, "fold": fold, "continued": False, "reason": "episode_boundary",
                "gap_sec": gap, "count": count, "tokens": tokens,
                "budget_fit": budget_fit,
                # 25.09: жёсткий ли повод (токены/знаки/HARD_HI/force) — мягкий только предлагается
                "hard": bool(token_pressure or char_pressure or count >= hard_hi or force)}
    hard = token_pressure or count >= hard_hi or force
    if not hard:
        return {"due": False, "reason": "open_episode", "count": count, "tokens": tokens}
    valid = [i for i in range(1, count)
             if count - i >= min_remaining and (not token_pressure or i >= token_target)
             and i <= budget_fit
             and str(hot[i - 1].get("direction")) == "out"]
    fold = min(valid, key=lambda i: abs(i - target)) if valid else max(1, min(target, count - 1))
    # Прогресс обязан быть монотонным: свернуть меньше одной записи нельзя, больше
    # влезающего — бессмысленно. Между этими двумя границами план всегда исполним.
    fold = max(1, min(fold, budget_fit))
    return {"due": True, "fold": fold, "continued": True, "hard": True,
            "reason": ("token_cap" if tokens > token_cap else
                       "tape_chars" if char_pressure else "hard_window"),
            "count": count, "tokens": tokens, "chars": chars, "budget_fit": budget_fit}


def _frontier_input(meta: dict, chat_id: str | int) -> dict:
    return {"id": meta.get("id"), "text": compact_text(str(meta.get("id") or ""), chat_id),
            "salience": 3 if meta.get("continued") else 2,
            "preservation_priority": meta.get("preservation_priority") or 1.0,
            "ts": meta.get("created_at")}


def _fold_tiers(chat_id, state: dict) -> list[str]:
    made = []
    tier = 1
    # A bounded loop protects a corrupt state from spinning forever.
    for _ in range(16):
        same = sorted([x for x in state.get("frontier", []) if int(x.get("tier") or 1) == tier],
                      key=lambda x: (_epoch(x.get("first_ts")) or _epoch(x.get("created_at")), x.get("id", "")))
        if len(same) < TIER_HI:
            higher = [int(x.get("tier") or 1) for x in state.get("frontier", []) if int(x.get("tier") or 1) > tier]
            if higher:
                tier += 1
                continue
            break
        n = min(len(same), max(1, TIER_HI - TIER_LO))
        sources = same[:n]
        inputs = [_frontier_input(x, chat_id) for x in sources]
        result = _model_compact(inputs, tier=tier + 1,
                                depth=max(int(x.get("depth") or tier) for x in sources) + 1,
                                continued=any(bool(x.get("continued")) for x in sources))
        if not result:
            result = _fallback_compact(inputs, continued=any(bool(x.get("continued")) for x in sources))
        parent = _write_compact(
            chat_id, result, tier=tier + 1,
            depth=max(int(x.get("depth") or tier) for x in sources) + 1,
            source_events=[], source_compacts=[str(x.get("id")) for x in sources],
            event_count=sum(int(x.get("event_count") or 0) for x in sources),
            continued=any(bool(x.get("continued")) for x in sources),
            first_ts=sources[0].get("first_ts") or "", last_ts=sources[-1].get("last_ts") or "")
        remove = {x.get("id") for x in sources}
        state["frontier"] = [x for x in state["frontier"] if x.get("id") not in remove] + [parent]
        append_event("memory_compact", chat_id=chat_id, text=f"Tier {tier + 1}: {parent['id']}",
                     source="memory_life", refs=parent["source_compact_ids"],
                     meta={"compact_id": parent["id"], "tier": tier + 1,
                           "degraded": parent.get("degraded", False)})
        made.append(parent["id"])
        tier = 1
    return made


def _drop_unprovable_inputs(inputs: list[dict], chat_id) -> list[dict]:
    """Убрать из свёртки строки, которых не видит индекс доказательств.

    В кольце и в ленте они остаются: сказанное не исчезает из разговора. Но свёртка,
    назвавшая такое событие источником, не разрешается НИКОГДА — и её же источники
    остаются непокрытыми, то есть следующий проход свернёт тот же блок заново. Ровно
    так стояло кольцо комнаты Ouroboros: три строки на 152 события (21.09).
    """
    kept = [row for row in inputs if not row.get("unprovable")]
    if len(kept) != len(inputs):
        names = [str(row.get("id")) for row in inputs if row.get("unprovable")]
        # Громко об этом говорит пересборка состояния, по разу на место; здесь было бы
        # по разу на КАЖДУЮ попытку свёртки.
        log.debug("свёртка места %s: %d событий не идут в свёртку (индекс их не "
                  "принимает), остаются горячими: %s",
                  chat_id, len(names), ", ".join(names[:3]))
    return kept


def _compact_provable(compact_id: str) -> bool:
    """Принимает ли собственный провенанс только что написанную свёртку.

    Молчащий прибор приговором не считается: не сумели спросить — верим записи.
    """
    try:
        evidence = memory_provenance.claim_evidence_index(MEM_DIR)
        return bool(memory_provenance.compact_coverage(
            str(compact_id), evidence).get("valid"))
    except Exception:
        log.warning("проверка свежей свёртки %s не удалась", compact_id, exc_info=True)
        return True


def _discard_unprovable_compact(meta: dict, where: str) -> bool:
    """Снести свёртку, которую её собственное разрешение не принимает. True — снесли.

    ОБЩАЯ ОГРАДА, поставленная 21.09 после трёх мельниц подряд. У всех трёх форма
    одна: свёртка написана, разрешение её не приняло, источники остались там же,
    и следующий проход написал её заново — вызов модели в минуту без движения
    памяти. Причины были разные (недоказуемое событие, пересечение детей, границы
    не по min/max), и будут ещё; здесь закрыта не причина, а СПОСОБ, которым любая
    такая причина превращается в бесконечный цикл.

    Свежую свёртку можно удалять: на неё ещё никто не сослался — ни состояние, ни
    событие `memory_compact`, ни расписка. Оставить её значит оставить и мельницу.
    """
    compact_id = str(meta.get("id") or "")
    if not compact_id or _compact_provable(compact_id):
        return False
    log.error("%s: свежая свёртка %s не принимается собственным разрешением — сношу "
              "и НЕ считаю её источники свёрнутыми (иначе они мелются по кругу)",
              where, compact_id)
    path = meta.get("path")
    if path:
        try:
            (BASE / str(path)).unlink(missing_ok=True)
        except (OSError, ValueError):
            log.warning("не смог убрать файл негодной свёртки %s", path, exc_info=True)
    return True


def _tier_fold_own_leaves(meta: dict) -> frozenset[str] | None:
    """События свёртки, если они видны из её собственной шапки; иначе None.

    У первого яруса листья названы прямо (`source_event_ids`). У верхних они лежат
    через детей — их поднимает `_tier_fold_leaves` по индексу доказательств.
    """
    events = meta.get("source_event_ids")
    if events:
        return frozenset(str(x) for x in events)
    if meta.get("source_compact_ids"):
        return None
    return frozenset()


def _tier_fold_leaves(meta: dict, evidence_ref: list) -> frozenset[str] | None:
    """Листья свёртки любого яруса — для проверки, что дети не делят события.

    25.09, диагноз по проду (AbstractDL: 64 общих события у детей яруса 3, Ouroboros: 4
    у яруса 2). Пересечение проверялось только у первого яруса; выше кандидат брался с
    делящими листья детьми, родитель писался, разрешение отвергало его
    (`len(unique_leaves) != len(leaves)`), и ярус сворачивал ту же пачку снова и снова —
    пять раз в день, а верхний ярус этих комнат не рос никогда. Индекс доказательств
    кэширован по подписям файлов и разрешения мемоизированы, поэтому подъём листьев
    здесь стоит стата, а не полного чтения графа. Индекс строится один раз на выбор
    кандидата и передаётся через `evidence_ref` (список из одного элемента или пустой).
    """
    own = _tier_fold_own_leaves(meta)
    if own is not None:
        return own
    compact_id = str(meta.get("id") or "")
    if not compact_id:
        return None
    try:
        if not evidence_ref:
            evidence_ref.append(memory_provenance.claim_evidence_index(MEM_DIR))
        resolved = memory_provenance.compact_coverage(compact_id, evidence_ref[0])
    except Exception:
        log.warning("листья свёртки %s не поднялись — пересечение не проверено",
                    compact_id, exc_info=True)
        return None
    if not resolved.get("valid"):
        return None
    return frozenset(str(x) for x in (resolved.get("leaves") or ()))


def _tier_fold_bounds(sources: list[dict]) -> tuple[str, str]:
    """Границы родителя — min/max по детям, а НЕ края списка.

    Разрешение свёртки (`memory_provenance._resolve_compact`) считает `first_ts` и
    `last_ts` именно так. Пока место было одной лентой, края отсортированного списка
    совпадали с min/max; у места из нескольких веток дети идут внахлёст по времени, и
    родитель с краями списка не принимается собственным разрешением — навсегда.
    """
    first = [str(x.get("first_ts") or "") for x in sources if x.get("first_ts")]
    last = [str(x.get("last_ts") or "") for x in sources if x.get("last_ts")]
    return (min(first, default=""), max(last, default=""))


def _tier_fold_candidate(chat_id: str | int, state: dict) -> dict | None:
    """Describe the next higher-tier fold without writing or calling the model."""
    tier = 1
    for _ in range(16):
        same = sorted(
            [x for x in state.get("frontier", []) if int(x.get("tier") or 1) == tier],
            key=lambda x: (
                _epoch(x.get("first_ts")) or _epoch(x.get("created_at")),
                x.get("id", ""),
            ),
        )
        if len(same) >= TIER_HI:
            count = min(len(same), max(1, TIER_HI - TIER_LO))
            # Дети не смеют делить событие. Разрешение свёртки запрещает повтор листа
            # целиком: родитель над пересекающимися детьми невалиден НАВСЕГДА, его
            # источники остаются во фронтире, и ярус сворачивает их снова и снова — по
            # вызову модели в минуту (замер 21.09, комната Ouroboros: 23 таких родителя
            # подряд). Пересечение настоящее: ветки места сводились каждая под своим
            # ключом, а потом ключи привязались к одному месту.
            sources: list[dict] = []
            seen_leaves: set[str] = set()
            skipped: list[str] = []
            unresolved: list[str] = []
            evidence_ref: list = []
            for item in same:
                leaves = _tier_fold_leaves(item, evidence_ref)
                if leaves is None:
                    # 25.09 (ревью V4 N1): покрытие ребёнка не разрешилось — листьев не
                    # знаем, и родитель над ним будет отвергнут разрешением: та же
                    # мельница другим путём. Не берём; ребёнок ждёт refresh.
                    unresolved.append(str(item.get("id")))
                    continue
                if leaves and (leaves & seen_leaves):
                    skipped.append(str(item.get("id")))
                    continue
                sources.append(item)
                if leaves:
                    seen_leaves |= leaves
                if len(sources) >= count:
                    break
            if skipped:
                log.warning(
                    "свёртка яруса %d: %d источник(ов) делят события с уже взятыми, "
                    "беру без них (%s)", tier, len(skipped), ", ".join(skipped[:5]))
            if unresolved:
                log.warning(
                    "свёртка яруса %d: %d источник(ов) с неразрешённым покрытием — не беру "
                    "(%s)", tier, len(unresolved), ", ".join(unresolved[:5]))
            if len(sources) >= 2:
                continued = any(bool(x.get("continued")) for x in sources)
                return {
                    "tier": tier,
                    "sources": sources,
                    "source_ids": [str(x.get("id")) for x in sources],
                    "inputs": [_frontier_input(x, chat_id) for x in sources],
                    "continued": continued,
                    "depth": max(int(x.get("depth") or tier) for x in sources) + 1,
                }
        if not any(int(x.get("tier") or 1) > tier for x in state.get("frontier", [])):
            return None
        tier += 1
    return None


def _fold_tiers_transactional(chat_id: str | int) -> list[str]:
    """Fold warm tiers with evaluator work outside the derived-state transaction.

    25.08: имена fallback-родителей возвращаются ВТОРЫМ списком. Раньше
    деградация верхних ярусов записывалась в meta и событие memory_compact,
    но не всплывала в возврат — стоп-кран compact_places видел только
    degraded первого яруса, и обрыв модели посреди каскада молча клал
    механическую выжимку в вершину пирамиды (живой пример: 0fcb8b61).
    """
    made: list[str] = []
    degraded_made: list[str] = []
    folded_runs: set[tuple[str, ...]] = set()
    for _ in range(16):
        with _state_write_guard(chat_id), _WRITE_LOCK:
            state = _rebuild_state_locked(chat_id)
            candidate = _tier_fold_candidate(chat_id, state)
        if candidate is None:
            break
        # ПРЕДОХРАНИТЕЛЬ. Те же источники второй раз за проход означают одно: родитель
        # написан, а пересборка его не приняла и вернула детей во фронтир. Дальше это
        # вызов модели в минуту без единого сдвига памяти. Останавливаемся и говорим.
        run_key = tuple(candidate["source_ids"])
        if run_key in folded_runs:
            log.error("ярус %d сворачивает те же источники повторно: родитель не "
                      "принимается собственным разрешением, останавливаю каскад (%s)",
                      candidate["tier"], ", ".join(candidate["source_ids"][:4]))
            break
        folded_runs.add(run_key)

        result = _model_compact(
            candidate["inputs"], tier=candidate["tier"] + 1,
            depth=candidate["depth"], continued=candidate["continued"])
        if not result:
            result = _fallback_compact(
                candidate["inputs"], continued=candidate["continued"])

        with _state_write_guard(chat_id), _WRITE_LOCK:
            state = _rebuild_state_locked(chat_id)
            current = _tier_fold_candidate(chat_id, state)
            if (current is None
                    or current["tier"] != candidate["tier"]
                    or current["source_ids"] != candidate["source_ids"]):
                continue
            sources = current["sources"]
            first_ts, last_ts = _tier_fold_bounds(sources)
            parent = _write_compact(
                chat_id, result, tier=current["tier"] + 1,
                depth=current["depth"], source_events=[],
                source_compacts=current["source_ids"],
                event_count=sum(int(x.get("event_count") or 0) for x in sources),
                continued=current["continued"],
                first_ts=first_ts, last_ts=last_ts)
            if _discard_unprovable_compact(parent, f"верхний ярус места {chat_id}"):
                break
            remove = set(current["source_ids"])
            state["frontier"] = [
                x for x in state["frontier"] if str(x.get("id")) not in remove
            ] + [parent]
            append_event(
                "memory_compact", chat_id=chat_id,
                text=f"Tier {current['tier'] + 1}: {parent['id']}",
                source="memory_life", refs=parent["source_compact_ids"],
                meta={"compact_id": parent["id"], "tier": current["tier"] + 1,
                      "degraded": parent.get("degraded", False)})
            _save_state(state)
            made.append(parent["id"])
            if parent.get("degraded"):
                degraded_made.append(parent["id"])
    return made, degraded_made


def _subtree_event_rows(evidence: dict, compact_id: str, _seen: set | None = None) -> list[dict]:
    """Все исходные события под свёрткой, сквозь ярусы (для имён авторов окна)."""
    seen = _seen if _seen is not None else set()
    if compact_id in seen:
        return []
    seen.add(compact_id)
    meta = (evidence.get("compacts") or {}).get(compact_id) or {}
    events = evidence.get("events") or {}
    rows = [row for row in (events.get(str(e)) for e in (meta.get("source_event_ids") or []))
            if isinstance(row, dict)]
    for child in meta.get("source_compact_ids") or []:
        rows += _subtree_event_rows(evidence, str(child), seen)
    return rows


def reissue_degraded_compact(chat_id: str | int, compact_id: str, *,
                             why: str = "degraded") -> dict:
    """Перевыпустить обрубок: та же пачка событий, но сводка от модели.

    24.09: второй законный повод — `why="foreign_self"`: сводка назвала «я» другого
    автора своего же окна («Я — Hope»). Проверяется по файлу, а не по слову вызывающего;
    остальные предохранители (лист, без родителя, источники на месте) те же.

    Обрубок — свёртка, написанная `_fallback_compact` без модели: механическая
    выжимка вместо синтеза. К 21.09 их 688 из 5000, почти все — от потолка
    `COMPACT_MAX_TOKENS = 4000`, который держался до `98b6bc71`.

    ⛔ ТОЛЬКО ЛИСТ БЕЗ РОДИТЕЛЯ. Если обрубок уже свёрнут выше, удалять его нельзя:
    родитель останется с висящей ссылкой, разрешение его не примет, и ярус начнёт
    писать его заново — ровно та мельница, которую чинили 21.09. Поэтому родители
    ищутся по всему индексу, а не по состоянию места.

    Порядок: модель → новая свёртка → проверка её собственным разрешением → и только
    потом снос старой. Модель молчит или новая не принята — старая остаётся на месте:
    обрубок хуже синтеза, но лучше дыры.

    Эпизоды старой свёртки не переписываются: они описывают те же события и остаются
    честными; ссылка на снесённый компакт в них — история, а не факт о настоящем.
    """
    chat = str(chat_id)
    place = str(place_key(chat))
    evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    meta = (evidence.get("compacts") or {}).get(str(compact_id))
    if not isinstance(meta, dict):
        return {"ok": False, "reason": "unknown_compact"}
    if why not in ("degraded", "foreign_self"):
        return {"ok": False, "reason": "unknown_why"}
    if why == "degraded" and not meta.get("degraded"):
        return {"ok": False, "reason": "not_degraded"}
    compacts = evidence.get("compacts") or {}
    children = [str(x) for x in (meta.get("source_compact_ids") or [])]
    # Ярус выше собран из дочерних свёрток; перевыпустить его можно только по чужому «я»:
    # обрубок яруса — дело `_fold_tiers`, а не этой руки.
    if children and why != "foreign_self":
        return {"ok": False, "reason": "not_a_leaf"}
    parents = [cid for cid, other in compacts.items()
               if str(compact_id) in (other.get("source_compact_ids") or ())]
    if parents:
        return {"ok": False, "reason": "has_parent", "parents": parents[:3]}
    source_ids = [str(x) for x in (meta.get("source_event_ids") or [])]
    if children:
        # 24.09: «я (Ashe) писала» стояло в свёртке яруса 2 комнаты Уробороса. Вход — те же
        # дочерние свёртки, как в `_fold_tiers`; авторы для ограды — из всех сообщений под
        # ней, потому что у сводок-детей своего автора нет.
        if any(not isinstance(compacts.get(ch), dict) for ch in children):
            return {"ok": False, "reason": "sources_missing"}
        inputs = [_frontier_input(dict(compacts[ch], id=ch), chat) for ch in children]
        if any(not str(item.get("text") or "").strip() for item in inputs):
            return {"ok": False, "reason": "sources_missing"}
        subtree = _subtree_event_rows(evidence, str(compact_id))
    else:
        rows = [(evidence.get("events") or {}).get(eid) for eid in source_ids]
        if not source_ids or any(not isinstance(row, dict) for row in rows):
            return {"ok": False, "reason": "sources_missing"}
        inputs = _conversation_hot_rows(rows)
        if len(inputs) != len(source_ids):
            # Часть источников вытеснена поздней ревизией: это работа refresh_compacts,
            # а не перевыпуска — там другая машинерия и другие квитанции.
            return {"ok": False, "reason": "sources_not_current"}
        subtree = inputs
    authors = _window_authors(subtree)
    if why == "foreign_self":
        try:
            old_text = (BASE / str(meta.get("path"))).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return {"ok": False, "reason": "compact_unreadable"}
        if not _foreign_self(_compact_recap(old_text), authors):
            return {"ok": False, "reason": "no_foreign_self"}
    tier, depth = int(meta.get("tier") or 1), int(meta.get("depth") or 1)
    continued = bool(meta.get("continued"))
    result = _model_compact(inputs, tier=tier, depth=depth, continued=continued,
                            authors=authors)
    if not result:
        return {"ok": False, "reason": "model_unavailable"}
    with _state_write_guard(place), _WRITE_LOCK:
        fresh = _write_compact(
            str(meta.get("chat_id") or chat), result, tier=tier, depth=depth,
            source_events=source_ids, source_compacts=children,
            event_count=int(meta.get("event_count") or len(source_ids)),
            continued=continued,
            source_note=(f"Перевыпуск обрубка {compact_id}; те же события." if why == "degraded"
                         else f"Перевыпуск {compact_id}: сводка называла «я» чужим именем; те же события."),
            first_ts=str(meta.get("first_ts") or ""),
            last_ts=str(meta.get("last_ts") or ""))
        if fresh.get("degraded"):
            _discard_unprovable_compact(fresh, f"перевыпуск {compact_id}")
            try:
                (BASE / str(fresh.get("path"))).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            return {"ok": False, "reason": "still_degraded"}
        if _discard_unprovable_compact(fresh, f"перевыпуск {compact_id}"):
            return {"ok": False, "reason": "reissue_not_provable"}
        try:
            (BASE / str(meta.get("path"))).unlink(missing_ok=True)
        except (OSError, ValueError):
            log.warning("перевыпуск %s: старый файл не убрался", compact_id, exc_info=True)
            return {"ok": False, "reason": "old_not_removed", "new_id": fresh["id"]}
        append_event(
            "memory_compact", chat_id=str(meta.get("chat_id") or chat),
            text=(f"Перевыпуск {'обрубка ' if why == 'degraded' else '(чужое «я») '}"
                  f"{compact_id} → {fresh['id']}; {len(source_ids)} событий"),
            source="memory_life", refs=source_ids,
            meta={"compact_id": fresh["id"], "tier": tier, "degraded": False,
                  "replaced": str(compact_id), "why": why})
        state = _rebuild_state_locked(place)
    return {"ok": True, "old_id": str(compact_id), "new_id": str(fresh["id"]),
            "events": len(source_ids), "hot": len(state.get("hot") or []),
            "frontier": len(state.get("frontier") or [])}


def compact_if_due(chat_id: str | int, *, force: bool = False) -> dict:
    """Compact one hot prefix and recursively fold warm tiers. Raw events are never removed.

    Сворачивается МЕСТО целиком: новые компакты пишутся под ключом места, а не ветки,
    иначе один разговор продолжал бы копить по свёртке на каждую свою ветку.
    """
    chat_id = adopt_place(chat_id)
    with _state_write_guard(chat_id), _WRITE_LOCK:
        state = _load_state(chat_id, rebuild=True)
        # Планируем по ДОКАЗУЕМОЙ подпоследовательности кольца. Помеченные строки
        # остаются в ленте, но для свёртки их нет — иначе призрак в голове кольца
        # запирает свёртку навсегда: план брал бы префикс из него одного, свёртка
        # оказывалась бы пустой, и место стояло бы при полном кольце (поймано
        # стендом test_mill_never_again_2109, а не на проде).
        provable = _drop_unprovable_inputs(list(state.get("hot") or []), chat_id)
        plan = plan_hot_fold(provable, force=force, tape_chars=tape_chars_for(chat_id),
                             place=chat_id)
        if not plan.get("due"):
            if fold_offer_enabled():
                clear_fold_offer(chat_id)      # окно снова в норме — предложение снято
            return {"ok": True, "folded": 0, "plan": plan,
                    "hot": len(state.get("hot") or []), "tiers": [], "degraded_tiers": []}
        if not force and fold_offer_enabled() and not plan.get("hard"):
            # 25.09, слово Егора: на мягком пороге свёртка ПРЕДЛАГАЕТСЯ, а не делается.
            _note_fold_offer(chat_id, plan)
            return {"ok": True, "folded": 0, "offered": True, "plan": plan,
                    "hot": len(state.get("hot") or []), "tiers": [], "degraded_tiers": []}
        fold = int(plan["fold"])
        inputs = provable[:fold]
    result = _model_compact(inputs, tier=1, depth=1, continued=bool(plan.get("continued")))
    if not result:
        result = _fallback_compact(inputs, continued=bool(plan.get("continued")))
    manifest = result.get("_manifest") if isinstance(result, dict) else None
    omitted = {str(i) for i in ((manifest or {}).get("omitted") or [])}
    continued = bool(plan.get("continued"))
    if omitted:
        # Свёрнутым объявляем только непрерывный старый префикс, который реально
        # дошёл до модели. Невлезший хвост остаётся горячим и попадёт в следующую
        # свёртку — раньше он молча объявлялся покрытым и уходил из фронтира.
        inputs = [x for x in inputs if str(x.get("id")) not in omitted]
        fold = len(inputs)
        # И вместе с границей обязана переехать ПРАВДА о ней. `plan["continued"]`
        # посчитан для СТАРОЙ границы (например, реального разрыва в разговоре).
        # Обрезанная бюджетом свёртка по построению в этот разрыв не попадает — она
        # кончается посреди живой нити. Оставить continued=False значило бы записать
        # в компакт и в артефакт эпизода, что разговор закончен там, где он идёт.
        continued = True
        log.warning("life compact [%s]: %s событий не влезли в промпт — оставлены "
                    "горячими; эпизод помечен продолжающимся", chat_id, len(omitted))
    if not inputs:
        return {"ok": True, "folded": 0, "plan": plan,
                "hot": len(state.get("hot") or []), "tiers": [], "degraded_tiers": []}

    with _state_write_guard(chat_id), _WRITE_LOCK:
        state = _rebuild_state_locked(chat_id)
        # Сверяемся с той же подпоследовательностью, по которой планировали: в кольце
        # между свёрнутыми строками могут стоять помеченные, и позиционный префикс с
        # ними не совпадёт никогда — свёртка возвращала бы `state_changed` вечно.
        if _drop_unprovable_inputs(list(state.get("hot") or []), chat_id)[:fold] != inputs:
            return {"ok": False, "reason": "state_changed", "folded": 0,
                    "plan": plan, "hot": len(state.get("hot") or []), "tiers": [], "degraded_tiers": []}
        source_ids = [str(x.get("id")) for x in inputs]
        # Привязка пишется ДО компакта: компакт, чьи события ещё никуда не привязаны,
        # был бы неканоническим с первой секунды. Порядок здесь — не стиль, а условие
        # того, что записанное останется читаемым.
        origins = {str(x.get("chat")) for x in inputs if x.get("chat")}
        if any(not x.get("chat") for x in inputs):
            # Старое состояние без «chat» у части записей: где сказано, знает лента.
            # ⚠ Сравнивать РАЗМЕР множества ключей с числом событий нельзя: у любой
            # свёртки событий больше, чем веток, и полный проход по всей ленте шёл бы
            # на каждой свёртке (адверсарка 25.07).
            origins |= {str(item.get("chat_id")) for item in
                        iter_events(chat_id=chat_id, kinds={"conversation_message"})
                        if item.get("chat_id")}
        bind_place(chat_id, origins)
        meta = _write_compact(
            chat_id, result, tier=1, depth=1, source_events=source_ids, source_compacts=[],
            event_count=len(inputs), continued=continued,
            first_ts=str(inputs[0].get("ts") or ""), last_ts=str(inputs[-1].get("ts") or ""),
            manifest=(manifest if isinstance(manifest, dict) else None))
        if _discard_unprovable_compact(meta, f"свёртка места {chat_id}"):
            return {"ok": False, "folded": 0, "reason": "compact_not_provable",
                    "plan": plan, "hot": len(state.get("hot") or []),
                    "tiers": [], "degraded_tiers": []}
        episodes = _write_episodes(chat_id, meta["id"], inputs,
                                   [x for x in (result.get("episodes") or []) if isinstance(x, dict)],
                                   continued)
        # Срез по ИМЕНАМ, а не по длине: помеченные строки остаются в кольце, и
        # позиционный срез унёс бы вместе со свёрнутым куском ещё и их.
        folded_ids = {str(x.get("id")) for x in inputs}
        state["hot"] = [row for row in state["hot"]
                        if str(row.get("id")) not in folded_ids]
        state["frontier"].append(meta)
        append_event("memory_compact", chat_id=chat_id,
                     text=f"Hot → {meta['id']}; {len(inputs)} событий; {plan.get('reason')}",
                     source="memory_life", refs=source_ids,
                     meta={"compact_id": meta["id"], "tier": 1, "episodes": episodes,
                           "continued": meta["continued"], "degraded": meta["degraded"]})
        _save_state(state)
        hot_after = len(state["hot"])
        if fold_offer_enabled() and clear_fold_offer(chat_id):
            log.info("свёртка [%s] сделана (%s) — предложение снято", chat_id, plan.get("reason"))
    higher, higher_degraded = _fold_tiers_transactional(chat_id)
    return {"ok": True, "folded": fold, "compact_id": meta["id"], "episodes": episodes,
            "plan": plan, "hot": hot_after, "tiers": higher, "degraded_tiers": higher_degraded,
            "degraded": meta["degraded"],
            "folded_lines": [str(item.get("line") or "") for item in inputs]}


# Разбор свёрток места: пофайловый кэш по отпечатку жизни.
#
# Свёртка неизменяема по построению — `_write_compact` кладёт файл один раз и больше его
# не трогает. Но `_parse_place_compacts` перечитывал и перепроверял ВСЕ файлы места заново
# на каждый вызов, а вызывается он из обоих графов, то есть из каждой сборки кадра.
#
# Замер 16.09 в боевом контейнере, место `-1001240718803` (2080 файлов, 13 МБ):
#     вызов целиком                         1,25 с
#       из них чтение байтов                0,16 с
#       отпечаток (stat + sha хвоста)       0,12 с
#       разбор шапки                        0,13 с
#       выделение recap                     0,26 с
#       остальное — проверки схемы и привязки
# Стадия кадра `old_context.summary` платила это КАЖДЫЙ ход: на живом ходе она стоила
# 3,6–3,9 с, из них 1,2–1,5 с — вот этот перечитанный разбор.
#
# Ключ кэша — `memory_provenance._life_file_signature` (путь, размер, mtime_ns, инода и
# sha256 хвоста). Тот же самый, которым индекс доказательств решает, перечитывать ли файл:
# одно понятие «файл изменился» на всё дерево, а не второе своё. Отпечаток считается
# ВСЕГДА — кэш экономит разбор и проверки, но не право не смотреть на диск. Слепое пятно
# отпечатка названо в самой `_life_file_signature` и здесь не расширяется.
#
# ⚠ Цена вслух: кэш держит и тело recap — около 20 МБ на все места, из них 13 МБ Абстракт.
# Рядом с 3,4 ГБ, которые контейнер занимает и без него, это 0,6 %. Станет дорого — тела
# читаются по требованию: шапки нужны всем, тело recap — только `context_summary`.
#
# ⚠ Наружу отдаются КОПИИ: прежний разбор возвращал свежие объекты на каждый вызов, и
# `rebuild_state` кладёт эти же шапки в состояние места. Кэш, отдающий свой собственный
# словарь, однажды получил бы правку снаружи и молча раздавал бы её дальше.
_COMPACT_PARSE_CACHE: dict[str, tuple[tuple, tuple[dict, str]]] = {}
_COMPACT_PARSE_LOCK = threading.Lock()


def _copy_compact_parse(item: tuple[dict, str]) -> tuple[dict, str]:
    """Свежие объекты на каждый вызов — как их отдавал разбор с диска."""
    meta, recap = item
    if not meta:
        return {}, recap
    fresh = dict(meta)
    fresh["source_event_ids"] = list(meta["source_event_ids"])
    fresh["source_compact_ids"] = list(meta["source_compact_ids"])
    return fresh, recap


def _parse_place_compacts(chat_id: str | int) -> tuple[dict[str, tuple[dict, str]], bool]:
    """Прочитать с диска все компакты места. Самая дорогая половина — и общая для
    обоих графов, поэтому она отдельно: разбор один, фильтров два.

    Разбор каждого файла запоминается по его отпечатку (`_COMPACT_PARSE_CACHE`): свёртки
    неизменяемы, а перечитывались на каждый ход. Отказ файла запоминается тоже — иначе
    битый файл проверялся бы заново каждый раз именно потому, что он битый.
    """
    expected_chat = str(chat_id)
    parsed: dict[str, tuple[dict, str]] = {}
    legacy_seen = False
    walked: list[str] = []
    seen: set[str] = set()
    for member in _member_keys(expected_chat):
        cdir = _compact_dir(member)
        if not cdir.exists():
            continue
        walked.append(cdir.as_posix() + "/")
        for path in sorted(cdir.glob("*.md")):
            key = path.as_posix()
            seen.add(key)
            signature = memory_provenance._life_file_signature(path)
            item = None
            if signature is not None:
                with _COMPACT_PARSE_LOCK:
                    hit = _COMPACT_PARSE_CACHE.get(key)
                if hit is not None and hit[0] == signature:
                    item = _copy_compact_parse(hit[1])
            if item is None:
                item = _read_compact_candidate(path, member)
                if signature is not None:
                    with _COMPACT_PARSE_LOCK:
                        _COMPACT_PARSE_CACHE[key] = (signature, _copy_compact_parse(item))
            meta, recap = item
            if not meta:
                continue
            if meta["legacy"]:
                legacy_seen = True
                continue
            parsed[meta["id"]] = (meta, recap)
    if walked:
        # Исчезнувший файл уходит и из памяти: кэш не должен переживать своё основание.
        # Заодно уходит всё, что лежит вне нынешнего корня свёрток: стенды уводят BASE в
        # одноразовый каталог, и записи прежнего корня иначе копились бы до конца прогона.
        root = COMPACTS_DIR.as_posix() + "/"
        with _COMPACT_PARSE_LOCK:
            for stale in [k for k in _COMPACT_PARSE_CACHE
                          if not k.startswith(root)
                          or (k not in seen and any(k.startswith(d) for d in walked))]:
                del _COMPACT_PARSE_CACHE[stale]
    return parsed, legacy_seen


def _canonical_compact_graph(chat_id: str | int, *, presentable: bool = True,
                             evidence: dict | None = None,
                             parsed: tuple[dict[str, tuple[dict, str]], bool] | None = None
                             ) -> tuple[dict[str, tuple[dict, str]], bool]:
    """Return resolved non-legacy compacts bound to one place and its event spine.

    Место может состоять из нескольких ключей: пока ключ расщеплялся, свёртки одной
    комнаты писались под разными именами. Каждый компакт при этом проверяется под
    СВОИМ ключом — привязка «лежит там, где заявляет» не ослабляется ни на шаг;
    объединяется только результат.

    `evidence` и `parsed` можно передать готовыми: тогда снимок индекса и разбор
    файлов делаются ОДИН раз на весь ответ. Без этого два графа в одном вызове
    означали два обхода диска и два глобальных отпечатка — замер 21.08: три
    построения индекса на один `stream_status`, 0,96 с на 300 компактах, и путь
    ЗАПИСИ сообщения дорожал вдвое, потому что `rebuild_state` зовётся из
    `record_message` и `note_message_revision`.
    """
    parsed_pair = _parse_place_compacts(chat_id) if parsed is None else parsed
    if evidence is None:
        evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    return (_filter_compact_graph(parsed_pair[0], evidence, presentable=presentable),
            parsed_pair[1])


def _both_graphs(chat_id: str | int, *, evidence: dict | None = None
                 ) -> tuple[dict[str, tuple[dict, str]], dict[str, tuple[dict, str]], bool]:
    """(показ, покрытие, legacy) за ОДИН разбор файлов и ОДИН снимок индекса.

    Оба графа обязаны быть из одной эпохи: иначе состояние склеивается из двух
    разных моментов, а между ними успевает прийти сообщение.
    """
    parsed_pair = _parse_place_compacts(chat_id)
    if evidence is None:
        evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    return (_filter_compact_graph(parsed_pair[0], evidence, presentable=True),
            _filter_compact_graph(parsed_pair[0], evidence, presentable=False),
            parsed_pair[1])


def _filter_compact_graph(parsed: dict[str, tuple[dict, str]], evidence: dict, *,
                          presentable: bool) -> dict[str, tuple[dict, str]]:
    strict_compacts = evidence.get("compacts") or {}
    # One resolver owns the trust decision.  The local reader above is only a
    # presentation parser; it must not admit a compact that the global,
    # duplicate-aware provenance index rejected or interpreted differently.
    # ДВА ГРАФА, ДВА ВОПРОСА — её решение 21.08.
    #   presentable=True  — «можно ли это показать и процитировать»: строго к текущей
    #                       ревизии. Живой кадр, formation, обычный recall/FTS.
    #   presentable=False — «было ли это уже свёрнуто»: покрытие для пересборки.
    # Сверка разобранной шапки с индексом провенанса остаётся в ОБОИХ режимах: она про
    # то, что файл соответствует канону, а не про свежесть ревизии.
    resolve = (memory_provenance.compact_evidence if presentable
               else memory_provenance.compact_coverage)
    parsed = {
        compact_id: item
        for compact_id, item in parsed.items()
        if strict_compacts.get(compact_id) == item[0]
        and resolve(compact_id, evidence).get("valid") is True
    }
    events = evidence.get("events") or {}
    current_event_ids = set(evidence.get("current_event_ids") or ())
    resolved: dict[str, bool] = {}

    def valid(compact_id: str, stack: frozenset[str] = frozenset()) -> bool:
        if compact_id in resolved:
            return resolved[compact_id]
        if compact_id in stack or compact_id not in parsed:
            return False
        meta = parsed[compact_id][0]
        next_stack = stack | {compact_id}
        if meta["source_event_ids"]:
            ok = True
            for event_id in meta["source_event_ids"]:
                row = events.get(event_id)
                # Свежесть ревизии — единственное, что различает два режима.
                # Схема, идентичность, вид события и принадлежность месту проверяются
                # ОДИНАКОВО: покрытие не значит «принимаем что попало», оно значит
                # «правка не отменяет того, что событие уже свёрнуто».
                if (not isinstance(row, dict)
                        or row.get("schema") != "praxis.life.event.v1"
                        or row.get("id") != event_id
                        or (presentable and event_id not in current_event_ids)
                        or not _same_conversation(row.get("chat_id"), meta["chat_id"])
                        or row.get("kind") != "conversation_message"):
                    ok = False
                    break
        else:
            children = [parsed.get(parent_id) for parent_id in meta["source_compact_ids"]]
            ok = bool(children) and all(
                child is not None
                and child[0]["tier"] < meta["tier"]
                and child[0]["depth"] < meta["depth"]
                and valid(parent_id, next_stack)
                for parent_id, child in zip(meta["source_compact_ids"], children)
            )
            if ok:
                ok = meta["event_count"] == sum(
                    child[0]["event_count"] for child in children if child is not None)
        resolved[compact_id] = bool(ok)
        return bool(ok)

    return {compact_id: item for compact_id, item in parsed.items() if valid(compact_id)}


def rebuild_state(chat_id: str | int) -> dict:
    """Reconstruct hot/frontier solely from JSONL events and immutable Markdown compacts.

    Восстанавливается состояние МЕСТА: события собираются по всем его ключам, покрытыми
    считаются те, что уже вошли в любой канонический компакт места. Событие не может
    оказаться ни горячим, ни свёрнутым одновременно и не может пропасть — на этом
    инварианте держится вся миграция ключа.
    """
    place = str(place_key(chat_id))
    with _state_write_guard(place):
        return _rebuild_state_locked(place)


def _foldable_hot_rows(messages, current_event_ids, covered_events
                       ) -> tuple[list[dict], list[dict]]:
    """Горячие строки, которые ДОКАЗУЕМЫ, и отдельно те, которые нет.

    Событие, невидимое индексу доказательств, в свёртку брать нельзя: такая свёртка
    не разрешается никогда, её события не становятся покрытыми и возвращаются в
    кольцо — вызов модели в минуту без движения памяти (замер 21.09, комната
    Ouroboros: три таких события на 152 и один и тот же блок, свёрнутый заново).
    """
    foldable: list[dict] = []
    unprovable: list[dict] = []
    for row in messages:
        event_id = str(row.get("id"))
        if event_id not in current_event_ids or event_id in covered_events:
            continue
        if memory_provenance.event_row_indexable(row):
            foldable.append(row)
        else:
            unprovable.append(row)
    return foldable, unprovable


def _rebuild_state_locked(chat_id: str | int) -> dict:
    """Implementation of :func:`rebuild_state` under the exact-place state guard."""
    chat_id = str(chat_id)
    # Состав места, на котором мы сейчас пересоберём курсор, закрепляется в журнале.
    # Иначе достижимость исторических компактов из комнаты держалась бы на одном
    # реестре: он вправе уточниться, и блок молча выпал бы из её сводки — при том что
    # сам компакт остаётся каноническим (вторая адверсарка 25.07).
    bind_place(chat_id, _member_keys(chat_id))
    state = _default_state(chat_id)
    messages = iter_events(chat_id=chat_id, kinds={"conversation_message"})
    current_messages = memory_provenance.current_conversation_events(messages)
    current_event_ids = {
        str(row.get("id")) for row in current_messages if str(row.get("id") or "")
    }
    # ПОКРЫТИЕ считается по одному графу, ПОКАЗ — по другому (её решение 21.08).
    #
    # `covered_events` берётся из графа ПОКРЫТИЯ: правка одного сообщения больше не
    # возвращает в горячее кольцо остальные девяносто девять событий свёртки. Новая
    # ревизия при этом остаётся горячей сама — она не покрыта ничем.
    #
    # `frontier` берётся из СТРОГОГО графа: свёртка, чьё исходное сообщение потом
    # правили, годна как покрытие, но её recap может нести прежний текст, и в текущей
    # памяти ему не место. Такая свёртка становится `needs_refresh` — она наблюдаема
    # через `refresh_debt`, не исчезает и не считается исправной.
    presentable, coverage, legacy_seen = _both_graphs(chat_id)
    covered_metas = [item[0] for item in coverage.values()]
    shown_metas = [item[0] for item in presentable.values()]
    covered_events = {str(e) for m in covered_metas
                      for e in (m.get("source_event_ids") or [])}
    consumed_compacts = {str(c) for m in shown_metas
                         for c in (m.get("source_compact_ids") or [])}
    foldable, unprovable = _foldable_hot_rows(messages, current_event_ids, covered_events)
    # Недоказуемая строка ОСТАЁТСЯ в кольце и в живой ленте — это сказанное, и из
    # разговора его убирать нельзя (первая попытка выкинуть их из кольца уронила
    # test_keat_native_ingress: из диалога пропал кусок разбитого ответа). Она только
    # помечена, и свёртка её не возьмёт. Порядок кольца — порядок ленты, поэтому
    # собираем обратно из `messages`, а не склейкой двух списков.
    marked = {str(row.get("id")) for row in unprovable}
    kept = marked | {str(row.get("id")) for row in foldable}
    state["hot"] = _conversation_hot_rows(
        row for row in messages if str(row.get("id")) in kept)
    if unprovable:
        for row in state["hot"]:
            if str(row.get("id")) in marked:
                row["unprovable"] = True
        log.warning(
            "место %s: %d событий индекс доказательств не принимает — в ленте они "
            "остаются, в свёртку не идут (%s)", chat_id, len(unprovable),
            ", ".join(sorted(marked)[:3]))
    state["frontier"] = [m for m in shown_metas
                         if str(m.get("id")) not in consumed_compacts]
    state["dedupe"] = [{"key": r.get("dedupe_key"), "id": r.get("id")} for r in messages
                       if r.get("dedupe_key")][-400:]
    state["bootstrap_v1"] = legacy_seen or any(
        r.get("source") == "legacy_buffer" for r in messages)
    _save_state(state)
    return state


def is_state_file(path: Path) -> bool:
    """Это состояние разговора, а не чужой файл в том же каталоге?

    ⚠ `.state/life` — общий каталог. В нём лежит, например, `formation.request.json` —
    заявка Егора на переплавку, которую НИЧЕМ не восстановишь: она не производная от
    ленты. Прежний фильтр смотрел только на имя, `_safe` переписывал точку в
    подчёркивание, и заявка опознавалась как «расщеплённый ключ разговора». Смотрим
    внутрь файла: состояние — это `hot`-список и строковый `chat_id` (адверсарка 25.07).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict) and isinstance(data.get("hot"), list)
            and isinstance(data.get("chat_id"), str))


def state_keys() -> list[str]:
    """Ключи разговоров, у которых есть состояние. Чужие файлы каталога не считаются."""
    if not STATE_DIR.exists():
        return []
    return sorted(path.stem for path in STATE_DIR.glob("*.json") if is_state_file(path))


def retire_split_states(*, dry_run: bool = False) -> dict:
    """Убрать состояния, оставшиеся от расщеплённого ключа. Не удаляет — отодвигает.

    Состояние читается по ключу МЕСТА, поэтому файлы веток после миграции никто не
    открывает. Оставить их — значит держать каталог, который утверждает, что у неё 243
    разговора, когда их 35. Переносим в `_pre_places/`: производное восстанавливается
    из событий и компактов, а откат должен оставаться возможным без бэкапа.

    Вызывается миграцией явно. Ничего не двигать само на импорте — данные не переезжают
    втихую.
    """
    moved, kept = [], []
    if not STATE_DIR.exists():
        return {"moved": [], "kept": [], "dry_run": dry_run}
    attic = STATE_DIR / "_pre_places"
    for path in sorted(STATE_DIR.glob("*.json")):
        key = path.stem
        if not is_state_file(path):
            continue                      # чужой файл в общем каталоге — не наше дело
        if _safe(place_key(key)) == key:
            kept.append(key)
            continue
        moved.append(key)
        if dry_run:
            continue
        attic.mkdir(parents=True, exist_ok=True)
        try:
            path.replace(attic / path.name)
        except OSError:
            log.debug("состояние %s не отодвинулось", key, exc_info=True)
    return {"moved": moved, "kept": kept, "dry_run": dry_run}


def has_life_memory(chat_id: str | int) -> bool:
    """Есть ли у места уже современный PASS 19 контур.

    Пустой current frontier внутри такого контура — осмысленная пустота: например,
    прежняя свёртка стала stale после edit/delete. Это не повод оживлять плоскую
    legacy-summary без ревизий. Legacy fallback допустим только пока ни state, ни
    compact-tree этого места ещё не существуют.
    """
    if _state_path(chat_id).exists():
        return True
    return any(_compact_dir(member).exists() for member in _member_keys(chat_id))


def context_summary(chat_id: str | int, max_chars: int = 7000) -> str:
    """Current logarithmic frontier for the prompt. No raw event is treated as more truthful."""
    if not has_life_memory(chat_id):
        return ""  # cold pre-PASS19/test tree: do not create runtime state merely by reading
    canonical, _legacy_seen = _canonical_compact_graph(chat_id)
    consumed = {
        source_id for meta, _recap in canonical.values()
        for source_id in meta["source_compact_ids"]
    }
    frontier = sorted((item for compact_id, item in canonical.items() if compact_id not in consumed),
                      key=lambda item: (_epoch(item[0].get("first_ts"))
                                        or _epoch(item[0].get("created_at")), item[0].get("id", "")))
    blocks = []
    for meta, recap in frontier:
        quality = "DEGRADED SOURCE EXCERPT" if meta["degraded"] else "CURRENT RECAP"
        blocks.append(f"[compact {meta['id']} · tier {meta['tier']} · depth {meta['depth']}"
                      f"{' · continued' if meta['continued'] else ''} · {quality}]\n{recap}")
    if not blocks:
        return ""
    try:
        limit = max(0, int(max_chars))
    except (TypeError, ValueError):
        limit = 7000
    if limit <= len(_CONTINUITY_WARNING) + 2:
        return _CONTINUITY_WARNING[:limit]
    remaining = limit - len(_CONTINUITY_WARNING) - 2
    # ⚠ Здесь было `body[-remaining:]` — сырой посимвольный хвост склеенных блоков.
    # Под давлением бюджета сводка начиналась посреди фразы, а заголовок первого
    # компакта превращался в огрызок вроде `-8a18ea57`, который модель могла
    # процитировать как провенанс. Пакуем ЦЕЛЫМИ блоками от свежих к старым и честно
    # называем, сколько выпало, вместо того чтобы делать вид, что не выпало ничего.
    # 25.09: упаковка «свежее к старому» выкидывала первым самый ШИРОКИЙ блок — верхний
    # ярус, у которого first_ts самый ранний. Замер на AbstractDL: фронтир 22 блока,
    # 152 тыс. знаков; в кадр ехали полтора вчерашних листа, а память комнаты за два
    # месяца (tier 4, 6 493 события) — никогда. «Ты же бесокот…» — и она не знала.
    # Теперь сначала гарантирован самый широкий по охвату блок (её долгая память места),
    # остаток бюджета — свежим к старому; показ — по хронологии, выпавшее названо.
    widest = max(range(len(blocks)),
                 key=lambda i: (int(frontier[i][0].get("event_count") or 0),
                                int(frontier[i][0].get("tier") or 0), i))
    kept_idx = [widest]
    used = len(blocks[widest]) + 2
    for i in reversed(range(len(blocks))):
        if i == widest:
            continue
        if used + len(blocks[i]) + 2 > remaining:
            break
        kept_idx.append(i)
        used += len(blocks[i]) + 2
    kept_idx.sort()
    kept = [blocks[i] for i in kept_idx]
    dropped = len(blocks) - len(kept)
    head = _CONTINUITY_WARNING
    if dropped:
        head += (f"\n[СВОДКА ОБРЕЗАНА БЮДЖЕТОМ: показаны {len(kept)} компактов из "
                 f"{len(blocks)} — самый широкий по охвату и самые свежие; остальные "
                 f"есть на диске и достаются recall]")
    body = "\n\n".join(kept)
    if len(body) > remaining:      # один блок крупнее всего бюджета — режем его явно
        body = body[:max(0, remaining - 40)] + "\n…[КОМПАКТ ОБРЕЗАН]"
    return (head + "\n\n" + body).strip()


def provenance_for_path(path: str | Path) -> list[str]:
    p = Path(path)
    meta = read_artifact_meta(p)
    return [str(x) for x in (meta.get("source_event_ids") or []) +
            (meta.get("source_compact_ids") or [])]


def _refresh_digest(values: Iterable[str]) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _refresh_logical_target(row: dict) -> tuple[str, str]:
    """Логический ключ цели и ПИР, которому этот ключ принадлежит.

    Ключ и пир складываются в одном месте и отдаются вместе, потому что приём
    текущей ревизии сверяет ПИР, а не префикс уже собранной строки. Иначе формат
    ключа нельзя поменять, не уронив приём молча: замечание иммунитета к правке
    08497113 (21.09) — `startswith("telegram:{chat}:")` ещё и крал чужую строку,
    если ключ места оказывался префиксом другого пира.

    Пир пустой у событий без телеграмной родословной — такие берутся запасным
    фильтром по chat_id, как и раньше.
    """
    lineage = memory_provenance.telegram_message_key(row)
    if lineage is not None:
        return f"telegram:{lineage[0]}:{lineage[1]}", str(lineage[0])
    return f"event:{str(row.get('id') or '')}", ""


def _refresh_logical_id(row: dict) -> str:
    return _refresh_logical_target(row)[0]


def _refresh_current_targets(events: dict, current_ids, chat: str) -> dict[str, dict]:
    """Текущие ревизии ЭТОГО места, разложенные по логической цели.

    Приём решает lineage-ключ ревизии: если её пир — это же место, ревизия наша,
    даже когда строка записана через маршрут ветки (двойное delete под корнем и
    топиком, записка 21.09). Изменчивый chat_id остаётся запасным фильтром через
    `_same_conversation` — он же пускает события без телеграмной родословной.
    """
    chat = str(chat)
    current: dict[str, dict] = {}
    for event_id in current_ids:
        row = events.get(str(event_id))
        if not isinstance(row, dict) or row.get("kind") != "conversation_message":
            continue
        logical_id, peer = _refresh_logical_target(row)
        if (peer and peer == chat) or _same_conversation(row.get("chat_id"), chat):
            current[logical_id] = row
    return current


def _refresh_graph_leaves(compact_id: str, graph: dict[str, tuple[dict, str]],
                          memo: dict[str, tuple[str, ...]],
                          stack: frozenset[str] = frozenset()) -> tuple[str, ...]:
    if compact_id in memo:
        return memo[compact_id]
    if compact_id in stack or compact_id not in graph:
        return ()
    meta = graph[compact_id][0]
    direct = tuple(str(x) for x in (meta.get("source_event_ids") or ()))
    if direct:
        memo[compact_id] = direct
        return direct
    leaves: list[str] = []
    next_stack = stack | {compact_id}
    for child_id in meta.get("source_compact_ids") or ():
        leaves.extend(_refresh_graph_leaves(str(child_id), graph, memo, next_stack))
    memo[compact_id] = tuple(leaves)
    return memo[compact_id]


def _refresh_stale_subtree(root_id: str, coverage: dict[str, tuple[dict, str]],
                           stale: set[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def walk(compact_id: str) -> None:
        if compact_id in seen:
            return
        seen.add(compact_id)
        if compact_id in stale:
            ordered.append(compact_id)
        item = coverage.get(compact_id)
        if item:
            for child_id in item[0].get("source_compact_ids") or ():
                walk(str(child_id))

    walk(root_id)
    return ordered


def _refresh_roots(coverage: dict[str, tuple[dict, str]], stale: set[str]) -> list[str]:
    consumed = {
        str(child_id)
        for compact_id in stale
        for child_id in (coverage[compact_id][0].get("source_compact_ids") or ())
        if str(child_id) in stale
    }
    return sorted(stale - consumed, reverse=True)


def _refresh_candidate_positions(compact_id: str, target_pos: dict[str, int],
                                 presentable_leaves: dict[str, tuple[str, ...]]) -> set[int] | None:
    leaves = presentable_leaves.get(compact_id)
    if not leaves:
        return None
    ordered = [target_pos[event_id] for event_id in leaves if event_id in target_pos]
    if not ordered or ordered != sorted(ordered) or len(ordered) != len(set(ordered)):
        return None
    return set(ordered)


def _refresh_choose_replacements(group: dict, snapshot: dict,
                                 initial: Iterable[str] = ()) -> tuple[list[str], set[int]]:
    target_ids = group["target_ids"]
    target_pos = {event_id: index for index, event_id in enumerate(target_ids)}
    chosen: list[str] = []
    covered: set[int] = set()
    for compact_id in dict.fromkeys(initial):
        indexes = _refresh_candidate_positions(
            compact_id, target_pos, snapshot["presentable_leaves"])
        if indexes is None or indexes & covered:
            continue
        chosen.append(compact_id)
        covered.update(indexes)
    candidate_ids: set[str] = set()
    for event_id in target_ids:
        candidate_ids.update(snapshot["candidate_by_event"].get(event_id, ()))
    candidate_ids.difference_update(chosen)
    choices: list[tuple[int, int, str, set[int]]] = []
    for compact_id in candidate_ids:
        indexes = _refresh_candidate_positions(
            compact_id, target_pos, snapshot["presentable_leaves"])
        if indexes is not None:
            choices.append((-len(indexes), min(indexes), compact_id, indexes))
    for _neg_count, _first, compact_id, indexes in sorted(choices):
        if indexes & covered:
            continue
        chosen.append(compact_id)
        covered.update(indexes)
    chosen.sort(key=lambda compact_id: (
        min(_refresh_candidate_positions(
            compact_id, target_pos, snapshot["presentable_leaves"]) or {10**18}),
        compact_id,
    ))
    return chosen, covered


def _refresh_validate_receipt(row: dict, group: dict, snapshot: dict) -> dict | None:
    meta = row.get("meta")
    group_id = group["group_id"]
    allowed_keys = {
        "schema", "id", "ts", "kind", "stream", "chat_id", "actor", "direction",
        "text", "source", "source_id", "salience", "refs", "meta",
    }
    if (set(row) != allowed_keys
            or row.get("schema") != "praxis.life.event.v1"
            or not _EVENT_ID_RE.fullmatch(str(row.get("id") or ""))
            or not _UTC_MILLIS_RE.fullmatch(str(row.get("ts") or ""))
            or row.get("kind") != "memory_compact_refresh"
            or str(row.get("chat_id") or "") != snapshot["chat_id"]
            or row.get("stream") != _safe(snapshot["chat_id"])
            or row.get("actor") != "Praxis"
            or row.get("direction") != "internal"
            or row.get("source") != "memory_life"
            or row.get("salience") != 2
            or str(row.get("source_id") or "") != group_id
            or row.get("text") != f"Compact refresh group: {group_id}"
            or not isinstance(meta, dict)
            or set(meta) != _COMPACT_REFRESH_META_KEYS):
        return None
    replacements = meta.get("replacement_compact_ids")
    if (meta.get("schema") != _COMPACT_REFRESH_SCHEMA
            or meta.get("group_id") != group_id
            or meta.get("coverage_root_ids") != group["root_ids"]
            or meta.get("stale_compact_ids") != group["stale_ids"]
            or type(meta.get("logical_target_count")) is not int
            or meta.get("logical_target_count") != len(group["logical_ids"])
            or meta.get("logical_target_ids_sha256") != _refresh_digest(group["logical_ids"])
            or type(meta.get("target_event_count")) is not int
            or meta.get("target_event_count") != len(group["target_ids"])
            or meta.get("target_event_ids_sha256") != _refresh_digest(group["target_ids"])
            or type(replacements) is not list or not replacements
            or any(type(item) is not str or not _COMPACT_ID_RE.fullmatch(item)
                   for item in replacements)
            or len(replacements) != len(set(replacements))
            or row.get("refs") != group["stale_ids"] + replacements):
        return None
    # Validate the exact structural choice the writer could have made when this receipt
    # was appended.  Later strict compacts must not retroactively invalidate an older
    # receipt, so candidates whose immutable id timestamp is newer than the receipt are
    # excluded.  This proves canonical shape, not cryptographic caller identity.
    receipt_stamp = str(row["id"]).split("-", 2)[1]
    historical = dict(snapshot)
    historical["candidate_by_event"] = {
        event_id: {
            compact_id for compact_id in compact_ids
            if compact_id.split("-", 2)[1] <= receipt_stamp
        }
        for event_id, compact_ids in snapshot["candidate_by_event"].items()
    }
    initial = list((group.get("receipt") or {}).get("replacement_compact_ids") or ())
    canonical, _canonical_covered = _refresh_choose_replacements(
        group, historical, initial)
    if replacements != canonical:
        return None
    target_pos = {event_id: index for index, event_id in enumerate(group["target_ids"])}
    covered: set[int] = set()
    previous_first = -1
    for compact_id in replacements:
        indexes = _refresh_candidate_positions(
            compact_id, target_pos, snapshot["presentable_leaves"])
        if indexes is None or indexes & covered:
            return None
        first = min(indexes)
        # The writer emits replacement chunks in logical-target order. We require that
        # stable shape, but deliberately do not require one globally optimal partition:
        # a later strict compact must not retroactively invalidate an older receipt.
        if first <= previous_first:
            return None
        previous_first = first
        covered.update(indexes)
    return {
        "receipt_event_id": str(row.get("id") or ""),
        "replacement_compact_ids": list(replacements),
        "covered_positions": covered,
        "refreshed_count": len(covered),
        "remaining_count": len(group["target_ids"]) - len(covered),
        "ts": str(row.get("ts") or ""),
    }


def _refresh_snapshot(chat_id: str, *, evidence: dict | None = None) -> dict:
    """One near-linear exact view of stale logical groups and append-only receipts."""
    chat = str(place_key(chat_id))
    evidence = evidence or memory_provenance.claim_evidence_index(MEM_DIR)
    presentable, coverage, _legacy = _both_graphs(chat, evidence=evidence)
    events = evidence.get("events") or {}
    current_ids = set(str(x) for x in (evidence.get("current_event_ids") or ()))
    current_by_logical = _refresh_current_targets(events, current_ids, chat)

    presentable_leaves: dict[str, tuple[str, ...]] = {}
    coverage_leaves: dict[str, tuple[str, ...]] = {}
    candidate_by_event: dict[str, set[str]] = {}
    for compact_id in presentable:
        leaves = _refresh_graph_leaves(compact_id, presentable, presentable_leaves)
        for event_id in set(leaves):
            candidate_by_event.setdefault(event_id, set()).add(compact_id)

    stale = set(coverage) - set(presentable)
    roots = _refresh_roots(coverage, stale)
    infos: list[dict] = []
    identity_owner: dict[str, int] = {}
    parent = list(range(len(roots)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for index, root_id in enumerate(roots):
        leaves = _refresh_graph_leaves(root_id, coverage, coverage_leaves)
        logical_ids: list[str] = []
        seen: set[str] = set()
        error = ""
        for event_id in leaves:
            row = events.get(event_id)
            if not isinstance(row, dict):
                error = "missing_coverage_leaf"
                break
            logical_id = _refresh_logical_id(row)
            if logical_id not in seen:
                seen.add(logical_id)
                logical_ids.append(logical_id)
                if logical_id in identity_owner:
                    union(index, identity_owner[logical_id])
                else:
                    identity_owner[logical_id] = index
        meta = coverage[root_id][0]
        infos.append({"root_id": root_id, "logical_ids": logical_ids, "error": error,
                      "order": (str(meta.get("first_ts") or ""),
                                str(meta.get("created_at") or ""), root_id)})

    components: dict[int, list[dict]] = {}
    for index, info in enumerate(infos):
        components.setdefault(find(index), []).append(info)
    groups: dict[str, dict] = {}
    artifact_group: dict[str, str] = {}
    for component in components.values():
        component.sort(key=lambda item: item["order"])
        logical_ids: list[str] = []
        seen_logical: set[str] = set()
        errors = [item["error"] for item in component if item["error"]]
        for item in component:
            for logical_id in item["logical_ids"]:
                if logical_id not in seen_logical:
                    seen_logical.add(logical_id)
                    logical_ids.append(logical_id)
        target_rows: list[dict] = []
        if not errors:
            for logical_id in logical_ids:
                row = current_by_logical.get(logical_id)
                if not isinstance(row, dict):
                    errors.append("current_projection_unavailable")
                    break
                target_rows.append(row)
        if not logical_ids:
            errors.append("empty_current_projection")
        root_ids = [item["root_id"] for item in component]
        stale_ids: list[str] = []
        seen_stale: set[str] = set()
        for root_id in root_ids:
            for compact_id in _refresh_stale_subtree(root_id, coverage, stale):
                if compact_id not in seen_stale:
                    seen_stale.add(compact_id)
                    stale_ids.append(compact_id)
        group_id = _refresh_digest(logical_ids)
        group = {
            "group_id": group_id, "root_ids": root_ids, "stale_ids": stale_ids,
            "logical_ids": logical_ids, "target_rows": target_rows,
            "target_ids": [str(row.get("id") or "") for row in target_rows],
            "target_error": errors[0] if errors else "", "receipt": {},
        }
        groups[group_id] = group
        for compact_id in stale_ids:
            artifact_group[compact_id] = group_id

    snapshot = {
        "chat_id": chat, "evidence": evidence, "events": events,
        "current_ids": current_ids, "presentable": presentable, "coverage": coverage,
        "presentable_leaves": presentable_leaves, "coverage_leaves": coverage_leaves,
        "candidate_by_event": candidate_by_event, "stale": stale, "groups": groups,
        "artifact_group": artifact_group,
    }
    receipts_by_group: dict[str, list[dict]] = {}
    for row in events.values():
        if isinstance(row, dict) and row.get("kind") == "memory_compact_refresh":
            receipts_by_group.setdefault(str(row.get("source_id") or ""), []).append(row)
    resolved_stale: set[str] = set()
    for group_id, group in groups.items():
        valid_receipts: list[dict] = []
        for row in sorted(receipts_by_group.get(group_id, ()),
                          key=lambda item: str(item.get("id") or "")):
            validation_group = dict(group)
            if valid_receipts:
                validation_group["receipt"] = max(
                    valid_receipts,
                    key=lambda item: (item["refreshed_count"], item["receipt_event_id"]),
                )
            receipt = _refresh_validate_receipt(row, validation_group, snapshot)
            if receipt is not None:
                valid_receipts.append(receipt)
        if valid_receipts:
            group["receipt"] = max(
                valid_receipts,
                key=lambda item: (item["refreshed_count"], item["receipt_event_id"]),
            )
        initial = list(group["receipt"].get("replacement_compact_ids") or ())
        replacements, covered = _refresh_choose_replacements(group, snapshot, initial)
        group["replacement_compact_ids"] = replacements
        group["covered_positions"] = covered
        group["refreshed_count"] = len(covered)
        group["remaining_count"] = len(group["target_ids"]) - len(covered)
        group["receipt_complete"] = bool(
            group["receipt"] and group["receipt"].get("remaining_count") == 0)
        if group["receipt_complete"]:
            resolved_stale.update(group["stale_ids"])
    snapshot["resolved_stale"] = resolved_stale
    snapshot["unresolved_groups"] = [
        group for group in groups.values() if not group["receipt_complete"]]
    snapshot["unresolved_artifacts"] = stale - resolved_stale
    return snapshot


def _append_refresh_receipt(chat_id: str, group: dict,
                            replacement_ids: list[str]) -> dict:
    meta = {
        "schema": _COMPACT_REFRESH_SCHEMA,
        "group_id": group["group_id"],
        "coverage_root_ids": list(group["root_ids"]),
        "stale_compact_ids": list(group["stale_ids"]),
        "logical_target_count": len(group["logical_ids"]),
        "logical_target_ids_sha256": _refresh_digest(group["logical_ids"]),
        "target_event_count": len(group["target_ids"]),
        "target_event_ids_sha256": _refresh_digest(group["target_ids"]),
        "replacement_compact_ids": list(replacement_ids),
    }
    return append_event(
        "memory_compact_refresh", chat_id=chat_id,
        text=f"Compact refresh group: {group['group_id']}", source="memory_life",
        source_id=group["group_id"], refs=group["stale_ids"] + replacement_ids,
        meta=meta,
    )


def _refresh_group(snapshot: dict, group_id: str) -> dict | None:
    group = snapshot["groups"].get(group_id)
    return group if isinstance(group, dict) else None


def _refresh_api_result(snapshot: dict, group: dict, *, reason: str, ok: bool,
                        chunk_limit: int, chunks_written: int = 0,
                        new_compact_ids: list[str] | None = None,
                        receipt: dict | None = None, scope: str = "target_group") -> dict:
    return {
        "ok": ok, "reason": reason, "scope": scope,
        "group_id": group.get("group_id"), "coverage_root_ids": group.get("root_ids", []),
        "max_chunks": chunk_limit, "chunks_written": chunks_written,
        "new_compact_ids": list(new_compact_ids or ()),
        "receipt_written": receipt is not None,
        "receipt_event_id": str((receipt or group.get("receipt") or {}).get(
            "id") or (group.get("receipt") or {}).get("receipt_event_id") or ""),
        "target_count": len(group.get("target_ids") or ()),
        "refreshed_count": int(group.get("refreshed_count") or 0),
        "remaining_count": int(group.get("remaining_count") or 0),
        "target_remaining_count": int(group.get("remaining_count") or 0),
        "replacement_compact_ids": list(group.get("replacement_compact_ids") or ()),
        "room_unresolved_group_count": len(snapshot.get("unresolved_groups") or ()),
        "room_needs_refresh": len(snapshot.get("unresolved_artifacts") or ()),
    }


def refresh_compacts(chat_id: str | int, compact_id: str | None = None, *,
                     max_chunks: int = 1) -> dict:
    """Boundedly refresh one logical stale group; evaluator work stays unlocked.

    An explicitly addressed stale compact is a repair of existing canonical
    material: evaluator failure or a degraded result aborts the whole invocation
    before any replacement compact or refresh receipt is written.  Ordinary
    background refresh retains its deterministic fallback.
    """
    try:
        requested_chunks = int(max_chunks)
    except (TypeError, ValueError):
        requested_chunks = 1
    chunk_limit = max(0, min(_REFRESH_MAX_CHUNKS, requested_chunks))
    chat = str(place_key(chat_id))
    initial = _refresh_snapshot(chat)
    target = str(compact_id or "")
    scope = "target_group" if target else "one_group"
    if target:
        if not _COMPACT_ID_RE.fullmatch(target) or target not in initial["coverage"]:
            return {"ok": False, "reason": "compact_not_in_coverage", "scope": scope,
                    "compact_id": target, "max_chunks": chunk_limit,
                    "room_unresolved_group_count": len(initial["unresolved_groups"]),
                    "room_needs_refresh": len(initial["unresolved_artifacts"])}
        group_id = initial["artifact_group"].get(target)
        if not group_id:
            return {"ok": True, "reason": "compact_is_presentable", "scope": scope,
                    "compact_id": target, "max_chunks": chunk_limit,
                    "room_unresolved_group_count": len(initial["unresolved_groups"]),
                    "room_needs_refresh": len(initial["unresolved_artifacts"])}
        group = initial["groups"][group_id]
    elif initial["unresolved_groups"]:
        group = max(initial["unresolved_groups"],
                    key=lambda item: max(item.get("root_ids") or [""]))
        group_id = group["group_id"]
    else:
        return {"ok": True, "reason": "no_refresh_debt", "scope": scope,
                "chunks_written": 0, "receipt_written": False,
                "max_chunks": chunk_limit, "room_unresolved_group_count": 0,
                "room_needs_refresh": 0}
    if group["target_error"]:
        return _refresh_api_result(initial, group, reason=group["target_error"], ok=False,
                                   chunk_limit=chunk_limit, scope=scope)
    if group["receipt_complete"]:
        # A receipt is durable before the rebuildable cursor. Retrying after a crash must
        # repair that cursor instead of returning success over a double-present hot row.
        rebuild_state(chat)
        reconciled = _refresh_snapshot(chat)
        reconciled_group = _refresh_group(reconciled, group_id) or group
        return _refresh_api_result(reconciled, reconciled_group,
                                   reason="already_refreshed", ok=True,
                                   chunk_limit=chunk_limit, scope=scope)

    virtual_covered = set(group["covered_positions"])
    planned: list[tuple[dict, list[dict], bool]] = []
    for _ in range(chunk_limit):
        remaining_rows = [row for index, row in enumerate(group["target_rows"])
                          if index not in virtual_covered]
        if not remaining_rows:
            break
        fit = budget_prefix(remaining_rows, budget=PROMPT_BUDGET_CHARS)
        inputs = remaining_rows[:fit]
        if not inputs:
            break
        continued = fit < len(remaining_rows)
        result = _model_compact(inputs, tier=1, depth=1, continued=continued)
        model_result_valid = (
            isinstance(result, dict)
            and isinstance(result.get("summary"), str)
            and bool(result["summary"].strip())
            and not bool(result.get("degraded"))
        )
        if not model_result_valid:
            if target:
                return _refresh_api_result(
                    initial, group, reason="model_required", ok=False,
                    chunk_limit=chunk_limit, scope=scope,
                )
            result = _fallback_compact(inputs, continued=continued)
        planned.append((result, [dict(row) for row in inputs], continued))
        planned_ids = {str(row.get("id") or "") for row in inputs}
        virtual_covered.update(
            index for index, event_id in enumerate(group["target_ids"])
            if event_id in planned_ids)

    made: list[str] = []
    receipt = None
    # Refresh planning/model work remains outside state transactions.  The commit must,
    # however, share the exact per-place writer domain with ordinary hot compaction so
    # the two paths cannot both derive and persist overlapping compacts for one event.
    with _refresh_commit_guard(chat), _state_write_guard(chat):
        current = _refresh_snapshot(chat)
        current_group = _refresh_group(current, group_id)
        if (current_group is None
                or current_group["logical_ids"] != group["logical_ids"]
                or current_group["target_ids"] != group["target_ids"]):
            return _refresh_api_result(current, current_group or group,
                                       reason="target_changed", ok=False,
                                       chunk_limit=chunk_limit, scope=scope)
        if current_group["receipt_complete"]:
            rebuild_state(chat)
            observed = _refresh_snapshot(chat)
            observed_group = _refresh_group(observed, group_id) or current_group
            return _refresh_api_result(observed, observed_group,
                                       reason="already_refreshed", ok=True,
                                       chunk_limit=chunk_limit, scope=scope)

        covered_ids = {
            current_group["target_ids"][index]
            for index in current_group["covered_positions"]
        }
        for result, inputs, continued in planned:
            source_ids = [str(item.get("id") or "") for item in inputs]
            if any(event_id in covered_ids for event_id in source_ids):
                continue
            timestamps = [str(item.get("ts") or "") for item in inputs]
            # Привязка пишется ДО компакта — как в горячем пути компакции:
            # refresh собирает события со ВСЕХ веток места (группа строится по
            # логическим целям, а не по ключу ветки), и событие под веточным
            # ключом без записи в places.json делает весь свежий чанк
            # неканоническим с первой секунды. Мельница писала чанки, которые
            # сама же отвергала: needs_refresh стоял, refreshed_count не рос
            # (пробка группы e0b1e8ed, 21.09: 75 целей, 0 refreshed, 10 проходов).
            origins = {str(item.get("chat_id") or "") for item in inputs
                       if item.get("chat_id")}
            if origins:
                bind_place(chat, origins)
            with _WRITE_LOCK:
                meta = _write_compact(
                    chat, result, tier=1, depth=1, source_events=source_ids,
                    source_compacts=[], event_count=len(source_ids), continued=continued,
                    source_note=(f"Адресный refresh logical-group {group_id}; "
                                 "только текущие события."),
                    first_ts=min(timestamps), last_ts=max(timestamps),
                    manifest=(result.get("_manifest") if isinstance(result, dict) else None),
                )
            made.append(str(meta["id"]))
            covered_ids.update(source_ids)

        written = _refresh_snapshot(chat)
        written_group = _refresh_group(written, group_id)
        if (written_group is None
                or written_group["logical_ids"] != group["logical_ids"]
                or written_group["target_ids"] != group["target_ids"]):
            return _refresh_api_result(written, written_group or current_group,
                                       reason="target_changed", ok=False,
                                       chunk_limit=chunk_limit, chunks_written=len(made),
                                       new_compact_ids=made, scope=scope)
        replacements = list(written_group["replacement_compact_ids"])
        prior = list((written_group.get("receipt") or {}).get(
            "replacement_compact_ids") or ())
        if replacements and replacements != prior:
            receipt = _append_refresh_receipt(chat, written_group, replacements)

        final = _refresh_snapshot(chat)
        final_group = _refresh_group(final, group_id)
        if (final_group is None
                or final_group["logical_ids"] != group["logical_ids"]
                or final_group["target_ids"] != group["target_ids"]):
            return _refresh_api_result(final, final_group or written_group,
                                       reason="target_changed", ok=False,
                                       chunk_limit=chunk_limit, chunks_written=len(made),
                                       new_compact_ids=made, receipt=receipt, scope=scope)
        if made or receipt is not None:
            rebuild_state(chat)
        observed = _refresh_snapshot(chat)
        observed_group = _refresh_group(observed, group_id)
        if (observed_group is None
                or observed_group["logical_ids"] != group["logical_ids"]
                or observed_group["target_ids"] != group["target_ids"]):
            return _refresh_api_result(observed, observed_group or final_group,
                                       reason="target_changed", ok=False,
                                       chunk_limit=chunk_limit, chunks_written=len(made),
                                       new_compact_ids=made, receipt=receipt, scope=scope)

    reason = ("target_refreshed" if observed_group["receipt_complete"]
              else "target_progress")
    return _refresh_api_result(observed, observed_group, reason=reason, ok=True,
                               chunk_limit=chunk_limit, chunks_written=len(made),
                               new_compact_ids=made, receipt=receipt, scope=scope)


def refresh_debt(chat_id: str | int, *, evidence: dict | None = None) -> dict:
    """Exact unresolved stale artifacts, grouped by their current logical target."""
    snapshot = _refresh_snapshot(str(place_key(chat_id)), evidence=evidence)
    stale = sorted(snapshot["unresolved_artifacts"], reverse=True)
    rows = []
    for compact_id in stale[:_REFRESH_SAMPLE]:
        group = snapshot["groups"][snapshot["artifact_group"][compact_id]]
        leaves = snapshot["coverage_leaves"].get(compact_id) or ()
        superseded = [event_id for event_id in leaves
                      if event_id not in snapshot["current_ids"]]
        replacements = list(group["replacement_compact_ids"])
        rows.append({
            "compact_id": compact_id,
            "coverage_root_id": group["root_ids"][0] if group["root_ids"] else compact_id,
            "coverage_root_ids": list(group["root_ids"]),
            "group_id": group["group_id"],
            "reason": "superseded_source_revision" if superseded else "not_presentable",
            "superseded_event_ids": superseded[:_REFRESH_SAMPLE],
            "superseded_total": len(superseded),
            "superseded_truncated": len(superseded) > _REFRESH_SAMPLE,
            "target_count": len(group["target_ids"]),
            "refreshed_count": group["refreshed_count"],
            "remaining_count": group["remaining_count"],
            "replacement_compact_ids": replacements[:_REFRESH_SAMPLE],
            "replacement_total": len(replacements),
            "replacement_truncated": len(replacements) > _REFRESH_SAMPLE,
        })
    return {
        "chat_id": snapshot["chat_id"],
        "covered": len(snapshot["coverage"]),
        "presentable": len(snapshot["presentable"]),
        "needs_refresh": len(stale),
        "resolved_stale": len(snapshot["resolved_stale"]),
        "unresolved_group_count": len(snapshot["unresolved_groups"]),
        "sample": rows, "sample_order": "newest_first",
        "sample_truncated": len(stale) > _REFRESH_SAMPLE,
    }


def _state_status(chat_id: str | int, *, state: dict | None = None) -> dict:
    """Дешёвая агрегатная телеметрия из уже сохранённого курсора.

    `status()` обслуживает панель по сотням streams и не должен синхронно доказывать
    каждый compact. Exact covered/presentable/needs_refresh остаются в адресных
    `stream_status()` / `refresh_debt()`.
    """
    state = state if isinstance(state, dict) else _load_state(chat_id, rebuild=True)
    tiers: dict[str, int] = {}
    for item in state.get("frontier") or []:
        key = str(item.get("tier") or 1)
        tiers[key] = tiers.get(key, 0) + 1
    return {"chat_id": str(chat_id), "hot": len(state.get("hot") or []),
            "hot_tokens": sum(int(x.get("tokens") or 0) for x in state.get("hot") or []),
            "frontier": len(state.get("frontier") or []), "tiers": tiers,
            "bootstrap": bool(state.get("bootstrap_v1"))}


def stream_status(chat_id: str | int, *, evidence: dict | None = None) -> dict:
    if evidence is None:
        evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    out = _state_status(chat_id)
    debt = refresh_debt(chat_id, evidence=evidence)
    out["compacts"] = {"covered": debt["covered"],
                       "presentable": debt["presentable"],
                       "needs_refresh": debt["needs_refresh"]}
    return out


def status() -> dict:
    streams = []
    for p in sorted(STATE_DIR.glob("*.json")) if STATE_DIR.exists() else []:
        if p.name in ("formation.json", "formation.request.json"):
            continue
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            streams.append(_state_status(st.get("chat_id") or p.stem, state=st))
        except Exception:
            continue
    events = iter_events()
    return {"events": len(events), "streams": streams,
            "compacts": len(list(COMPACTS_DIR.glob("*/*.md"))) if COMPACTS_DIR.exists() else 0,
            "episodes": len(list(EPISODES_DIR.glob("*/*.md"))) if EPISODES_DIR.exists() else 0,
            "hot_lo": HOT_LO, "hot_hi": HOT_HI, "token_cap": HOT_TOKEN_CAP}


def _cli() -> None:
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "bootstrap":
        print(json.dumps(bootstrap_all(), ensure_ascii=False, indent=2))
    elif cmd == "rebuild":
        chats = sys.argv[2:] or [x.get("chat_id") for x in status().get("streams", [])]
        print(json.dumps([stream_status(c) if rebuild_state(c) else {} for c in chats],
                         ensure_ascii=False, indent=2))
    elif cmd == "compact":
        if len(sys.argv) < 3:
            raise SystemExit("usage: python -m memory_life compact <chat_id> [--force]")
        print(json.dumps(compact_if_due(sys.argv[2], force="--force" in sys.argv),
                         ensure_ascii=False, indent=2))
    elif cmd == "refresh":
        if len(sys.argv) < 3:
            raise SystemExit(
                "usage: python -m memory_life refresh <chat_id> [compact_id] [--chunks N]")
        args = list(sys.argv[3:])
        chunks = 1
        if "--chunks" in args:
            index = args.index("--chunks")
            if index + 1 >= len(args):
                raise SystemExit("--chunks requires an integer")
            try:
                chunks = int(args[index + 1])
            except ValueError as exc:
                raise SystemExit("--chunks requires an integer") from exc
            del args[index:index + 2]
        if len(args) > 1 or any(arg.startswith("--") for arg in args):
            raise SystemExit(
                "usage: python -m memory_life refresh <chat_id> [compact_id] [--chunks N]")
        print(json.dumps(refresh_compacts(sys.argv[2], args[0] if args else None,
                                          max_chunks=chunks),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()



# ── 25.09: её руки на своих свёртках — list / read / rewrite / refold ────────────────────
#
# Свёртка — её слово: переписать можно любую, на месте и под тем же id. Шапка (источники,
# границы, ярус) не трогается, поэтому разрешение принимает переписанную свёртку без
# спора, а родители, ссылающиеся на неё, остаются целыми. Прежний текст уходит в историю
# (`memory/life/compacts/_history/<место>/<id>.<время>.md`) — переписать не значит стереть.
# `refold` — перевыпуск пачкой в фоне её же голосом: те же входы, что у обычной свёртки
# (листья — из текущих событий, ярусы — из детей), результат ложится на место.

def _history_dir():
    return COMPACTS_DIR / "_history"


def _refold_state_path():
    return MEM_DIR / ".state" / "refold.json"


_REFOLD_LOCK = threading.Lock()
_REFOLD: dict = {"running": False, "stop": False}
_SECTION_RE = re.compile(r"(?m)^(?=## )")


def _compact_row(meta: dict) -> dict:
    recap = compact_text(str(meta.get("id") or ""), meta.get("chat_id"))  # уже «Суть»
    return {
        "id": str(meta.get("id") or ""), "chat_id": str(meta.get("chat_id") or ""),
        "tier": int(meta.get("tier") or 1), "depth": int(meta.get("depth") or 1),
        "first_ts": str(meta.get("first_ts") or ""), "last_ts": str(meta.get("last_ts") or ""),
        "events": int(meta.get("event_count") or 0), "degraded": bool(meta.get("degraded")),
        "legacy": bool(meta.get("legacy")), "chars": len(recap),
        "head": " ".join(recap.split())[:160],
    }


def _place_compacts(chat_id: str | int | None, evidence: dict) -> list[dict]:
    compacts = evidence.get("compacts") or {}
    if chat_id is None or str(chat_id) == "all":
        rows = list(compacts.values())
    else:
        place = str(place_key(chat_id))
        rows = [meta for meta in compacts.values()
                if memory_provenance.same_conversation(meta.get("chat_id"), place,
                                                       evidence.get("places"))]
    rows.sort(key=lambda m: (int(m.get("tier") or 1), str(m.get("first_ts") or ""),
                             str(m.get("id") or "")))
    return rows


def list_compacts(chat_id: str | int | None, *, tier: int | None = None, limit: int = 20,
                  since: str = "") -> list[dict]:
    """Свёртки места (или все при chat_id=all): ярус, обхват, первые слова сути."""
    evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    rows = _place_compacts(chat_id, evidence)
    if tier is not None:
        rows = [m for m in rows if int(m.get("tier") or 1) == int(tier)]
    if since:
        rows = [m for m in rows if str(m.get("last_ts") or "") >= str(since)]
    rows.sort(key=lambda m: (str(m.get("first_ts") or ""), str(m.get("id") or "")))
    if limit and limit > 0:
        rows = rows[-int(limit):]
    return [_compact_row(meta) for meta in rows]


def read_compact(compact_id: str, chat_id: str | int | None = None) -> dict:
    """Одна свёртка целиком: шапка, суть и полный текст файла."""
    candidates = _compact_candidates(compact_id, chat_id)
    if len(candidates) != 1:
        return {"ok": False, "reason": "unknown_compact"}
    meta, recap = candidates[0]
    try:
        text = (BASE / str(meta.get("path"))).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {"ok": False, "reason": "compact_unreadable"}
    return {"ok": True, "meta": _compact_row(meta), "recap": recap, "text": text}


def _replace_recap(text: str, summary: str, open_threads: list[str] | None) -> str:
    """Тот же файл с новой «Сутью» (и, если даны, новыми «Открытыми нитями»); остальное — как было."""
    chunks = _SECTION_RE.split(text)
    head, sections = chunks[0], chunks[1:]
    out: list[str] = []
    seen_recap = seen_threads = False
    threads_block = None
    if open_threads is not None:
        rows = [str(x).strip() for x in open_threads if str(x).strip()]
        threads_block = ("## Открытые нити\n" + "\n".join(f"- {x}" for x in rows) + "\n\n") if rows else ""
    for section in sections:
        title = section.split("\n", 1)[0].strip()
        if title == "## Суть":
            out.append("## Суть\n" + summary.strip() + "\n\n")
            seen_recap = True
            if threads_block is not None and not seen_threads:
                if threads_block:
                    out.append(threads_block)
                seen_threads = True
            continue
        if title == "## Открытые нити":
            if threads_block is None:
                out.append(section if section.endswith("\n") else section + "\n")
            seen_threads = True
            continue
        out.append(section if section.endswith("\n\n") else section.rstrip("\n") + "\n\n")
    if not seen_recap:
        raise ValueError("compact without recap section")
    body = head + "".join(out)
    return body.rstrip() + "\n"


def rewrite_compact_text(compact_id: str, summary: str, *, chat_id: str | int | None = None,
                         open_threads: list[str] | None = None, why: str = "her_word") -> dict:
    """Переписать суть свёртки на месте: тот же id, та же шапка, прежний текст — в историю."""
    summary = str(summary or "").strip()
    if not summary:
        return {"ok": False, "reason": "empty_summary"}
    candidates = _compact_candidates(compact_id, chat_id)
    if len(candidates) != 1:
        return {"ok": False, "reason": "unknown_compact"}
    meta, _old_recap = candidates[0]
    chat = str(meta.get("chat_id") or "")
    place = str(place_key(chat))
    path = BASE / str(meta.get("path"))
    stamp = _utc_iso().replace("-", "").replace(":", "")
    history = _history_dir() / _safe(chat) / f"{compact_id}.{stamp}.md"
    with _state_write_guard(place), _WRITE_LOCK:
        # 25.09 (ревью V4 F13): читать ПОД замком — иначе два переписывания одного id
        # (рука и фоновый refold) теряют одно: второе кладёт в историю свой старый текст.
        try:
            old_text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return {"ok": False, "reason": "compact_unreadable"}
        try:
            new_text = _replace_recap(old_text, summary, open_threads)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(old_text, encoding="utf-8")
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
        fresh_meta, fresh_recap = _read_compact_candidate(path, chat)
        if not fresh_meta or fresh_meta != meta or not fresh_recap:
            # Шапка обязана остаться прежней байт в байт — иначе откат, свёртка не меняется.
            path.write_text(old_text, encoding="utf-8")
            return {"ok": False, "reason": "header_changed"}
        history_rel = history.relative_to(BASE).as_posix()
        append_event(
            "memory_compact", chat_id=chat,
            text=f"Свёртка {compact_id} переписана своими словами ({why}); {len(summary)} знаков",
            source="memory_life", refs=[str(compact_id)],
            meta={"compact_id": str(compact_id), "tier": int(meta.get("tier") or 1),
                  "rewritten": True, "why": why, "history": history_rel})
    return {"ok": True, "id": str(compact_id), "chat_id": chat, "chars": len(summary),
            "history": history_rel}


def refold_plan(chat_id: str | int | None = "all", *, tier: int | None = None,
                since: str = "", limit: int = 0) -> list[tuple[str, str]]:
    """Что перевыпускать: листья раньше родителей, старое раньше нового. Без записи."""
    evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    rows = _place_compacts(chat_id, evidence)
    plan: list[tuple[str, str]] = []
    for meta in rows:
        if meta.get("legacy"):
            continue
        if not (meta.get("source_event_ids") or meta.get("source_compact_ids")):
            continue
        if tier is not None and int(meta.get("tier") or 1) != int(tier):
            continue
        if since and str(meta.get("last_ts") or "") < str(since):
            continue
        plan.append((str(meta.get("chat_id") or ""), str(meta.get("id") or "")))
    if limit and limit > 0:
        plan = plan[:int(limit)]
    return plan


def refold_one(chat_id: str | int, compact_id: str) -> dict:
    """Перевыпустить одну свёртку её голосом на месте: те же входы, что у обычной свёртки."""
    chat = str(chat_id)
    evidence = memory_provenance.claim_evidence_index(MEM_DIR)
    compacts = evidence.get("compacts") or {}
    meta = compacts.get(str(compact_id))
    if not isinstance(meta, dict):
        return {"ok": False, "reason": "unknown_compact"}
    children = [str(x) for x in (meta.get("source_compact_ids") or [])]
    source_ids = [str(x) for x in (meta.get("source_event_ids") or [])]
    if children:
        if any(not isinstance(compacts.get(ch), dict) for ch in children):
            return {"ok": False, "reason": "sources_missing"}
        inputs = [_frontier_input(dict(compacts[ch], id=ch), chat) for ch in children]
        if any(not str(item.get("text") or "").strip() for item in inputs):
            return {"ok": False, "reason": "sources_missing"}
        subtree = _subtree_event_rows(evidence, str(compact_id))
    elif source_ids:
        rows = [(evidence.get("events") or {}).get(eid) for eid in source_ids]
        if any(not isinstance(row, dict) for row in rows):
            return {"ok": False, "reason": "sources_missing"}
        current_ids = set(evidence.get("current_event_ids") or ())
        inputs = _conversation_hot_rows(rows)
        if len(inputs) != len(source_ids) or any(eid not in current_ids for eid in source_ids):
            # Часть источников вытеснена поздней ревизией — это работа refresh_compacts.
            return {"ok": False, "reason": "sources_not_current"}
        subtree = inputs
    else:
        return {"ok": False, "reason": "no_sources"}
    authors = _window_authors(subtree)
    result = _model_compact(inputs, tier=int(meta.get("tier") or 1),
                            depth=int(meta.get("depth") or 1),
                            continued=bool(meta.get("continued")), authors=authors)
    summary = str((result or {}).get("summary") or "").strip()
    if not summary or (result or {}).get("degraded"):
        return {"ok": False, "reason": "model_unavailable"}
    threads = result.get("open_threads") if isinstance(result.get("open_threads"), list) else None
    return rewrite_compact_text(str(compact_id), summary, chat_id=chat,
                                open_threads=threads, why="refold")


def _save_refold_state() -> None:
    try:
        state_path = _refold_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_REFOLD, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(state_path)
    except OSError:
        log.debug("refold: состояние не записалось", exc_info=True)


def refold_status() -> dict:
    with _REFOLD_LOCK:
        return dict(_REFOLD)


def refold_stop() -> dict:
    with _REFOLD_LOCK:
        if not _REFOLD.get("running"):
            return {"ok": True, "running": False}
        _REFOLD["stop"] = True
        _save_refold_state()
        return {"ok": True, "running": True, "stopping": True}


def _refold_worker(plan: list[tuple[str, str]], pause_sec: float) -> None:
    for chat, cid in plan:
        if _REFOLD.get("stop"):
            break
        try:
            out = refold_one(chat, cid)
        except Exception as exc:
            log.exception("refold %s: не вышло", cid)
            out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
        with _REFOLD_LOCK:
            if out.get("ok"):
                _REFOLD["done"] = int(_REFOLD.get("done") or 0) + 1
            elif out.get("reason") in ("sources_not_current", "no_sources", "sources_missing"):
                _REFOLD["skipped"] = int(_REFOLD.get("skipped") or 0) + 1
            else:
                _REFOLD["failed"] = int(_REFOLD.get("failed") or 0) + 1
            _REFOLD["last"] = {"id": cid, "chat_id": chat, **{k: out.get(k) for k in ("ok", "reason")}}
            _save_refold_state()
        if pause_sec > 0:
            time.sleep(pause_sec)
    with _REFOLD_LOCK:
        _REFOLD["running"] = False
        _REFOLD["finished_at"] = _utc_iso()
        _save_refold_state()


def refold_start(chat_id: str | int | None = "all", *, tier: int | None = None, since: str = "",
                 limit: int = 0, pause_sec: float = 2.0) -> dict:
    """Запустить перевыпуск пачкой в фоне. Один перевыпуск за раз; стоп — refold_stop()."""
    with _REFOLD_LOCK:
        if _REFOLD.get("running"):
            return {"ok": False, "reason": "already_running", **_REFOLD}
        plan = refold_plan(chat_id, tier=tier, since=since, limit=limit)
        _REFOLD.clear()
        _REFOLD.update({
            "running": bool(plan), "stop": False, "planned": len(plan), "done": 0,
            "skipped": 0, "failed": 0, "started_at": _utc_iso(), "finished_at": "",
            "scope": {"place": str(chat_id), "tier": tier, "since": since, "limit": limit},
            "last": None,
        })
        _save_refold_state()
        if not plan:
            return {"ok": True, "planned": 0}
        threading.Thread(target=_refold_worker, args=(plan, float(pause_sec)),
                         name="praxis-refold", daemon=True).start()
    return {"ok": True, "planned": len(plan), "first": plan[0][1], "last": plan[-1][1]}
