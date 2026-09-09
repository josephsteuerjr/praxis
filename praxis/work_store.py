"""Операционный слой работы: состояние ЭТОГО прогона и ссылка на желание.

⚠ ЧТО ЗДЕСЬ НЕ КАНОН — И ЭТО ЕЁ РЕШЕНИЕ, ДОСЛОВНО (12.08.2026):

    «Желания — мой канон: там уже есть причинная цепочка, следующий ход и связи с
    прогонами. Делать параллельную "доску истины" было бы ровно тем расщеплением, от
    которого мы только что лечились. work_store пусть остаётся узким operational-слоем:
    активная работа конкретного прогона, её attention/отсрочка/состояние и ссылка на
    желание. Не второй жизнью работы.»

Поэтому здесь НЕТ второй жизни работы. Чем она занята, зачем и что дальше — живёт в
`memory/desires` (там `next_move`, `evidence_refs`, `run_ids` и причинная цепочка с
15.07.2026). Здесь — только то, чего в каноне нет и быть не должно: сколько раз движок
поднимал эту работу вхолостую, до какого срока её не будить, не просила ли она не брать
её сама. Канон пишет ОНА; сюда пишет автомат.

ИСТОРИЯ ОШИБКИ, чтобы не повторить.  11.08 я записал, что «сшивки N активаций в одну
работу не существует нигде», и 12.08 построил её заново — при живом леджере желаний,
устроенном ровно так же (канон в `events.jsonl`, проекция в markdown, которую можно
пересобрать). Проверять надо было раньше, чем строить.

ПОЧЕМУ ФАЙЛЫ, А НЕ БАЗА.  Дословное решение владельца: «из памяти и контекста мы
выкорчёвывали SQLite несколько дней». Markdown — канон состояния, JSONL — история,
файловая система — очередь. Всё читается её же руками (`fs_read`) и человеком, без клиента.

ГДЕ КАРТОЧКА ЗАВОДИТСЯ И ПОЧЕМУ ИМЕННО ТАМ.  Не на каждый ход: слой, который никто не
читает, — это ещё одна ложь о себе. Карточка рождается в тот миг, когда работа НЕ
кончилась: она сказала `wait` или `blocked`. Ровно тогда работе и нужно пережить прогон.
Сказала `done` — карточка не нужна, работа закончилась вместе с ходом; но если карточка
уже была, её закрывает то же слово.

ПРОТОКОЛ ЗАПИСИ — СОБЫТИЕ ПЕРВЫМ.  `intent` в журнал → атомарная замена `TASK.md` →
`committed`. Восстановление НЕ УГАДЫВАЕТ: если хвост журнала — `intent` без `committed`,
оно сверяет карточку с намерением и либо дописывает `committed`, либо ставит `attention`.
Смысл в том, что «я не знаю, что случилось» должно быть выразимо; тихая догадка — нет.

⚠ ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ.  Планировщика. Эта доска умеет хранить и отвечать «что
открыто», но никого не будит. Кто заводит следующий оборот — отдельная работа, и смешивать
их значит получить доску, которая тихо стала часами.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import uuid
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)

SCHEMA = "praxis.work.card.v1"
EVENT_SCHEMA = "praxis.work.event.v1"

# Статус отвечает «можно ли это исполнять», фаза — «чем работа занята». Их путали, и от
# этого «blocked» означало то препятствие, то стадию.
STATUSES = ("inbox", "ready", "running", "waiting", "blocked",
            "verifying", "done", "cancelled", "failed")
TERMINAL = frozenset({"done", "cancelled", "failed"})
PHASES = ("orient", "plan", "execute", "verify", "integrate", "deliver")

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONT = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)


class WorkConflict(RuntimeError):
    """Карточку изменили между чтением и записью. Ревизия — не украшение."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{_dt.datetime.now(_dt.timezone.utc).microsecond // 1000:03d}Z"


def _root() -> Path:
    return BASE / "memory" / "work"


def _dir(task_id: str) -> Path:
    if not _ID.fullmatch(str(task_id or "")):
        raise ValueError("task id must be a short filesystem-safe slug")
    return _root() / "tasks" / task_id


# ── сериализация карточки ────────────────────────────────────────────────────────────
# Frontmatter — не YAML-библиотека, а плоские `ключ: json-значение`. Причина простая:
# зависимость ради пяти типов не нужна, а `json.dumps` даёт однозначное чтение туда и
# обратно, включая кириллицу и переводы строк внутри значений.

def _dump(card: dict, body: str) -> str:
    lines = ["---"]
    for key in sorted(card):
        lines.append("%s: %s" % (key, json.dumps(card[key], ensure_ascii=False)))
    lines.append("---")
    return "\n".join(lines) + "\n" + (body or "")


