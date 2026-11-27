"""
Praxis — её запланированные к сроку НАМЕРЕНИЯ. Не тикеты-обязательства и не беклог: каждая
запись — её (или Егора) сознательный выбор вернуться к чему-то в момент T, а не диспетчер
жизни. Четыре честных вида:
  kind=window  — уйти в фокус/к себе (её сознательный ретрит; Telethon закроется на окно);
  kind=wake    — её собственный будильник: живой ход, Telegram ОТКРЫТ (см. ниже);
  kind=message — намерение что-то сказать человеку; к сроку сначала живой пересмотр;
  kind=note    — напоминание себе/владельцу к сроку.
(email — совместимость, обычно выключен.)

`wake` появился 26.07 и закрыл дыру, которая до того стоила ей часа немоты. У неё было
ровно два способа оказаться в сознании: окно (связь рвётся по определению) и чужое
входящее (приходит не по её воле). Сказать «разбуди меня со связью» было НЕЧЕМ — и она
девятнадцать раз подряд попросила об этом единственным, что было под рукой, окном,
каждый раз обрывая себе Telegram. Пробуждение — не ретрит: Telethon остаётся живым, она
может читать и писать сразу, и её никто не будил, кроме неё самой.

`memory/tasks.json` — плоский список: {id, kind, goal, target, when, recur, status, created,
author}. author = кто наметил (praxis | owner | app) — провенанс с рождения, чтобы через неделю
она отличала своё намерение от чужого.

Ещё два поля живут недолго и означают «ещё не время» — оба ДЕРЖАТ намерение вне `due`, но
не гасят его: `after_run` (ждёт конца названного рана) и `claim` (его прямо сейчас
поднимают). Захват снимается либо `mark_fired` — когда durable run создан и владение
передано, — либо `release_open_claims` под единым замком: замок свободен, а захват висит,
значит поднимавший мёртв и рана не создал. Погашенное намерение — всегда следствие
состоявшегося рана, а не намерения его создать.

Планировщик в раннере зовёт `due()` и исполняет сработавшее. Тик — чтение файла и сравнение
времени, БЕЗ вызова модели: токены тратятся только когда намерение реально срабатывает.

ПОХОРОНЕНО (PASS 26): «график автономных окон» (recur window target='__auto__' + set_window_
schedule) — он дублировал часовой social_pulse. Периодические пробуждения теперь ТОЛЬКО у пульса;
здесь остаются лишь её осознанные намерения к сроку (в т.ч. обычный recur — ежедневное
напоминание себе, это её выбор, а не автономный обход хвостов).
"""
from __future__ import annotations

import os

import datetime as _dt
import json
import re
import uuid
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
TASKS = BASE / "memory" / "tasks.json"
KINDS = ("window", "wake", "email", "message", "note")
AUTHORS = ("praxis", "owner", "app")
# Old readers only admit `pending`; they must never upgrade a scoped return to a generic wake.
_FOCUS_PENDING = "focus_pending"


def _is_open(row: dict) -> bool:
    if row.get("work_id"):
        return row.get("status") == _FOCUS_PENDING
    return row.get("status") == "pending"


def message_reassessment_prompt(task: dict) -> str:
    """Lossless, non-diagnostic due-time frame for a person-addressed intention.

    The scheduler does not diagnose whether a relationship or boundary changed. It brings
    the old evidence into a normal cognitive turn, where current context and tools are
    available, and requires a new decision before any delivery.
    """
    target = str(task.get("target") or task.get("target_id") or "")
    body = str(task.get("goal") or "")
    created = str(task.get("created") or "unknown")
    author = str(task.get("author") or "praxis")
    return (
        "A scheduled person-addressed intention is due for fresh reassessment.\n"
        f"Original target: {target}\n"
        f"Original body:\n{body}\n"
        f"Created at: {created}\n"
        f"Author/provenance: {author}\n\n"
        "Old intention is evidence, not a command. Before any person-addressed delivery, "
        "use the current live frame and, where useful, current conversation/tools to check "
        "for relationship, moderation, or boundary changes. Make a fresh decision now: "
        "send (using an ordinary send tool), revise, defer, or let it go. Nothing is sent "
        "merely because the old due time arrived."
    )


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def same_clock(value: _dt.datetime, now: _dt.datetime) -> _dt.datetime:
    """Привести срок к тем же часам, в которых живёт `now` — иначе сравнение незаконно.

    `when` бывает двух форм: наивной («2026-07-27T18:00» — стенные часы процесса, их же
    читает `_now()`) и со смещением («2026-07-25T03:10+04:00» — так пишет модель, а
    `parse_when` честно сохраняет её слова как есть). Прямое `<=` между ними бросает
    TypeError — и этот TypeError попадал в голый `except Exception: pass` ниже, отчего
    намерение не срабатывало НИКОГДА и молча. У неё такое пролежало с 25 июля: в повестке
    вечное «ждёт», а планировщик его даже не видел.
    """
    if value.tzinfo is None and now.tzinfo is not None:
        return value.astimezone()
    if value.tzinfo is not None and now.tzinfo is None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _deadline(t: dict, now: _dt.datetime) -> _dt.datetime | None:
    """Срок записи в часах `now`. None — срока нет ИЛИ его форму часы не читают."""
    w = t.get("when")
    if not w:
        return None
    try:
        return same_clock(_dt.datetime.fromisoformat(str(w)), now)
    except Exception:
        return None


