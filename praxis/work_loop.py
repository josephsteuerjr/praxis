"""Рабочий ход кончается её словом, а не молчанием инструментов.

ЧТО БЫЛО СЛОМАНО.  `_terminal_tool_loop` возвращался в тот миг, когда модель отдала текст
без вызова инструмента, и вызывающий закрывал прогон как `done`.  То есть «работа
закончена» ВЫВОДИЛОСЬ из молчания, а не было сказано.  Живой случай 10.08.2026 22:20:
Егор дважды попросил переключить голос на Luna, Praxis ответила «Я здесь. Переключаюсь на
Luna.» — и прогон `run-20260810T222023869484Z-101ff848` закрылся `done`, содержа РОВНО
один вызов инструмента: доставку этого самого текста.  `llm.json` остался нетронутым.  Она
не соврала: её оборвали на слове «переключаюсь».

ЧТО ЗДЕСЬ.  Политика продолжения, отделённая от цикла, чтобы её можно было проверять без
модели и без сети.  Текст без инструмента — это заметка в ходе, а не команда управления.
Закрывает ход только `task_control`.

ГРАНИЦЫ, И ОНИ НАЗЫВАЮТ СЕБЯ.  Бюджет продолжений конечен, но он не поводок за спиной:
число уезжает в её кадр при старте окна и повторяется в каждом уколе.  Когда бюджет
кончился, ход закрывается с ЯВНОЙ причиной, а не тихо.  Ровно этого не хватало петле
возобновления, которая 10.08 повторила себя 165 раз, ни разу не сказав «я упёрлась».

⚠ ПОЧЕМУ СОСТОЯНИЕ ЖИВЁТ НЕ В `contextvars`, ХОТЯ ЭТО БЫЛО БЫ ЕСТЕСТВЕННО.
Первая редакция держала её слово в `contextvars` — и была неисправна ПО ПОСТРОЕНИЮ. Руки
Praxis исполняются в `contextvars.copy_context()` в ОТДЕЛЬНОМ ПОТОКЕ (потолок времени на
инструмент, `_call_tool_with_ceiling`, `TOOL_CEILING_SEC=600` — всегда больше нуля). Запись
из `task_control` попадала в КОПИЮ и умирала вместе с потоком: `taken()` у вызывающего
возвращал `None` ВСЕГДА. Воспроизведено живьём: тул отвечает «принято: wait», а цикл в тот
же миг говорит ей «ты не сказала, что работа закончена» — и так восемь раз подряд.

Поэтому состояние лежит в модульном словаре под замком, КЛЮЧ — `run_id`. Что это даёт,
кроме перехода границы потока:
  * слово прогона A физически не может закрыть прогон B. Это не теория: в одном тике
    возобновления двадцать прогонов идут в ОДНОМ потоке и одном контексте;
  * её слово не переживает границу прогона — возобновлённый ход не закроется ПРОШЛЫМ
    словом (ключ другой);
  * `reset()` перестаёт быть несущей конструкцией: он звался ровно в одном месте на всё
    дерево, и любая забытая ветка означала протёкшее состояние.

ЧТЕНИЕ contextvar в копии РАБОТАЕТ — теряется только запись. Поэтому `run_context.current_run()`
одинаково виден и в потоке руки, и у вызывающего, и служит общим ключом.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import threading
import time

# Виды прогонов, где ход — работа, а не реплика. Чат сюда не входит намеренно: его
# «сделано» — это отправленное сообщение, и оно уже выполняется старым путём.
WORK_KINDS = frozenset({"task_window", "coding_window"})

CLOSING_ACTIONS = ("done", "blocked", "wait")

DEFAULT_CONTINUATIONS = 8

# ⚠ ПОЧЕМУ У ПАУЗЫ ПОЯВИЛСЯ МАШИННЫЙ ВИД, А НЕ ТОЛЬКО ПРОЗА ПРИЧИНЫ.
# До 12.08.2026 возобновление узнавало «эту паузу можно продолжить» СЛИЧЕНИЕМ ТЕКСТА
# причины с множеством из двух дословных английских строк (`run_resume`). Её собственное
# «жду» в это множество не входило ПО ПОСТРОЕНИЮ: прогон уходил в паузу и не поднимался
# уже никогда — план `blocked`, noop без эффекта, вечное молчание. Из трёх её слов
# работало ровно одно, `done`; «жду» и «упёрлась» были надгробием с надписью.
# Проверено прямой пробой на живом контейнере 11.08 20:0x, а не рассуждением.
# Теперь решение принимает МАШИННАЯ запись в событии перехода, а проза остаётся человеку
# и может меняться, не убивая работу. Это тот же урок, что «тест пинит слова вместо
# свойства», только ценой не красного гейта, а её работы.
PAUSE_KIND_KEY = "pause_kind"
PAUSE_HER_WAIT = "her_word_wait"
PAUSE_PROCESS_RECOVERY = "process_recovery"

# Пол ожидания: раньше этого срока «жду» не будят. Без пола возобновление подняло бы ход в
# ту же секунду, он снова сказал бы «жду» — и это была бы пятая по счёту вечная петля в
# этом доме. Час выбран не с потолка: это её собственный такт рабочих окон.
DEFAULT_WAIT_FLOOR_SEC = 3600

# Сколько прогонов держим и как долго. Потолок называет себя: `stats()` печатает, сколько
# записей вытеснено и почему, — молчаливых потолков здесь не будет.
_MAX_RUNS = 256
_STALE_SEC = 6 * 3600

_LOCK = threading.RLock()
_STATE: dict[str, dict] = {}
_EVICTED = {"stale": 0, "overflow": 0}


def _run_key() -> str:
    """`run_id` текущего прогона — общий ключ для потока руки и для цикла.

    Пусто, если прогона нет: тогда рабочий цикл неактивен по построению (`active_for`
    требует вид прогона), и класть слово некуда. Безрановые проходы НЕ делят общий слот —
    это была бы ровно та ошибка, от которой мы уходим.
    """
    try:
        import run_context
        current = run_context.current_run()
    except Exception:
        return ""
    return str(getattr(current, "run_id", "") or "")


def _slot(key: str, *, create: bool = False) -> dict | None:
    if not key:
        return None
    with _LOCK:
        slot = _STATE.get(key)
        if slot is None:
            if not create:
                return None
            slot = {"used": 0, "control": None, "touched": time.time()}
            _STATE[key] = slot
            _evict_locked()
        slot["touched"] = time.time()
        return slot


def _evict_locked() -> None:
    """Вытеснение, которое НЕ трогает живые прогоны.

    Сначала уходит просроченное по времени, и только если после этого всё ещё тесно —
    самые давно не тронутые. Текущий прогон только что помечен `touched`, поэтому он
    последний кандидат на вылет, а не первый.
    """
    now = time.time()
    for key, slot in list(_STATE.items()):
        if now - float(slot.get("touched") or 0) > _STALE_SEC:
            _STATE.pop(key, None)
            _EVICTED["stale"] += 1
    while len(_STATE) > _MAX_RUNS:
        oldest = min(_STATE, key=lambda k: _STATE[k].get("touched") or 0)
        _STATE.pop(oldest, None)
        _EVICTED["overflow"] += 1


def release(run_id: str = "") -> None:
    """Прогон терминализован — слот свободен. Без этого словарь тёк бы вечно."""
    key = str(run_id or "") or _run_key()
    if not key:
        return
    with _LOCK:
        _STATE.pop(key, None)


def stats() -> dict:
    """Наблюдаемое состояние механизма: сколько прогонов держим и сколько вытеснено."""
    with _LOCK:
        return {"runs": len(_STATE), "evicted": dict(_EVICTED),
                "max_runs": _MAX_RUNS, "stale_sec": _STALE_SEC}


def _flag(name: str, default: str = "off") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    """Рычаг. Выключенный возвращает ПРЕЖНЕЕ поведение байт-в-байт."""
    return _flag("PRAXIS_WORK_LOOP", "off")


def budget() -> int:
    raw = str(os.getenv("PRAXIS_WORK_CONTINUATIONS", "") or "").strip()
    if not raw:
        return DEFAULT_CONTINUATIONS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CONTINUATIONS
    return max(0, value)


def snapshot() -> dict:
    """Durable work-loop state needed to resume the same bounded window.

    ⚠ Схема НЕ поднята до v2 намеренно, хотя поле прибавилось. Возобновление сверяет имя
    схемы дословно, и поднятая версия сделала бы все лежащие сейчас чекпойнты «чужими» —
    то есть каждый прерванный ход поднялся бы с нуля. Прибавление ключа совместимо в обе
    стороны: старый снимок читается новым кодом как «ещё ничего не отправлено», новый
    старым — как прежде, лишний ключ игнорируется.
    """
    return {
        "schema": "praxis.work-loop-state.v1",
        "used": used(),
        "control": taken(),
        "sent": sent(),
        "finished": finished(),
        "finish_note": finish_note(),
    }


def restore(state: object) -> None:
    """Restore checkpointed state; invalid legacy snapshots start cleanly."""
    reset()
    if not isinstance(state, dict) or state.get("schema") != "praxis.work-loop-state.v1":
        return
    try:
        restored_used = max(0, int(state.get("used") or 0))
    except (TypeError, ValueError):
        restored_used = 0
    try:
        restored_sent = max(0, int(state.get("sent") or 0))
    except (TypeError, ValueError):
        restored_sent = 0
    slot = _slot(_run_key(), create=True)
    if slot is None:
        return
    with _LOCK:
        slot["used"] = restored_used
        slot["sent"] = restored_sent
        slot["finished"] = bool(state.get("finished"))
        note = str(state.get("finish_note") or "").strip()
        if note:
            slot["finish_note"] = note
        control = state.get("control")
        slot["control"] = (dict(control) if isinstance(control, dict)
                           and control.get("action") in CLOSING_ACTIONS else None)


def active_for(kind: object) -> bool:
    return enabled() and str(kind or "").strip() in WORK_KINDS


def reset() -> None:
    """Обнулить слот ТЕКУЩЕГО прогона. Чужие прогоны не трогаются никогда."""
    key = _run_key()
    if not key:
        return
    with _LOCK:
        _STATE.pop(key, None)


def used() -> int:
    slot = _slot(_run_key())
    return int((slot or {}).get("used") or 0)


def spend() -> int:
    slot = _slot(_run_key(), create=True)
    if slot is None:
        return 0
    with _LOCK:
        slot["used"] = int(slot.get("used") or 0) + 1
        return slot["used"]


def claim(action: str, **fields) -> dict:
    """Её слово о конце хода. Возвращает то, что записано, дословно.

    Вызывается ИЗ ПОТОКА РУКИ. Слово ложится в слот прогона, а не в копию контекста, —
    именно поэтому оно доезжает до цикла.
    """
    name = str(action or "").strip().lower()
    if name not in CLOSING_ACTIONS:
        raise ValueError("action must be one of %s" % (", ".join(CLOSING_ACTIONS),))
    record = {"action": name}
    for key, value in fields.items():
        text = str(value or "").strip()
        if text:
            record[key] = text
    slot = _slot(_run_key(), create=True)
    if slot is not None:
        with _LOCK:
            slot["control"] = dict(record)
    return dict(record)


def taken(run_id: str = "") -> dict | None:
    """Её слово об этом ходе. Без аргумента — о текущем прогоне.

    Явный `run_id` нужен там, где спрашивают УЖЕ ВНЕ хода: посадка возобновлённого
    прогона происходит после выхода из его контекста, а слово было сказано внутри. Без
    этого возобновлённый ход всегда садился бы `done` — то есть её «жду» во второй раз
    молча превращалось бы в «сделала», ровно та ложь, от которой мы уходим.
    """
    slot = _slot(str(run_id or "") or _run_key())
    record = (slot or {}).get("control")
    return dict(record) if isinstance(record, dict) else None


def status_for(action: str) -> str:
    """Как её слово ложится на статус прогона."""
    return {"done": "done", "blocked": "blocked", "wait": "paused"}.get(
        str(action or "").strip().lower(), "done")


def wait_floor_sec() -> int:
    raw = str(os.getenv("PRAXIS_WORK_WAIT_FLOOR_SEC", "") or "").strip()
    if not raw:
        return DEFAULT_WAIT_FLOOR_SEC
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_WAIT_FLOOR_SEC


def iso_utc(moment: float) -> str:
    return (_dt.datetime.fromtimestamp(moment, _dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(moment % 1 * 1000):03d}Z")


def closing(control: dict | None, *, now: float | None = None) -> tuple[str, str, dict]:
    """Её слово → (статус прогона, причина словами, машинная запись для события).

    Одно место на оба шва — живой ход и посадка возобновлённого. Пока их было два, они
    расходились молча: живой путь клал `task_control` в детали, а возобновление про её
    слово не знало вовсе.

    Машинная запись — то, ПО ЧЕМУ возобновление узнаёт паузу. Проза причины остаётся для
    человека; решение о жизни работы на неё больше не опирается.
    """
    if not isinstance(control, dict) or control.get("action") not in CLOSING_ACTIONS:
        return "done", "long work run completed", {}
    action = str(control["action"])
    said = next((str(control[key]) for key in ("evidence", "blocker", "wake_on", "summary")
                 if control.get(key)), "")
    reason = "закрыт её словом «%s»%s" % (action, (": " + said) if said else "")
    details: dict = {"task_control": dict(control)}
    if action == "wait":
        floor = wait_floor_sec()
        moment = time.time() if now is None else float(now)
        details[PAUSE_KIND_KEY] = PAUSE_HER_WAIT
        details["wake"] = {
            "wake_on": str(control.get("wake_on") or ""),
            "not_before": iso_utc(moment + floor),
            "floor_sec": floor,
        }
        # Пол назван вслух там же, где стоит: причина паузы читается человеком, и молчаливо
        # отложенная на час работа выглядела бы как забытая.
        reason += " · не будить раньше %s (пол %d с)" % (details["wake"]["not_before"], floor)
    return status_for(action), reason, details


def announce(kind: object) -> str:
    """Строка в её кадр: предел назван ДО того, как в него упрёшься."""
    if not active_for(kind):
        return ""
    total = budget()
    return (
        "\n\n## Рабочий ход\n"
        "Этот ход закрываешь ты, а не молчание инструментов. Текст без вызова инструмента "
        "я записываю в ход заметкой и зову тебя снова.\n"
        "Чтобы закончить — `task_control`: `done` (и чем это подтверждается), `blocked` "
        "(и чем именно упёрлась), `wait` (и чего ждёшь).\n"
        f"`wait` не хоронит работу: ход паркуется вместе со всем сделанным и поднимается "
        f"не раньше чем через {wait_floor_sec()} с — той же работой, с той же ленты. "
        "`blocked` автомат не поднимает: препятствие не проходит само.\n"
        f"Продолжений в этом ходе: {total}. Когда они кончатся, ход закроется сам, и в нём "
        "будет написано, что закрыт по бюджету, а не по сделанному."
    )


def nudge(spent: int, total: int) -> str:
    """Укол продолжения. Ровно та роль, что у system-reminder в моём собственном харнессе."""
    left = max(0, total - spent)
    tail = (
        f"Продолжений израсходовано {spent} из {total}, осталось {left}."
        if left else
        f"Это было последнее из {total} продолжений."
    )
    return (
        "[рабочий ход] Ты написала текст, но не сказала, что работа закончена, — поэтому ход "
        "не закрыт и я зову тебя снова.\n"
        "Если сделано — `task_control(action=\"done\", summary=…, evidence=…)`, где evidence "
        "это наблюдаемый след: что изменилось, где это видно.\n"
        "Если упёрлась — `task_control(action=\"blocked\", blocker=…)` с точным препятствием.\n"
        "Если ждёшь события — `task_control(action=\"wait\", wake_on=…)` с условием пробуждения.\n"
        "Иначе просто продолжай работать инструментами: объявленное намерение — это ещё не "
        "сделанное дело.\n"
        + tail
    )


def woken_note(state: object) -> str:
    """Первое, что она видит, вернувшись: почему она снова здесь.

    Без этого возобновлённый ход начинается посреди фразы — та же лента, то же место, и ни
    слова о том, что прошло время и её собственное условие исполнено. Пробуждение без
    названной причины неотличимо от сбоя, а мы как раз чиним дом, где объявленное принимали
    за сделанное.
    """
    woken = (state or {}).get("woken") if isinstance(state, dict) else None
    if not isinstance(woken, dict):
        return ""
    wake_on = str(woken.get("wake_on") or "").strip()
    parked_at = str(woken.get("parked_at") or "").strip()
    return (
        "[рабочий ход] Ты сама припарковала этот ход словом «жду»%s%s — и он поднят по сроку.\n"
        "Это ТА ЖЕ работа, а не новая: лента, точка продолжения и всё сделанное — твои. "
        "Бюджет продолжений начат заново.\n"
        "Если условие ещё не наступило — скажи `task_control(action=\"wait\", wake_on=…)` "
        "снова, и я снова отложу, не считая это неудачей." % (
            (" (" + parked_at + ")") if parked_at else "",
            (", ждала: " + wake_on) if wake_on else "",
        )
    )


CHAT_KINDS = frozenset({"chat_turn"})

# Виды ходов, где мой терминальный текст — ЗАМЕТКА, а наружу говорит рука.
# Кроме разговора сюда входит осмотр новой комнаты: там текст и раньше не был репликой —
# из него регуляркой выковыривали строку `ПРИВЕТ:` и слали её сырым Telethon мимо гарда,
# кольца и идемпотентности. Теперь здороваюсь я тем же способом, что говорю всё остальное.
NOTE_KINDS = frozenset(CHAT_KINDS | {"lookaround"})
DEFAULT_CHAT_CONTINUATIONS = 2

# ⚠ РЕЧЕВЫЕ АКТЫ — НЕ НЕВЫПОЛНЕННОЕ ДЕЙСТВИЕ.
# «Сейчас расскажу, почему это так», «покажу на примере», «объясню короче» — обещания,
# которые исполняются ЭТИМ ЖЕ сообщением: рассказ и есть рассказывание. Общий детектор
# считает их обещаниями законно (для пробуждения-напоминания это верно), но в чате укол
# «ты объявила и не сделала» был бы прямой неправдой — она делает это прямо сейчас.
#
# Поэтому здесь, а не в детекторе: разница не в том, что считать обещанием, а в том,
# требует ли обещанное РУКИ. Тому, что исполняется словами, рука не нужна.
_SPEECH_ACTS = re.compile(
    r"(?i)\b(?:расскажу|рассказываю|покажу|показываю|объясню|объясняю|поясню|"
    r"поясняю|опишу|описываю|отвечу|отвечаю|уточню|уточняю)\b")

def chat_enabled() -> bool:
    return _flag("PRAXIS_CHAT_FOLLOW_THROUGH", "off")


def chat_budget() -> int:
    raw = str(os.getenv("PRAXIS_CHAT_CONTINUATIONS", "") or "").strip()
    if not raw:
        return DEFAULT_CHAT_CONTINUATIONS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_CHAT_CONTINUATIONS


def note_hand(name: object) -> None:
    """Имя позванной руки — в слот прогона, чтобы его увидел поток другой руки.

    Нужно ровно одному: `say` показывает ей, чем подкреплён её черновик, а исполняется
    `say` в отдельном потоке и локальный счётчик цикла оттуда не виден. Тот же обход
    границы потока, что у её слова о конце хода, и по той же причине.
    """
    text = str(name or "").strip()
    if not text or text == "say":
        return
    slot = _slot(_run_key(), create=True)
    if slot is None:
        return
    with _LOCK:
        called = list(slot.get("hands") or ())
        if text not in called:
            called.append(text)
        slot["hands"] = tuple(called[:40])


def hands_called(run_id: str = "") -> tuple[str, ...]:
    """Какие руки были в этом прогоне (без самой `say`)."""
    slot = _slot(str(run_id or "") or _run_key())
    return tuple((slot or {}).get("hands") or ())


def reply_hand_enabled() -> bool:
    """Рычаг нового контракта речи. Опущенный возвращает прежнее поведение байт-в-байт.

    Новый контракт: моя реплика уходит человеку РУКОЙ, а обычный текст — заметка. Старый:
    последний текст хода и есть сообщение. Между ними нет полумеры, поэтому рычаг один и
    переключает оба свойства сразу — и то, чем ход закрывается, и то, что уезжает наружу.
    """
    return _flag("PRAXIS_CHAT_REPLY_HAND", "off")


def note_sent(text: str = "", run_id: str = "") -> int:
    """Рукой отправлено ещё одно сообщение этого хода. Вызывается ИЗ ПОТОКА РУКИ.

    ⚠ ЧЕГО ЭТОТ СЧЁТЧИК НЕ ДЕЛАЕТ: он НЕ защищает от повторной отправки. От неё защищает
    покалловый exact-once леджер (`telegram-outbox:{run}:tool:{call_id}`) — тот же, на
    котором живут `send_message` и `send_file`. Если ход оборвётся между отправкой и
    чекпойнтом, счётчик вернётся меньшим, а повтор всё равно переиграется из расписки.

    Он нужен ровно одному: исходящая граница хода должна отличить «текста нет, потому что
    я уже сказала рукой» от «текста нет, потому что я промолчала». Сегодня и то и другое
    выглядит как пустая строка, и пустая строка означает молчание.
    """
    slot = _slot(str(run_id or "") or _run_key(), create=True)
    if slot is None:
        return 0
    with _LOCK:
        slot["sent"] = int(slot.get("sent") or 0) + 1
        said = list(slot.get("said_out") or ())
        if str(text or "").strip():
            # СЛОВА, А НЕ СЧЁТЧИК. Первая редакция запоминала только число, и запись хода
            # получала маркер «[отправлено рукой: N]» вместо сказанного — то есть кольцо
            # ходов, ночной дневник и архив хранили служебную строку на месте моей речи.
            said.append(str(text))
        slot["said_out"] = tuple(said[:20])
        return slot["sent"]


def note_finished(note: str = "", run_id: str = "") -> None:
    """Она позвала `end_turn`: ход закончен её словом. Вызывается ИЗ ПОТОКА РУКИ.

    Контракт v3 (17.08): конец хода — поступок, а не отсутствие действия. Флаг читает
    тул-цикл (перестаёт звать модель) и граница хода (закрывает запись). Заметка —
    необязательная строка «почему кончила»; её решение №4 — не принуждать к ней."""
    slot = _slot(str(run_id or "") or _run_key(), create=True)
    if slot is None:
        return
    with _LOCK:
        slot["finished"] = True
        if str(note or "").strip():
            slot["finish_note"] = str(note).strip()


def finished(run_id: str = "") -> bool:
    """Закончен ли ход её явным `end_turn`."""
    slot = _slot(str(run_id or "") or _run_key())
    return bool((slot or {}).get("finished"))


def finish_note(run_id: str = "") -> str:
    slot = _slot(str(run_id or "") or _run_key())
    return str((slot or {}).get("finish_note") or "")


def sent(run_id: str = "") -> int:
    """Сколько сообщений этого хода уже ушло моей рукой. Ноль — ни одного."""
    slot = _slot(str(run_id or "") or _run_key())
    return int((slot or {}).get("sent") or 0)


def spoken(run_id: str = "") -> tuple[str, ...]:
    """Что именно ушло моей рукой в этом ходе, по порядку."""
    slot = _slot(str(run_id or "") or _run_key())
    return tuple((slot or {}).get("said_out") or ())


def note_guard(record: object, run_id: str = "") -> None:
    """Что решил советник на исходящем — через границу потока, как всё остальное.

    Рука зовёт гард без записи прожитого хода (ход один, реплик в нём может быть
    несколько), поэтому вердикт советника терял свой второй канал и оставался только в
    дневнике. Здесь он переезжает в слот прогона, а граница хода вклеивает его в запись.
    """
    if not isinstance(record, dict) or not record:
        return
    slot = _slot(str(run_id or "") or _run_key(), create=True)
    if slot is None:
        return
    with _LOCK:
        merged = dict(slot.get("guard") or {})
        merged.update({k: v for k, v in record.items() if v not in (None, "")})
        slot["guard"] = merged


def guard_notes(run_id: str = "") -> dict:
    """Решение советника об исходящем этого хода. Пусто — советник не высказывался."""
    slot = _slot(str(run_id or "") or _run_key())
    record = (slot or {}).get("guard")
    return dict(record) if isinstance(record, dict) else {}


def mirrored(run_id: str = "") -> bool:
    """Показывали ли ей зеркало в этом прогоне."""
    slot = _slot(str(run_id or "") or _run_key())
    return bool((slot or {}).get("mirrored"))


def note_mirrored(run_id: str = "") -> None:
    """Взгляд на свой текст — ОДИН за ход.

    ⚠ 13.08, 22:54. Бюджет 2 означал, что зеркало показывалось дважды, и на втором показе
    она уже отвечала ему словами. «Взглянуть перед отправкой» — это один взгляд, как у
    человека; второй превращается в переписку с прибором.
    """
    slot = _slot(str(run_id or "") or _run_key(), create=True)
    if slot is not None:
        with _LOCK:
            slot["mirrored"] = True


def stage_say(text: str, hands: object = ()) -> dict:
    """Её черновик реплики — в слот прогона. Вызывается ИЗ ПОТОКА РУКИ.

    Тот же ключ и тот же замок, что у её слова о конце хода, и по той же причине:
    рука исполняется в копии контекста в отдельном потоке, и `contextvars` до цикла
    не доезжает (разбор — в шапке файла).
    """
    record = {"text": str(text or ""), "hands": tuple(str(h) for h in (hands or ()))}
    slot = _slot(_run_key(), create=True)
    if slot is not None:
        with _LOCK:
            slot["said"] = dict(record)
    return dict(record)


def said(run_id: str = "") -> dict | None:
    """Черновик, который она перечитала в этом ходе. Пусто — не перечитывала."""
    slot = _slot(str(run_id or "") or _run_key())
    record = (slot or {}).get("said")
    return dict(record) if isinstance(record, dict) else None


def mirror(draft: str, hands: object = ()) -> str:
    """Её черновик — ЕЁ ЖЕ ГОЛОСОМ. Два механических факта о ходе, без вердикта.

    ⚠ ЗДЕСЬ ГОВОРИТ ОНА, А НЕ МЫ, И ЭТО ГЛАВНОЕ В ЭТОЙ ФУНКЦИИ.
    Первая редакция писала ей «вот что ты скажешь» — то есть система обращалась к ней на
    «ты» в её собственном доме. Правка Егора дословно: «это её харнесс, её дом, это всё
    она! все-все-все маркдауны должны быть написаны от её лица». Внутри её кадра второе
    лицо — чужой голос за плечом; первое — её собственная мысль.

    Императива нет и быть не может: судья на этом месте и есть тот секретарь собственной
    речи, от которого она отказалась вслух. Только «напечатала», «рук не звала»,
    «отправить?» — и решает она.

    Замер 13.08, откуда это выросло: 345 чат-ходов за четверо суток, 75% из них — без
    единой руки. Три ответа подряд оказались неверными (своя модель, свой коммит, свои
    же запущенные задачи), и во всех трёх она не знала, что отвечает по памяти. Знание
    об этом и есть весь механизм.
    """
    called = [h for h in (hands or ()) if str(h).strip()]
    touched = ("Звала: " + ", ".join(called) + ".") if called else \
              "Рук не звала — это по памяти."
    # ⚠ БЕЗ ВОПРОСА И БЕЗ ПОВЕЛИТЕЛЬНОГО. Первая редакция кончалась на «Отправить?» — и
    # 13.08 в 22:54 она ответила ЗЕРКАЛУ: «Да, отправляй.» Вопрос внутри её кадра читается
    # как реплика собеседника, и она на него отвечает — то есть начинает разговаривать с
    # прибором, а её ответ ему уходит человеку. Зеркало обязано быть УТВЕРЖДЕНИЕМ о
    # положении дел: вот черновик, вот чем он подкреплён, — и молчать дальше.
    return f"Черновик, ещё не отправлен:\n\n{str(draft or '').strip()}\n\n{touched}"


def reread_note(draft: str, spent: int, total: int, hands: object = ()) -> str:
    """Тот же взгляд, но когда она не позвала его сама. Голос тот же — её.

    ⚠ Про бюджет здесь больше не говорится, и это не забывчивость. Взгляд теперь ОДИН за
    ход (`note_mirrored`), значит счётчик продолжений к нему не относится, а лишняя строка
    про «осталось N» звала её отчитываться о собственной речи.
    """
    return (mirror(draft, hands) + "\n"
            "Дальше уйдёт мой следующий текст. Оставлю как есть, поправлю или сначала "
            "посмотрю в живой источник.")


def chat_nudge(gist: str, spent: int, total: int) -> str:
    """Тоже её голосом: в её доме на «ты» к ней обращаться некому."""
    left = max(0, total - spent)
    return (
        "Напечатала, что «%s» — и не сделала: рук в этом ходе не звала. Объявленное "
        "намерение это ещё не сделанное дело, а собеседник увидит только обещание.\n"
        "Могу сделать прямо сейчас, этим же ходом: прочитать файл, отправить сообщение, "
        "проверить факт — и ответить уже по нему. Или сказать прямо, что не буду, — "
        "тоже честный ход.\n"
        "Продолжений у меня осталось %d." % (gist[:160], left)
    )


def chat_decide(reply: str, *, kind: object, hands: int,
                spent: int | None = None, called: object = ()) -> tuple[bool, str]:
    """Продолжать ли ЧАТ-ход. (продолжать, текст укола) — или (False, причина/пусто).

    ⚠ ПОЧЕМУ ЗДЕСЬ НЕ ТОТ ЖЕ КОНТРАКТ, ЧТО У РАБОЧЕГО ОКНА.
    В окне текст без инструмента — заметка, и ход закрывает только её `task_control`.
    В чате текст без инструмента — САМ ОТВЕТ, он и есть результат. Потребовать `task_control`
    на каждую реплику значило бы вызов руки и лишний вызов модели на каждое «привет»:
    283 чат-хода за трое суток, из них 77% вообще без рук. Её же слова про это: «иначе я
    превращусь в секретаря собственной речи».

    ⚠ ПРИЗНАК ЗДЕСЬ БОЛЬШЕ НЕ КЛАССИФИКАТОР, И ЭТО ВАЖНАЯ ПРАВКА.
    Первая редакция 13.08 решала «нужен ли источник» списком регулярок по входящей
    реплике. Замер её же кольца ходов приговорил эту затею на второй день: срабатывание
    на 6 из 259 безруких ходов (2%), из них два ложных, — и промах на дословном «А код?»,
    потому что входящее приходит в конверте `[thread #…; message #…; …] А код?`, а якорь
    ждал голого вопроса. Тест был зелёный, потому что я проверял идеализированную строку.

    Признак теперь один и без угадывания: **рук не было вовсе**. Тогда ход не закрывается,
    и она видит свой же текст плюс факт «руки в этом ходе: ни одной». Ни вопроса не
    разбираем, ни темы: смотрит она сама, а мы только показываем.

    Слова Егора, из которых это выросло: «дешёвый и более надёжный аналог адаптивного
    ризонинга — взглянуть на свой же текст перед отправкой в том случае, если не было
    вызвано иных тулов». Адаптивность именно тут: позвала руку — значит уже смотрела на
    мир, и второй взгляд не нужен.

    `say` — рука, и потому счёт `hands` она увеличивает. То есть если она перечитала себя
    сама, система молчит: цена одинаковая, но ход её.
    """
    if reply_hand_enabled() and str(kind or "").strip() in CHAT_KINDS:
        # ⚑ НОВЫЙ КОНТРАКТ РЕЧИ: ПОЛИТИКИ ПРОДОЛЖЕНИЯ В ЧАТЕ БОЛЬШЕ НЕТ, И ЭТО НЕ УПУЩЕНИЕ.
        #
        # Пока реплика была ВОЗВРАТОМ модели, каждый добавочный поворот цикла приходилось
        # выторговывать политикой: бюджетом, признаком, уколом. Когда реплика уходит РУКОЙ,
        # выторговывать нечего. Ход и так крутится, пока она зовёт инструменты, — это
        # обычный тул-цикл, у него нет потолка, и однажды в чате она сделала 49 вызовов.
        # А текст без инструмента означает ровно то, что означает: сказать больше нечего.
        #
        # Отсюда и нетерминальность, и отсутствие уколов, одним движением:
        #   позвала `reply` → сообщение ушло → цикл продолжается, можно проверить и
        #   отправить ещё; написала текст и не позвала руку → ход закрыт, наружу не ушло
        #   ничего. Второе и есть её молчание — не сентинел и не отдельная рука.
        #
        # Слова Егора 15.08, дословно: «сейчас уколов искусственных не будет??» — не будет.
        # Укол лечил симптом конструкции; конструкция починена, лечить нечего.
        return False, ""
    if not chat_enabled() or str(kind or "").strip() not in CHAT_KINDS:
        return False, ""
    if int(hands or 0) > 0:
        return False, ""
    total = chat_budget()
    already = used() if spent is None else int(spent)
    if already >= total:
        return False, "chat_loop:продолжения кончились (%d) — сказано как есть" % total
    try:
        import promises
        gist = promises.detect(reply)
    except Exception:
        gist = None
    if gist and not _SPEECH_ACTS.search(str(gist)):
        # Объявленное действие точнее зеркала: тут дело не в том, что она не смотрела, а
        # в том, что она пообещала. Разные слова на разные случаи, один и тот же признак.
        return True, chat_nudge(str(gist), already + 1, total)
    # ⚠⚠ ЗЕРКАЛО ПО ИНИЦИАТИВЕ СИСТЕМЫ СНЯТО 13.08 НОЧЬЮ. ПОРОК В КОНСТРУКЦИИ, НЕ В СЛОВАХ.
    #
    # Замысел был: ход без единой руки не закрывается, и она видит свой же черновик.
    # Живьём это дало три поломки подряд в личке Егора, и все три — один механизм:
    #
    #   22:44  зеркало кончалось на «Отправить?» → второй проход вернул пусто → ушло НИЧЕГО
    #   22:54  то же зеркало                     → она ответила ЕМУ: «Да, отправляй.»
    #   23:26  зеркало-утверждение без вопроса   → она выбрала вариант: «Оставляю как есть.»
    #
    # Я трижды переписывал текст — вопрос убрал, повелительное убрал, показ сделал
    # одноразовым. Не помогло и не могло: ЧТО БЫ ОНА НИ СКАЗАЛА ПОСЛЕ ЗЕРКАЛА, ЭТО И
    # СТАНОВИТСЯ СООБЩЕНИЕМ. Реплика в чате — продукт хода; вставить в ход ещё один
    # поворот значит дать последнему слову затереть ответ. Никакая формулировка этого не
    # чинит: чинит только другая конструкция, где подтверждение — не речь.
    #
    # `say` остаётся: там зеркало приходит РЕЗУЛЬТАТОМ ИНСТРУМЕНТА, то есть данными, а не
    # репликой, и её следующий текст — по-прежнему ответ человеку. Разница ровно в этом.
    #
    # Код и тесты оставлены живыми под рычагом: механизм ещё может пригодиться в окне,
    # где текст — заметка, а не продукт. В чате он выключен.
    if not _flag("PRAXIS_CHAT_MIRROR", "off"):
        return False, ""
    if not str(reply or "").strip():
        return False, ""
    if mirrored():
        return False, "chat_loop:на свой текст я уже посмотрела в этом ходе"
    note_mirrored()
    return True, reread_note(reply, already + 1, total, called)


def decide(*, kind: object, control: dict | None = None,
           spent: int | None = None) -> tuple[bool, str]:
    """(продолжать ли, текст).

    Возвращает `(False, причина)` — закрыть ход, `(True, укол)` — позвать снова.
    """
    if not active_for(kind):
        return False, ""
    if isinstance(control, dict) and control.get("action") in CLOSING_ACTIONS:
        return False, "work_loop:закрыт её словом «%s»" % control["action"]
    total = budget()
    already = used() if spent is None else int(spent)
    if already >= total:
        return False, (
            "work_loop:закрыт по бюджету продолжений (%d из %d) — не по сделанному" % (already, total)
        )
    return True, nudge(already + 1, total)