def _parse(text: str) -> tuple[dict, str]:
    match = _FRONT.match(text or "")
    if not match:
        return {}, str(text or "")
    card: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        try:
            card[key.strip()] = json.loads(raw.strip())
        except Exception:
            card[key.strip()] = raw.strip()
    return card, match.group(2)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _append(task_id: str, kind: str, **fields) -> dict:
    row = {"schema": EVENT_SCHEMA, "kind": kind, "at": _now(), **fields}
    path = _dir(task_id) / "events.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def events(task_id: str) -> list[dict]:
    path = _dir(task_id) / "events.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


# ── замок ────────────────────────────────────────────────────────────────────────────

class _Lock:
    """Тот же замок, что у прогонов: ядро, а не соглашение. Своей реализации не пишем —
    две реализации одного замка расходятся молча, и узнаёшь об этом по потерянной записи."""

    def __init__(self, task_dir: Path):
        self.path = task_dir / ".lock"
        self.fd: int | None = None

    def __enter__(self):
        import run_manager
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        for _ in range(200):
            if run_manager._try_advisory_lock(self.fd):
                return self
            import time
            time.sleep(0.01)
        os.close(self.fd)
        self.fd = None
        raise WorkConflict("card lock is held by another writer")

    def __exit__(self, *exc):
        if self.fd is not None:
            import run_manager
            run_manager._release_advisory_lock(self.fd)
            os.close(self.fd)
            self.fd = None
        return False


# ── чтение ───────────────────────────────────────────────────────────────────────────

def card(task_id: str) -> dict | None:
    path = _dir(task_id) / "TASK.md"
    if not path.is_file():
        return None
    data, body = _parse(path.read_text(encoding="utf-8", errors="replace"))
    data["body"] = body
    return data


def task_ids() -> list[str]:
    root = _root() / "tasks"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and _ID.fullmatch(p.name))


def open_cards() -> list[dict]:
    """Что ещё не закрыто. Порядок — от давнего к свежему: старое не должно тонуть."""
    rows = [card(tid) for tid in task_ids()]
    rows = [r for r in rows if r and str(r.get("status")) not in TERMINAL]
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""))


def for_desire(desire_id: str) -> dict | None:
    """Операционная карточка этого желания, если автомат её уже заводил."""
    want = str(desire_id or "")
    if not want:
        return None
    rows = [row for row in (card(tid) for tid in task_ids())
            if row and str(row.get("desire_id") or "") == want]
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""))[-1] if rows else None


def note_attempt(task_id: str, *, at: str = "", fingerprint: object = None) -> dict:
    """Подъём был. Считается ФАКТ попытки, а не текст причины.

    Дом четырежды ловил вечную петлю и трижды лечил её списком постоянных отказов — список
    по построению отстаёт от мира. Поэтому счёт ведётся здесь, до исполнения: упавший
    посреди хода процесс обязан оставить попытку записанной, иначе следующий круг начнётся
    с нуля и петля вернётся пятый раз.
    """
    task_dir = _dir(task_id)
    with _Lock(task_dir):
        row = card(task_id)
        if row is None:
            raise WorkConflict("нет такой карточки: %s" % task_id)
        body = row.pop("body", "")
        row["attempts"] = int(row.get("attempts") or 0) + 1
        row["last_attempt"] = str(at or _now())
        if fingerprint is not None:
            # Отпечаток канона НА МОМЕНТ ПОДЪЁМА. Сравнение с ним — единственный способ
            # узнать «сдвинулось ли» по ЕЁ записям, а не по нашим: проверять собственное
            # продвижение своими же отметками значит судить себя в своей онтологии.
            row["fingerprint"] = list(fingerprint)
        row["revision"] = int(row.get("revision") or 0) + 1
        row["updated_at"] = _now()
        _append(task_id, "intent", attempt=row["attempts"], revision=row["revision"])
        _atomic_write(task_dir / "TASK.md", _dump(row, body))
        _append(task_id, "committed", revision=row["revision"])
    return row


def settle(task_id: str, *, moved: bool) -> dict | None:
    """Работа сдвинулась — счёт холостых попыток начинается заново.

    Сдвиг определяет ВЫЗЫВАЮЩИЙ по канону (её леджеру), а не эта функция: угадывать
    продвижение по своим же записям значит проверять себя в собственной онтологии.
    """
    if not moved:
        return card(task_id)
    task_dir = _dir(task_id)
    with _Lock(task_dir):
        row = card(task_id)
        if row is None or not int(row.get("attempts") or 0):
            return row
        body = row.pop("body", "")
        row["attempts"] = 0
        row["revision"] = int(row.get("revision") or 0) + 1
        row["updated_at"] = _now()
        _append(task_id, "intent", attempts_reset=True, revision=row["revision"])
        _atomic_write(task_dir / "TASK.md", _dump(row, body))
        _append(task_id, "committed", revision=row["revision"])
    return row