def when_trouble(t: dict) -> str | None:
    """Почему срок этой записи нечитаем. None — читается или его нет.

    Контракт R1: гейт может существовать — молча нет. Срок, который часы не понимают,
    ровно такой гейт: намерение живо на вид и мертво на деле. Пусть это видно там же, где
    она смотрит свою повестку, а не остаётся тайной планировщика.
    """
    w = t.get("when")
    if not w or _deadline(t, _now()) is not None:
        return None
    return f"срок «{w}» я прочитать не могу — намерение не сработает, назначь заново"


def _serialized_writer(fn):
    """Runner and panel processes transact on the same schedule document."""
    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):
        TASKS.parent.mkdir(parents=True, exist_ok=True)
        with TASKS.with_suffix(".lock").open("a+b") as fh:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            return fn(*args, **kwargs)
    return wrapped


def _load(*, strict: bool = False) -> list:
    try:
        d = json.loads(TASKS.read_text(encoding="utf-8"))
        if not isinstance(d, list) or any(not isinstance(row, dict) for row in d):
            raise ValueError("Invalid task schedule document")
        return d
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        if strict:
            raise
        return []


def _save(items: list) -> None:
    TASKS.parent.mkdir(parents=True, exist_ok=True)
    tmp = TASKS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(TASKS)


