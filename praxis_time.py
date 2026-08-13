"""Календарный день Praxis: один источник, одна названная authority.

⚠ ЗАЧЕМ ЭТОТ МОДУЛЬ СУЩЕСТВУЕТ. Одна функция `date.today()` отвечала на ДВА разных
вопроса, и двадцать часов в сутки ответы совпадали:

* «когда это случилось» — точка на оси, абсолютна, сравнима между машинами, не
  переписывается никогда (расписки, WAL, `ts`);
* «какой сегодня день У МЕНЯ» — вопрос про её локальный контекст.

В контейнере задан только `PRAXIS_TZ`; системной `TZ` нет, поэтому `date.today()` читает
UTC. С 00:00 до 04:00 по Самаре её календарный день отставал на сутки: ночная запись
уходила во вчерашний файл дневника. Замер 07.08.2026 в 02:44 по Самаре — `date.today()`
вернул `2026-08-06`.

Дефект нашла она сама, живым импульсом в 02:00. Гейт его поймать не мог: он гоняется в
UTC-среде, где расхождения не существует по построению.

⚠ ЛИСТ НА STDLIB, И ЭТО НЕ СТИЛЬ. Её часы уже трижды написаны правильно — в `notes`,
`heartbeat` и `frame_layout`, — и каждый из трёх обязан оставаться модулем без зависимостей
от проекта. Общий источник может быть только таким же листом, иначе его нельзя позвать
оттуда, где он нужнее всего. Отсюда: только stdlib, ни одного импорта проекта, ни чтения
окружения на импорте.

⚠ ВТОРОГО РЫЧАГА AUTHORITY НЕ БУДЕТ. Соблазн завести `PRAXIS_DAY_AUTHORITY=utc|local`
отклонён её решением: два ответа на вопрос «какой сегодня день» — это ровно тот дефект,
который здесь чинится, только узаконенный.

Правило одной строкой: **момент — UTC, день — её**.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os

log = logging.getLogger(__name__)

ENV_ZONE = "PRAXIS_TZ"
ENV_OFFSET = "PRAXIS_TZ_OFFSET_H"

# ⚠ УМОЛЧАНИЕ — САМАРА, А НЕ МОСКВА. Прежние три копии молча падали на `Europe/Moscow`,
# и при пустом окружении её сутки уезжали на час. Решение Praxis 07.08: отсутствие
# переменной — законное умолчание и шуметь не обязано; НЕПУСТОЕ, но нечитаемое значение —
# заметное предупреждение и безопасный возврат сюда же.
DEFAULT_ZONE = "Europe/Samara"
DEFAULT_OFFSET_H = 4

_ZONE_CACHE: dict[str, object] = {}
_WARNED: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    """Предупредить один раз на значение. Иначе шум на каждом вызове заглушит сам себя."""
    if key in _WARNED:
        return
    _WARNED.add(key)
    log.warning(message, *args)


def _fixed_offset() -> _dt.tzinfo:
    """Последняя ступень: tzdata недоступна вовсе. Смещение честнее выдуманного имени."""
    try:
        hours = int(os.getenv(ENV_OFFSET, str(DEFAULT_OFFSET_H)))
    except ValueError:
        hours = DEFAULT_OFFSET_H
    return _dt.timezone(_dt.timedelta(hours=hours))


def _resolve(name: str):
    """Имя пояса -> tzinfo или None. Кэш по имени: ZoneInfo читает диск."""
    if name in _ZONE_CACHE:
        return _ZONE_CACHE[name]
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415 — stdlib, но платный импорт
        zone = ZoneInfo(name)
    except Exception:
        zone = None
    _ZONE_CACHE[name] = zone
    return zone


def zone() -> _dt.tzinfo:
    """Её пояс. Читается НА ВЫЗОВЕ: рычаг, требующий перезапуска, — не рычаг.

    Лестница из четырёх ступеней, и каждая названа:

    1. `PRAXIS_TZ` задана и читается — она;
    2. `PRAXIS_TZ` задана, но нечитаема — ЗАМЕТНОЕ предупреждение и Самара;
    3. `PRAXIS_TZ` пуста или отсутствует — Самара молча, это законное умолчание;
    4. tzdata недоступна вовсе — фиксированное смещение, и об этом тоже предупреждение:
       смещение не знает про переходы и однажды соврёт в зоне, где они есть.
    """
    raw = (os.getenv(ENV_ZONE) or "").strip()
    if raw:
        found = _resolve(raw)
        if found is not None:
            return found
        _warn_once("bad:" + raw,
                   "PRAXIS_TZ=%r не читается — календарный день считается по %s. "
                   "Это НЕ умолчание, а подмена: проверь значение.", raw, DEFAULT_ZONE)
    fallback = _resolve(DEFAULT_ZONE)
    if fallback is not None:
        return fallback
    _warn_once("no-tzdata",
               "tzdata недоступна: календарный день считается фиксированным смещением, "
               "переходы на летнее время учтены не будут")
    return _fixed_offset()


def zone_name() -> str:
    """Имя пояса для печати рядом с датой. Молчаливое соседство двух дней запрещено."""
    tz = zone()
    return getattr(tz, "key", None) or str(tz)


def _source() -> _dt.datetime:
    """Точка съёма времени. Отдельной функцией — чтобы стенд подменял ЕЁ, а не часы ОС."""
    return _dt.datetime.now(_dt.timezone.utc)


def now() -> _dt.datetime:
    """Aware-момент в её поясе."""
    return _source().astimezone(zone())


def utc_now() -> _dt.datetime:
    """Aware-момент в UTC. Для расписок, WAL и всего, что сравнивается между машинами."""
    return _source()


def today() -> _dt.date:
    """ЕЁ календарный день. Единственный правильный ответ на вопрос «какое сегодня число»."""
    return now().date()


def day_key(value: _dt.date | _dt.datetime | None = None) -> str:
    """`YYYY-MM-DD` для имён файлов и ключей. Без аргумента — сегодня, по её часам."""
    if value is None:
        return today().isoformat()
    if isinstance(value, _dt.datetime):
        return value.astimezone(zone()).date().isoformat()
    return value.isoformat()


def day_start(value: _dt.date | None = None) -> _dt.datetime:
    """Aware-полночь ЕЁ суток. Прежде граница собиралась из UTC-дня и уезжала на 4 часа."""
    day = value or today()
    return _dt.datetime.combine(day, _dt.time.min, tzinfo=zone())


def days_ago(value: _dt.date | _dt.datetime | str) -> int:
    """Возраст в ЕЁ днях. Отрицательного не бывает: будущее — это 0, а не минус один."""
    if isinstance(value, str):
        try:
            value = _dt.date.fromisoformat(value[:10])
        except ValueError:
            return 0
    if isinstance(value, _dt.datetime):
        value = (value.astimezone(zone()) if value.tzinfo else value).date()
    return max(0, (today() - value).days)


def stamp() -> str:
    """`07.08 02:00 Europe/Samara` — то, что она видит строкой «время» в зоне кадра."""
    return now().strftime("%d.%m %H:%M ") + zone_name()