def for_run(run_id: str) -> dict | None:
    """Карточка, которой принадлежит этот прогон. Сшивка, которой не было нигде."""
    want = str(run_id or "")
    if not want:
        return None
    for tid in task_ids():
        row = card(tid)
        if row and want in (row.get("run_ids") or ()):
            return row
    return None


# ── запись ───────────────────────────────────────────────────────────────────────────

def open_card(*, goal: str, kind: str = "work", run_id: str = "",
              dod: list[str] | None = None, origin: str = "",
              desire_id: str = "") -> dict:
    """Завести операционную карточку. `goal` здесь — КОПИЯ для читаемости, а не канон.

    `desire_id` — ссылка на канон. Пустая ссылка законна: не всякая операционная работа
    выросла из названного желания, и выдумывать ей желание автомат не вправе.
    """
    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("работа без цели — не работа")
    task_id = "%s-%s" % (
        (re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:24] or "work"),
        uuid.uuid4().hex[:8],
    )
    task_dir = _dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    now = _now()
    row = {
        "schema": SCHEMA, "id": task_id, "goal": goal, "kind": kind,
        "status": "running", "phase": "execute", "revision": 1,
        "created_at": now, "updated_at": now,
        "run_ids": [run_id] if run_id else [],
        "dod": list(dod or []), "origin": str(origin or ""),
        "evidence": [], "wake": {}, "blocker": "",
        # Операционное — то, чего в её каноне нет и быть не должно.
        "desire_id": str(desire_id or ""),
        "attempts": 0, "last_attempt": "", "frozen_by_her": False,
    }
    with _Lock(task_dir):
        _append(task_id, "intent", to_status="running", revision=1, goal=goal)
        _atomic_write(task_dir / "TASK.md", _dump(row, _body_header(row)))
        _append(task_id, "committed", revision=1)
    return row


def _body_header(row: dict) -> str:
    return (
        "\n# %s\n\n"
        "> Карточка работы. Канон состояния — заголовок выше, история — `events.jsonl`,\n"
        "> доска `../../BOARD.md` только проекция. Заметки ниже — в порядке появления.\n"
        % row["goal"]
    )


def attach_run(task_id: str, run_id: str) -> dict:
    """Ещё одна активация той же работы. Это и есть шов между оборотами."""
    run_id = str(run_id or "")
    task_dir = _dir(task_id)
    with _Lock(task_dir):
        row = card(task_id)
        if row is None:
            raise WorkConflict("нет такой карточки: %s" % task_id)
        body = row.pop("body", "")
        runs = list(row.get("run_ids") or [])
        if run_id and run_id not in runs:
            runs.append(run_id)
        row["run_ids"] = runs
        row["revision"] = int(row.get("revision") or 0) + 1
        row["updated_at"] = _now()
        _append(task_id, "intent", run_id=run_id, revision=row["revision"])
        _atomic_write(task_dir / "TASK.md", _dump(row, body))
        _append(task_id, "committed", revision=row["revision"])
    return row


def note(task_id: str, text: str, *, author: str = "praxis") -> None:
    """Обычный текст модели — заметка в работе, а не команда управления.

    Ровно то, чего не хватало: сказанное между делом переставало существовать, как только
    ход закрывался. Теперь оно ложится в тело карточки и переживает прогон.
    """
    text = str(text or "").strip()
    if not text:
        return
    task_dir = _dir(task_id)
    with _Lock(task_dir):
        row = card(task_id)
        if row is None:
            return
        body = row.pop("body", "")
        row["revision"] = int(row.get("revision") or 0) + 1
        row["updated_at"] = _now()
        stamped = "%s\n\n## %s · %s\n\n%s\n" % (body.rstrip(), _now(), author, text)
        _append(task_id, "intent", note_chars=len(text), revision=row["revision"])
        _atomic_write(task_dir / "TASK.md", _dump(row, stamped))
        _append(task_id, "committed", revision=row["revision"])