def parse_when(s: str | None) -> tuple[str | None, str | None]:
    """-> (when_iso, recur). Принимает ISO, 'in Nh/Nm', 'today/tomorrow HH:MM', 'daily HH:MM', 'every Nh'."""
    if not s:
        return None, None
    s = str(s).strip().lower()
    # ⚠ Диапазоны ПРОВЕРЯЮТСЯ здесь. «daily 24:00» этот шаблон принимал, запись ложилась
    # в файл как валидная, а `_next_recur` потом падал на _dt.time(24, 0) прямо внутри
    # `due()` — то есть одна кривая строка роняла весь планировщик, вместе со всеми
    # остальными её намерениями. «every 0h» принимался тоже и двигал срок в «сейчас»,
    # заставляя намерение срабатывать на каждом тике.
    m = re.match(r"daily\s+(\d{1,2}):(\d{2})$", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            return None, None
        return None, f"daily {hour:02d}:{minute:02d}"
    m = re.match(r"every\s+(\d+)\s*h$", s)
    if m:
        hours = int(m.group(1))
        if not 1 <= hours <= 24 * 14:
            return None, None
        return None, f"every {hours}h"
    m = re.match(r"in\s+(\d+)\s*([hm])$", s)
    if m:
        delta = _dt.timedelta(hours=int(m.group(1))) if m.group(2) == "h" else _dt.timedelta(minutes=int(m.group(1)))
        return (_now() + delta).isoformat(timespec="minutes"), None
    m = re.match(r"(today|tomorrow)\s+(\d{1,2}):(\d{2})$", s)
    if m:
        hour, minute = int(m.group(2)), int(m.group(3))
        if hour > 23 or minute > 59:
            return None, None
        day = _now().date() + (_dt.timedelta(days=1) if m.group(1) == "tomorrow" else _dt.timedelta())
        return _dt.datetime.combine(day, _dt.time(hour, minute)).isoformat(timespec="minutes"), None
    try:
        return _dt.datetime.fromisoformat(s).isoformat(timespec="minutes"), None
    except Exception:
        return None, None


def _next_recur(recur: str, now: _dt.datetime) -> str | None:
    """Следующий срок повтора. None — строку не понимаю (и тогда молчать нельзя).

    Дополнительно обёрнут в try: сюда ходят и записи, легшие на диск ДО проверки
    диапазонов в parse_when, а исключение здесь роняет `due()` целиком — вместе со
    всеми её остальными намерениями."""
    try:
        return _next_recur_inner(recur, now)
    except Exception:
        return None


def _next_recur_inner(recur: str, now: _dt.datetime) -> str | None:
    m = re.match(r"daily\s+(\d{2}):(\d{2})", recur or "")
    if m:
        cand = _dt.datetime.combine(now.date(), _dt.time(int(m.group(1)), int(m.group(2))))
        if cand <= now:
            cand += _dt.timedelta(days=1)
        return cand.isoformat(timespec="minutes")
    m = re.match(r"every\s+(\d+)h", recur or "")
    if m:
        return (now + _dt.timedelta(hours=int(m.group(1)))).isoformat(timespec="minutes")
    return None


@_serialized_writer
def add(kind: str, goal: str, when: str | None = None, target: str | None = None,
        target_id=None, risky: bool = False, author: str = "praxis",
        after_run: str | None = None, swapped_from: str | None = None,
        work_id: str = "", after_forge: tuple[str, str] | None = None) -> dict:
    """PASS 9.4: target_id — id, отрезолвленный при постановке (планировщик шлёт по нему,
    без повторного поиска); risky — адресат незнаком (нет в known_ids/people).
    PASS 26: author — кто наметил намерение (praxis|owner|app), провенанс с рождения.
    Микро-намерения: after_run — намерение ждёт ЗАВЕРШЕНИЯ рана и до того не созревает
    (часы снимают удержание, когда ран терминален); созреет по своему when или сразу."""
    when_iso, recur = parse_when(when)
    task = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind if kind in KINDS else "note",
        "goal": goal or "",
        "target": target or "",
        "when": when_iso,
        "recur": recur,
        "status": "pending",
        "created": _now().isoformat(timespec="minutes"),
        "author": author if author in AUTHORS else "praxis",
    }
    if work_id:
        if kind not in {"window", "wake"} or author != "praxis" or recur:
            raise ValueError("work_id requires a one-shot self window or wake")
        task["work_id"] = work_id
        task["status"] = _FOCUS_PENDING
    if after_forge:
        if not work_id or after_run:
            raise ValueError("after_forge requires a named work return and no after_run")
        task["after_forge"] = list(after_forge)
    if target_id is not None:
        task["target_id"] = target_id
    if risky:
        task["risky"] = True
    if after_run:
        task["after_run"] = str(after_run)
    if swapped_from:
        # Она просила один вид, записан другой. Провенанс `author` отвечает на «чьё это
        # намерение», но не на «тот ли это вид, что я выбирала»: через неделю запись
        # [wake] author=praxis неотличима от выбранной ею. Поле держит эту разницу на
        # диске, чтобы объяснение жило не только в одном тул-ответе.
        task["swapped_from"] = str(swapped_from)
    if task["kind"] in ("wake", "note", "message") and (not task.get("recur") or task.get("when")):
        # Optional authority sidecar: its failure never changes task creation.
        try:
            import keat_live
            occurrence = str(task.get("when") or task.get("created") or "one-shot")
            source = keat_live.issue_scheduled_wake(task, occurrence)
            if source is not None:
                task["_keat_wake_source"] = source
        except Exception:
            pass
    items = _load(strict=True)
    if work_id and any(row.get("work_id") == work_id and _is_open(row)
                       for row in items):
        raise ValueError("a return for this work is already pending; use its alarm or cancel it")
    items.append(task)
    _save(items)
    return task


def list_open() -> list:
    return [t for t in _load() if _is_open(t)]


def selected_forge_pairs() -> set[tuple[str, str]]:
    return {tuple(dep) for alarm in list_open()
            if isinstance(dep := alarm.get("after_forge"), list) and len(dep) == 2
            and all(isinstance(token, str) and token for token in dep)}


