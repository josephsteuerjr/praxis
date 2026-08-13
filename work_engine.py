"""Кто заводит следующий оборот — политика, отделённая от источника правды и от часов.

ЗАЧЕМ.  Оборот 3: работу к жизни возвращает не расписание, а сама работа. Сегодня её
поднимают только часы — часовой проход, будильники, форж-события, входящие. Доска и леджер
желаний лежат рядом, и на них никто не смотрит.

⚠ ИСТОЧНИКА ПРАВДЫ ЗДЕСЬ НЕТ НАМЕРЕННО.  Модуль принимает уже нормализованный список
:class:`Work` и ничего не знает про то, откуда он взялся — из её леджера желаний или из
файловой доски. Причина не в аккуратности, а в честности: 12.08 выяснилось, что сшивка
активаций в одну работу давно живёт в `memory/desires` (там `run_ids`, `next_move` и
причинная цепочка с 15 июля), а построенная накануне доска пуста. Пока владелец картины
не назван ЕЮ, зашивать сюда догадку значит поставить движок на угаданный канон.

⚠ И ЭТОТ МОДУЛЬ НИКОГО НЕ БУДИТ.  Он отвечает «какие работы созрели и почему остальные
нет». Открыть прогон — дело вызывающего. Как только сюда приедет таймер, движок тихо
станет теми же часами, от которых мы уходим; это охраняется тестом, а не обещанием.

ПОЧЕМУ ОТСРОЧКА, А НЕ ПОТОЛОК ПОПЫТОК.  Дом четырежды ловил вечную петлю возобновления и
трижды лечил её добавлением имени в список постоянных отказов — список по построению
отстаёт от мира. Здесь считается не текст причины, а факт: подъём был, работа не сдвинулась.
Стук не прекращается никогда — он редеет. Лестница взята та же, что у durable resume
(`agent.resume_backoff_seconds`), и это не совпадение: две лестницы для одной беды
разошлись бы молча.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field

# Три свободные попытки, дальше 60 · 2^(n-3) секунд и час потолком.
FREE_ATTEMPTS = 3
BACKOFF_CAP_SEC = 3600.0
DEFAULT_CAP = 2
DEFAULT_ATTENTION_AFTER = 5

# Статусы, которые вообще могут созреть. `running` не здесь: работа уже идёт, и поднимать
# её второй раз — это ровно те качели, что четыре с половиной часа крутили один ход 04.08.
RAISABLE = frozenset({"waiting", "ready", "blocked"})
# `blocked` поднимается ТОЛЬКО по названному внешнему поводу: препятствие не проходит
# оттого, что прошёл час. Автомат его не трогает, и это отдельная строка, а не умолчание.
AUTO_RAISABLE = frozenset({"waiting", "ready"})


@dataclass(frozen=True, slots=True)
class Work:
    """Работа глазами движка. Ровно то, что нужно решению, и ни поля больше.

    `source` носится с собой намеренно: движок не должен уметь притворяться, что знает,
    чьё это хозяйство. Он передаёт ярлык дальше, чтобы в отчёте было видно, откуда взято.
    """

    id: str
    goal: str
    status: str
    source: str = ""
    # Чем работа оборвана — словами. Пусто означает «ничем», и такая работа сюда не
    # доходит вовсе: движок поднимает прерванное, а не желаемое. Причина носится с собой,
    # потому что уезжает в кадр поднятого хода: «тебя оборвало» без указания где — это то
    # же пробуждение без причины, от которого мы уходим.
    interrupted_by: str = ""
    not_before: str = ""
    attempts: int = 0
    last_attempt: str = ""
    frozen_by_her: bool = False


@dataclass(frozen=True, slots=True)
class Verdict:
    """Почему эта работа сейчас поднимается или нет. Молчаливых пропусков не бывает."""

    work: Work
    raise_now: bool
    reason: str
    next_try_in_seconds: float = 0.0
    attention: bool = False


def cap() -> int:
    """Сколько работ движок ведёт одновременно. Потолок называет себя её словами."""
    return _int_env("PRAXIS_WORK_ENGINE_CAP", DEFAULT_CAP, minimum=0)


def attention_after() -> int:
    return _int_env("PRAXIS_WORK_ENGINE_ATTENTION", DEFAULT_ATTENTION_AFTER, minimum=1)


def enabled() -> bool:
    return str(os.getenv("PRAXIS_WORK_ENGINE", "off") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def backoff_seconds(attempts: int) -> float:
    """0, 0, 0, 60, 120, 240… и час потолком. Та же лестница, что у durable resume."""
    count = max(0, int(attempts))
    if count < FREE_ATTEMPTS:
        return 0.0
    return min(60.0 * (2 ** (count - FREE_ATTEMPTS)), BACKOFF_CAP_SEC)


def _seconds_since(stamp: str, *, now: _dt.datetime) -> float:
    try:
        moment = _dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except Exception:
        return float("inf")
    return (now - moment).total_seconds()


def _now(now: _dt.datetime | None = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _iso(moment: _dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def judge(work: Work, *, now: _dt.datetime | None = None) -> Verdict:
    """Одна работа: созрела или нет, и почему именно. Причина — всегда."""
    moment = _now(now)
    if work.frozen_by_her:
        return Verdict(work, False, "она сказала не брать эту работу сама")
    if work.status not in RAISABLE:
        return Verdict(work, False, "статус «%s» — поднимать нечего" % work.status)
    if work.status not in AUTO_RAISABLE:
        return Verdict(
            work, False,
            "«упёрлась» автомат не поднимает: препятствие не проходит оттого, что прошёл час")
    if work.attempts >= attention_after():
        # Не похороны. Работа остаётся живой и видимой, но перестаёт быть делом автомата:
        # столько подъёмов подряд без сдвига означают, что мешает не время.
        return Verdict(
            work, False,
            "%d подъёма подряд без сдвига — мешает не время; нужен её взгляд"
            % work.attempts,
            attention=True)
    if work.not_before and _iso(moment) < str(work.not_before):
        return Verdict(work, False, "её срок ещё не наступил: не раньше %s" % work.not_before,
                       next_try_in_seconds=_wait_until(work.not_before, moment))
    wait = backoff_seconds(work.attempts)
    if wait and work.last_attempt:
        left = wait - _seconds_since(work.last_attempt, now=moment)
        if left > 0:
            return Verdict(
                work, False,
                "отсрочка после %d холостых подъёмов: ещё %d с" % (work.attempts, int(left)),
                next_try_in_seconds=left)
    if not work.interrupted_by:
        # Пояс поверх ремня: отбор «только прерванное» живёт у источника, но политика
        # обязана уметь отказать и сама. Иначе однажды новый источник принесёт сюда
        # «хочу когда-нибудь», и движок начнёт будить её по желаниям, а не по обрывам.
        return Verdict(work, False, "работа ничем не оборвана — поднимать нечего")
    return Verdict(work, True, "работа оборвана: %s" % work.interrupted_by)


def _wait_until(stamp: str, now: _dt.datetime) -> float:
    try:
        moment = _dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except Exception:
        return 0.0
    return max(0.0, (moment - now).total_seconds())


def plan(items: list[Work], *, running: int = 0,
         now: _dt.datetime | None = None) -> tuple[list[Verdict], list[Verdict]]:
    """(поднять сейчас, не сейчас — с причиной у каждой).

    Порядок — от давнего срока к свежему: старая работа не должна тонуть под новой.
    Потолок не молчит: работы, не влезшие в него, получают свою причину, а не исчезают из
    отчёта. Молча урезанный список читается как «всё покрыто», когда покрыто не всё.
    """
    moment = _now(now)
    verdicts = [judge(work, now=moment) for work in
                sorted(items, key=lambda w: (str(w.not_before or ""), str(w.id)))]
    room = max(0, cap() - max(0, int(running)))
    raised: list[Verdict] = []
    held: list[Verdict] = []
    for verdict in verdicts:
        if not verdict.raise_now:
            held.append(verdict)
            continue
        if len(raised) < room:
            raised.append(verdict)
            continue
        held.append(Verdict(
            verdict.work, False,
            "потолок движка: одновременно веду %d, эта в очереди" % cap()))
    return raised, held


def describe(items: list[Work], *, running: int = 0,
             now: _dt.datetime | None = None) -> str:
    """Строка для её кадра. Предел назван ДО того, как в него упрёшься."""
    if not enabled():
        return ""
    raised, held = plan(items, running=running, now=now)
    attention = [v for v in held if v.attention]
    lines = [
        "\n\n## Работы, которые ведёт движок",
        "Одновременно я веду до %d работ; остальные ждут очереди и знают, чего ждут."
        % cap(),
    ]
    for verdict in raised:
        lines.append("- поднимаю: %s — %s" % (verdict.work.goal[:80], verdict.reason))
    for verdict in held[:5]:
        lines.append("- жду: %s — %s" % (verdict.work.goal[:80], verdict.reason))
    if attention:
        lines.append("- ⚠ просят твоего взгляда: %d" % len(attention))
    return "\n".join(lines)