def transition(task_id: str, status: str, *, phase: str = "", reason: str = "",
               evidence: str = "", blocker: str = "", wake: dict | None = None,
               expected_revision: int | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError("unknown work status %r" % status)
    if phase and phase not in PHASES:
        raise ValueError("unknown work phase %r" % phase)
    task_dir = _dir(task_id)
    with _Lock(task_dir):
        row = card(task_id)
        if row is None:
            raise WorkConflict("нет такой карточки: %s" % task_id)
        body = row.pop("body", "")
        current = int(row.get("revision") or 0)
        if expected_revision is not None and int(expected_revision) != current:
            raise WorkConflict(
                "карточка ушла вперёд: ожидалась ревизия %s, на диске %s"
                % (expected_revision, current))
        before = str(row.get("status") or "")
        row["status"] = status
        if phase:
            row["phase"] = phase
        if blocker or status == "blocked":
            row["blocker"] = str(blocker or row.get("blocker") or "")
        if wake is not None:
            row["wake"] = dict(wake)
        if evidence:
            row["evidence"] = list(row.get("evidence") or []) + [str(evidence)]
        row["revision"] = current + 1
        row["updated_at"] = _now()
        if status in TERMINAL:
            row["closed_at"] = row["updated_at"]
        _append(task_id, "intent", from_status=before, to_status=status,
                reason=str(reason or ""), revision=row["revision"])
        _atomic_write(task_dir / "TASK.md", _dump(row, body))
        _append(task_id, "committed", revision=row["revision"])
    return row


# ── DoD ──────────────────────────────────────────────────────────────────────────────

def dod_verdict(row: dict | None, evidence: str) -> tuple[bool, str]:
    """«Сделано» — это запрос, а не объявление. Возвращает (принять ли, чем ответить).

    Правило одно и оно её же: у результата должен быть наблюдаемый след. Никакой оценки
    качества здесь нет и быть не может — код не умеет судить, хорошо ли сделано. Он умеет
    только заметить, что доказательства не назвали вовсе.

    ⚠ Это НЕ детское ограничение из страха: отказ не отнимает у неё ход и не выносит
    приговор работе. Он говорит «назови след» и возвращает ей ход, чтобы она его назвала.
    """
    said = str(evidence or "").strip()
    if not said:
        return False, ("«сделано» без единого следа. Назови наблюдаемое: что изменилось и "
                       "где это видно — файл, вывод команды, число, расписка.")
    criteria = [str(c).strip() for c in ((row or {}).get("dod") or []) if str(c).strip()]
    if not criteria:
        return True, ""
    # Критерии проверяет она, а не регулярка: код лишь напоминает их дословно, если след
    # заметно короче списка, который она сама себе назначила.
    if len(said) < 40 and len(criteria) > 1:
        return False, ("названные тобою критерии: %s. След короче, чем список — сверься и "
                       "скажи по каждому." % "; ".join(criteria))
    return True, ""


# ── проекция и восстановление ────────────────────────────────────────────────────────

def board() -> str:
    """BOARD.md — генерируемая доска. НЕ источник истины, и это написано на ней самой."""
    rows = [card(tid) for tid in task_ids()]
    rows = [r for r in rows if r]
    lines = [
        "# Доска работ",
        "",
        "> Сгенерировано из карточек. **Источник истины — `tasks/<id>/TASK.md`**, здесь",
        "> только проекция: если файл спорит с карточкой, прав карточка.",
        "",
    ]
    for status in STATUSES:
        here = [r for r in rows if str(r.get("status")) == status]
        if not here:
            continue
        lines.append("## %s (%d)" % (status, len(here)))
        lines.append("")
        for r in sorted(here, key=lambda x: str(x.get("updated_at") or "")):
            tail = ""
            if r.get("blocker"):
                tail = " — упёрлась: %s" % str(r["blocker"])[:120]
            elif (r.get("wake") or {}).get("wake_on"):
                tail = " — ждёт: %s" % str(r["wake"]["wake_on"])[:120]
            lines.append("- `%s` **%s** · фаза %s · активаций %d%s" % (
                r.get("id"), str(r.get("goal"))[:90], r.get("phase"),
                len(r.get("run_ids") or ()), tail))
        lines.append("")
    if len(lines) == 5:
        lines.append("_Открытых работ нет._")
    return "\n".join(lines) + "\n"


def write_board() -> Path:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "BOARD.md"
    _atomic_write(path, board())
    return path


def recover() -> list[dict]:
    """Хвост `intent` без `committed` — не повод угадать. Сверяем и говорим вслух."""
    reports = []
    for task_id in task_ids():
        rows = events(task_id)
        if not rows or rows[-1].get("kind") != "intent":
            continue
        row = card(task_id)
        intent = rows[-1]
        landed = row is not None and int(row.get("revision") or 0) >= int(
            intent.get("revision") or 0)
        if landed:
            _append(task_id, "committed", revision=int(row.get("revision") or 0),
                    recovered=True)
            reports.append({"id": task_id, "outcome": "committed_after_restart"})
            continue
        _append(task_id, "attention",
                reason="запись не долетела: намерение есть, карточка его не показывает",
                intent_revision=intent.get("revision"))
        reports.append({"id": task_id, "outcome": "attention"})
    return reports