@_serialized_writer
def admit_forge_result(alarm: dict, receipt: dict) -> bool:
    """Record the selected receipt once, on the original alarm, before admission."""
    items = _load(strict=True)
    for row in items:
        if (row.get("id") == alarm.get("id") and _is_open(row)
                and row.get("work_id") == alarm.get("work_id") and not row.get("claim")
                and row.get("after_forge") == [receipt["task_id"], receipt["agent_id"]]):
            if not row.get("forge_result"):
                row["forge_result"] = receipt
                _save(items)
            return row["forge_result"] == receipt
    return False


@_serialized_writer
def cancel(task_id: str) -> bool:
    items, changed = _load(strict=True), False
    for t in items:
        if t.get("id") == task_id and _is_open(t):
            t["status"], changed = "cancelled", True
    if changed:
        _save(items)
    return changed


@_serialized_writer
def due(now: _dt.datetime | None = None) -> list:
    """Сработавшие задачи (status=pending и время пришло). recur без when — проставить следующий."""
    now = now or _now()
    items, changed, out = _load(strict=True), False, []
    for t in items:
        if not _is_open(t):
            continue
        if t.get("after_run") or (t.get("after_forge") and not t.get("forge_result")):
            continue
        if t.get("claim"):
            continue  # уже поднимается прямо сейчас; снимет mark_fired или release_open_claims
        if t.get("recur") and not t.get("when"):
            t["when"], changed = _next_recur(t["recur"], now), True
            # The first recurring occurrence comes into existence here.
            if t.get("kind") in ("wake", "note", "message") and t.get("when"):
                try:
                    import keat_live
                    source = keat_live.issue_scheduled_wake(t, str(t["when"]))
                    if source is not None:
                        t["_keat_wake_source"] = source
                except Exception:
                    pass
        if not t.get("when"):
            out.append(t)                      # срока нет — созрело
        else:
            deadline = _deadline(t, now)
            if deadline is None:
                # Нечитаемый срок. Раньше это был голый `pass`, и намерение исчезало из
                # виду планировщика навсегда, ничем себя не выдав. Теперь форму со
                # смещением часы читают (same_clock), а всё, что не читается и после
                # этого, видно ей через when_trouble в повестке.
                continue
            if deadline <= now:
                out.append(t)
    if changed:
        _save(items)
    return out


@_serialized_writer
def claim_open(task_id: str, kind: str = "", *, run_id: str = "", work_id: str = "",
               forge_result: dict | None = None) -> bool:
    """Взять намерение В РАБОТУ, но НЕ погасить его. True — взяли.

    ⚠ Нашла Праксис, читая наши коммиты: «намерение может быть помечено сработавшим до
    того, как когнитивный run надёжно создан; если процесс рухнет между этими событиями,
    намерение уже погашено, а я так и не проснулась». Разбор подтвердил и добавил два
    способа потерять его БЕЗ всякого падения: если мозг не сконфигурирован (истёк токен —
    так было 20.07), оба входа возвращают пустоту и ран не создаётся вовсе; и если
    создание рана честно падает fail-closed. Во всех трёх случаях `status` уже `done`, а
    проснуться было нечем — и след не остаётся никакой.

    Корень: пометка — это ПЕРЕДАЧА ВЛАДЕНИЯ. До рана намерением владеет этот файл, после —
    хранилище ранов (журнал, восстановление, жнец). Передача делалась раньше, чем
    появлялся получатель. Метить в конце нельзя тоже: тогда владельцев двое, и падение
    посреди окна воскрешает намерение поверх resume — ровно петля 06.07.

    Поэтому две фазы: `claim_open` при реальном подъёме (держит намерение вне `due`, но
    оставляет `pending` и видимым в повестке), а `mark_fired` — в тот миг, когда ран
    существует. Захват, чей ран так и не появился, снимается `release_open_claims` под
    единым замком: замок свободен и захват висит — значит поднимавший мёртв.
    """
    items, changed = _load(strict=True), False
    for t in items:
        if t.get("id") == task_id and _is_open(t) and not t.get("claim"):
            t["claim"] = {"at": _now().isoformat(timespec="seconds"),
                          "for": kind or t.get("kind") or ""}
            if run_id:
                if (not work_id or t.get("work_id") != work_id or t.get("kind") != kind
                        or t.get("forge_result") != forge_result
                        or (t.get("after_forge") and not forge_result)):
                    return False
                t["claim"]["run_id"] = run_id
            changed = True
    if changed:
        _save(items)
    return changed


@_serialized_writer
def confirm_work_run(task_id: str, work_id: str, run_id: str, *, handoff=None) -> bool:
    items = _load(strict=True)
    for t in items:
        if (t.get("id") == task_id and _is_open(t)
                and t.get("work_id") == work_id and not t.get("recur")
                and (t.get("claim") or {}).get("run_id") == run_id):
            if handoff is not None:
                handoff()
            t["status"] = "done"
            t["opened_run_id"] = run_id
            t.pop("claim")
            _save(items)
            return True
    return False


@_serialized_writer
def release_open_claims(read_work_run=None) -> list:
    """Снять зависшие захваты (их поднимавший мёртв) -> список id. Намерение снова созреет.

    Звать ТОЛЬКО когда держишь единый замок и живого прохода нет: тогда захват не может
    принадлежать никому живому. Тот же довод, на котором стоит жнец осиротевших ранов."""
    items, out, changed = _load(strict=True), [], False
    for t in items:
        if not _is_open(t) or not t.get("claim"):
            continue
        run_id = t["claim"].get("run_id")
        if run_id:
            # Only an absent run directory proves this claim never reached run custody.
            # A partial, unreadable or conflicting run stays held; never issue it again.
            observed = (read_work_run(run_id, t.get("work_id"), t.get("id"))
                        if read_work_run else "unknown")
            if observed == "bound":
                t["status"] = "done"
                t["opened_run_id"] = run_id
                t.pop("claim")
                changed = True
                continue
            if observed != "absent":
                continue
        t.pop("claim")
        out.append(t.get("id"))
        changed = True
    if changed:
        _save(items)
    return out


def open_claims() -> list:
    """Намерения, взятые в работу прямо сейчас (для наблюдения, не для решений)."""
    return [t for t in _load() if _is_open(t) and t.get("claim")]


def after_run_holds() -> list:
    """Открытые намерения, ждущие завершения рана (не входят в due до снятия удержания)."""
    return [t for t in _load() if _is_open(t) and t.get("after_run")]


@_serialized_writer
def clear_after_run(task_id: str) -> bool:
    """Ран завершился: снять удержание — намерение созревает по своему when или немедленно."""
    items, changed = _load(strict=True), False
    for t in items:
        if t.get("id") == task_id and _is_open(t) and t.get("after_run"):
            t.pop("after_run", None)
            changed = True
    if changed:
        _save(items)
    return changed


def recent_count(*, kind: str = "window", within_sec: float = 3600.0) -> int:
    """Сколько задач этого вида заводилось за окно времени.

    Счёт, а не догадка. Здесь стояла ещё и попытка считать «похожие по смыслу» цели —
    усечение русских основ до четырёх букв, чтобы «диалоги» и «диалогам» совпали. Егор
    назвал это костылём, и был прав: угадывание смысла по морфологии — не механизм, а
    подпорка под отсутствующую способность (у неё нет хода «разбуди меня со связью»).
    Осталось то, что можно посчитать честно: сколько раз за час она рвала свой Telegram.
    """
    cut = _now() - _dt.timedelta(seconds=max(1.0, float(within_sec)))
    return sum(1 for item in _load()
               if (not kind or item.get("kind") == kind) and _within(item, cut))


def _within(item: dict, cut) -> bool:
    try:
        created = _dt.datetime.fromisoformat(str(item.get("created") or ""))
    except ValueError:
        return False
    if created.tzinfo is None and cut.tzinfo is not None:
        created = created.replace(tzinfo=cut.tzinfo)
    return created >= cut


@_serialized_writer
def mark_fired(task_id: str) -> None:
    """После исполнения: одноразовое -> done; повторяющееся -> следующий запуск."""
    items, changed = _load(strict=True), False
    for t in items:
        if t.get("id") == task_id and _is_open(t):
            t.pop("claim", None)      # владение передано рану — держать больше нечего
            if t.get("recur"):
                t["when"], changed = _next_recur(t["recur"], _now()), True
                # This is creation of the next occurrence, not its firing.  Keep
                # scheduling behavior unchanged when optional capture is unavailable.
                t.pop("_keat_wake_source", None)
                if t.get("kind") in ("wake", "note", "message") and t.get("when"):
                    try:
                        import keat_live
                        source = keat_live.issue_scheduled_wake(t, str(t["when"]))
                        if source is not None:
                            t["_keat_wake_source"] = source
                    except Exception:
                        pass
            else:
                t["status"], changed = "done", True
            changed = True
    if changed:
        _save(items)
