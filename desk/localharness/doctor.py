# -*- coding: utf-8 -*-
"""Доктор Hélène — уровень 0: CLI-движок диагноза установки и агентов.

    python doctor.py status  [--base <установка>] [--agent <id>]   машиночитаемо
    python doctor.py explain [--base <установка>] [--agent <id>]   словами владельца

Зачем. У установки есть приборы — beat-файлы, квитанции, журналы, дневник, — но
до сих пор их читал каждый по-своему: окно рисует шапку, служба смотрит за
детьми, владелец ходит по журналам руками. Когда что-то ломается, первый вопрос
не «как чинить», а «что вообще происходит» — и на него не отвечал никто одним
голосом. Доктор отвечает.

Он живёт в слое (app/, не в дереве пациента) и НИЧЕГО не пишет в обследуемое
дерево: только читает готовые приборы. Импортировать канал нельзя — он отдельный
процесс со своей памятью; вместо этого доктор читает те же файлы, что и канал
(`deskd/readers.py::reader_status`, `deskd/control.py::supervisor_state`), но
своим кодом: слой не должен зависеть от кода канала, а канал — запускаться,
чтобы его спросили.

Три нуля различаются всегда (см. doctor-skills/silent-places.md, класс
«пустой=нет=битый»):

    нет файла        прибор молчит: процесс не поднимался или не писал
    файл бит         писали, но содержимое не разбирается: писал умирающий процесс
    пусто/нет полей  файл есть и валиден, но данных в нём нет

Это три РАЗНЫХ диагноза с разными словами для владельца; смешение их — первое,
от чего доктора лечили на разведке (дизайн-документ 1.4.0, задача c8a5e076).

Градиент вердиктов — принцип уровня 0: одиночный замолчавший прибор —
СИМПТОМ (наблюдение), а не приговор: агент может быть погашен владельцем,
контейнер — на паузе, машина — спать. «Болен» ставится только по
подтверждённому паттерну из нескольких измерений: петля рестартов (>= 3 за
20 минут), глухота при долге (тишина + недоставленные события), шторм
откладываний (>= 150 за 5 минут), спор за порт. Так доктор не воет волком
на каждую тихую установку — и не тихает на настоящую болезнь.

Чистые функции решений (decide_*) отдельны от IO: принимают уже собранные
факты, решают «здоров / наблюдение / болен + что сказать» — по образцу
decide_preflight/decide_after в helene/core/bootguard.py. Пороги — константы
модуля; тесты проверяют именно их.

Раскладку агентов (--agent) доктор спрашивает у общего источника правды —
localharness/agents.py (roster): правила «кто живёт в установке» уже написаны
однажды, и вторая их реализация разъехалась бы первой же кириллицей.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents  # noqa: E402  — раскладка установки (roster), не свой парсер

SCHEMA = "helene.doctor.v1"

# --------------------------------------------------------------------------- #
#  Пороги (константы — тесты проверяют именно их)
# --------------------------------------------------------------------------- #

#: Надзор (serverboot) бьётся каждые ~3 с. См. deskd/control.py BEAT_STALE = 15.
SUPERVISOR_STALE_S = 15.0
#: Исполнитель обновлений бьётся каждые ~5 с. См. deskd/control.py UPDATER_STALE = 30.
UPDATER_STALE_S = 30.0
#: Квитанция читателя пишется каждые ~10 с, свежесть — 45 с.
#: См. deskd/readers.py READER_FRESH_S = 45 и runner.py _HEARTBEAT_SEC = 10.
READER_STALE_S = 45.0
#: Рестартов в дневнике за 20 минут считается петлёй от трёх.
#: См. deskd/readers.py _health_impl (инцидент 29.08).
RESTART_WINDOW_S = 20 * 60
RESTART_LOOP_MIN = 3
#: Тишина модели без долга — «долгое молчание» (readers.py: > 90 мин).
QUIET_LONG_MIN = 90.0
#: Тишина модели ПРИ недоставленных событиях — «глухота при долге» (> 20 мин).
QUIET_DEAF_MIN = 20.0
#: Откладываний восприятия за 5 минут — шторм defer-петли (readers.py: >= 150).
SKIP_STORM_MIN = 150
SKIP_WINDOW_S = 5 * 60
#: Сколько строк хвоста журнала показывать.
TAIL_LINES = 6
#: Максимум байт, читаемых из журнала разом (см. control.py MAX_BYTES).
TAIL_BYTES = 256 * 1024

#: Журналы по карте HELENE-MAP.md. `helene.log` — корень установки (оболочка);
#: прочие — внутри дерева агента (для корневого дерево и есть data/).
#: Имя из аргументов НИКОГДА не становится путём — список закрытый (как control.LOGS).
INSTALL_LOGS: dict[str, tuple[str, str]] = {
    "shell": ("helene.log", "оболочка"),
}
TREE_LOGS: dict[str, tuple[str, str]] = {
    "runner": ("runner.log", "агент (раннер)"),
    "channel": ("deskapp.log", "канал"),
    "service": ("service.log", "служба"),
    "broker": ("broker.log", "брокер прав"),
}

VERDICT_HEALTHY = "healthy"
VERDICT_WATCH = "watch"
VERDICT_SICK = "sick"

_VERDICT_WORDS = {
    VERDICT_HEALTHY: "здоров",
    VERDICT_WATCH: "наблюдение",
    VERDICT_SICK: "болен",
}

_RANK = {VERDICT_HEALTHY: 0, VERDICT_WATCH: 1, VERDICT_SICK: 2}


# --------------------------------------------------------------------------- #
#  Чтение приборов — IO отделено от решений
# --------------------------------------------------------------------------- #

def read_probe(path: Path) -> dict:
    """Прочитать один прибор (beat/квитанция) с различением трёх нулей.

    Возвращает один из четырёх ответов:
      {"state": "missing"}                       файла нет
      {"state": "broken", "why": "..."}          файл есть, не разбирается
      {"state": "empty"}                         валиден, но полей с данными нет
      {"state": "ok", "data": {...}}             есть данные
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"state": "missing"}
    except OSError as exc:
        return {"state": "broken", "why": f"{type(exc).__name__}: {exc}"}
    if not raw.strip():
        return {"state": "broken", "why": "файл пуст (0 байт)"}
    if raw[:3] == b"\xef\xbb\xbf":  # Блокнот пишет UTF-8 BOM — терпим, как read_config
        raw = raw[3:]
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"state": "broken", "why": str(exc)[:200]}
    if not isinstance(data, dict):
        return {"state": "broken", "why": "корень не JSON-объект"}
    return {"state": "ok", "data": data}


def _num(value) -> float | None:
    """Число из недоверенных полей или None — без исключений (как readers._num)."""
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    if got != got or got in (float("inf"), float("-inf")):  # NaN/inf — не метка времени
        return None
    return got


def read_supervisor(tree: Path) -> dict:
    """Записка надзора: memory/.state/supervisor.json (НЕ .control — частая путаница)."""
    probe = read_probe(Path(tree) / "memory" / ".state" / "supervisor.json")
    if probe["state"] == "ok":
        if not any(k in probe["data"] for k in ("beat_epoch", "kind", "started_utc")):
            return {"state": "empty"}
    return probe


def read_updater(tree: Path) -> dict:
    """Бит исполнителя обновлений: memory/.control/updater.json (~5 с)."""
    probe = read_probe(Path(tree) / "memory" / ".control" / "updater.json")
    if probe["state"] == "ok":
        if not any(k in probe["data"] for k in ("beat_epoch", "beat_utc", "phase")):
            return {"state": "empty"}
    return probe


def read_reader(tree: Path) -> dict:
    """Квитанция раннера: memory/.control/desk_inbox/.reader.json (~10 с).

    «Квитанции нет вовсе» — не «агент выключен», а «этот агент окно не читает»
    (серверный случай, readers.reader_status::ever): слово ждёт читателя.
    """
    probe = read_probe(Path(tree) / "memory" / ".control" / "desk_inbox" / ".reader.json")
    if probe["state"] == "ok":
        if not any(k in probe["data"] for k in ("at", "pid")):
            return {"state": "empty"}
    return probe


def read_restarts(tree: Path, now: float, window_s: float = RESTART_WINDOW_S) -> int:
    """Рестарты в дневнике за окно. Прибор тот же, что у сторожа тишины
    (readers._health_impl п.1): три соседних дня — день дерева может
    отличаться от локального на ±1; HH:MM строки — часы её машины; файл,
    который никто не трогал час, свежий рестарт нести не может."""
    count = 0
    journal = Path(tree) / "memory" / "journal"
    today = dt.datetime.now()
    for shift in (-1, 0, 1):
        day = today + dt.timedelta(days=shift)
        path = journal / f"{day:%Y-%m-%d}.md"
        try:
            if now - path.stat().st_mtime > 3600:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines()[-300:]:
            if "[restart]" not in line and "перезапускаюсь" not in line:
                continue
            try:
                hh, mm = line.split("- ", 1)[1].split(" ", 1)[0].split(":")
                stamp = day.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            except (ValueError, IndexError):
                continue
            if 0 <= (now - stamp.timestamp()) < window_s:
                count += 1
    return count


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    """Последние limit строк JSONL; битые строки молча пропускаются — это
    прибор, а не канал: одна кривая строка не должна ронять весь диагноз."""
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    rows: list[dict] = []
    for line in blob.decode("utf-8", errors="replace").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_quiet_and_skips(tree: Path, now: float) -> dict:
    """Тихо ли у модели и нет ли шторма откладываний.

    Прибор тот же, что у сторожа тишины (readers._health_impl п.2-3): последний
    вызов модели (llm_calls.jsonl), недоставленные события (core_events*.jsonl),
    свежие perception_skips.jsonl.
    """
    tree = Path(tree)
    state = tree / "memory" / ".state"
    last_call = 0.0
    for row in _tail_jsonl(state / "llm_calls.jsonl", 5):
        got = _num(row.get("ts"))
        if got is not None and got > 0:
            last_call = max(last_call, got)
    undelivered = 0
    probe = read_probe(state / "core_events_delivered.json")
    if probe["state"] == "ok":
        body = probe["data"].get("delivered") or probe["data"]
        delivered = set(body.keys()) if isinstance(body, dict) else set()
        for row in _tail_jsonl(state / "core_events.jsonl", 400):
            key = str(row.get("dedup_key") or row.get("id") or "")
            if key and key not in delivered:
                undelivered += 1
    skips = 0
    for row in _tail_jsonl(state / "perception_skips.jsonl", 400):
        got = _num(row.get("ts"))
        if got is not None and 0 <= now - got < SKIP_WINDOW_S:
            skips += 1
    quiet_min = (now - last_call) / 60 if last_call > 0 else None
    return {"quiet_min": quiet_min, "undelivered": undelivered, "skips_5m": skips}


# --------------------------------------------------------------------------- #
#  Чистые функции решений — без IO, тестируются напрямую
# --------------------------------------------------------------------------- #

def decide_beat(probe: dict, stale_s: float, now: float, title: str) -> dict:
    """Вердикт одного бьющегося прибора (надзор, исполнитель обновлений).

    Вход — уже прочитанный read_probe, не путь. Записка без действующего бита
    (битый JSON, пустой файл, нет полей) — не «процесса нет», а «процесс есть,
    но молчит ненадёжно»: наблюдение с честным словом, не тишина. Протухший
    бит — тоже наблюдение, не приговор: прибор один, а причин молчания много
    (пауза контейнера, спящая машина, приостановленный процесс) — болезнь
    подтверждается паттернами, не одним замером.
    """
    state = probe.get("state")
    if state == "missing":
        return {"verdict": VERDICT_WATCH, "note": f"{title}: записки нет"}
    if state == "broken":
        return {"verdict": VERDICT_WATCH,
                "note": f"{title}: записка битая ({probe.get('why', '')})"}
    if state == "empty":
        return {"verdict": VERDICT_WATCH, "note": f"{title}: записка пуста"}
    beat = _num(probe["data"].get("beat_epoch"))
    if beat is None or beat <= 0:
        return {"verdict": VERDICT_WATCH, "note": f"{title}: в записке нет бита"}
    age = now - beat
    if age > stale_s:
        return {"verdict": VERDICT_WATCH,
                "note": f"{title}: молчит {age:.0f} с — упал, приостановлен или пауза"}
    if age < -stale_s:
        return {"verdict": VERDICT_WATCH,
                "note": f"{title}: бит из будущего ({age:.0f} с) — часы разошлись?"}
    return {"verdict": VERDICT_HEALTHY, "note": f"{title}: жив, бит {age:.0f} с назад"}


def decide_reader(receipt: dict, now: float) -> dict:
    """Квитанция читателя (раннер). См. readers.reader_status: возраст at против 45 с.

    «Квитанции нет» — наблюдение, а не тревога: агент может просто не читаться
    окном (серверный случай). Молчание ПОСЛЕ живой квитанции — уже болезнь.
    """
    state = receipt.get("state")
    if state == "missing":
        return {"verdict": VERDICT_WATCH,
                "note": "квитанции читателя нет — агент не поднимался или его "
                        "читает не окно (серверный случай)"}
    if state == "broken":
        return {"verdict": VERDICT_WATCH,
                "note": f"квитанция битая: {receipt.get('why', '')}"}
    if state == "empty":
        return {"verdict": VERDICT_WATCH, "note": "квитанция пуста"}
    at = _num(receipt["data"].get("at"))
    if at is None:
        return {"verdict": VERDICT_WATCH, "note": "квитанция без отметки времени"}
    age = now - at
    if age > READER_STALE_S:
        # один замер — не приговор: погашенный владельцем агент протухает так же,
        # как упавший; различить их может только журнал (хвост прикладывается)
        return {"verdict": VERDICT_WATCH,
                "note": f"раннер молчит {age:.0f} с — погашен, не поднимался или упал; "
                        "ответ в runner.log"}
    if age < -READER_STALE_S:
        return {"verdict": VERDICT_WATCH,
                "note": f"квитанция из будущего ({age:.0f} с) — часы разошлись?"}
    busy = bool(receipt["data"].get("busy"))
    run = str(receipt["data"].get("run") or "")
    note = f"раннер жив (отметка {age:.0f} с назад)"
    if busy:
        note += f", ход {run or '?'} идёт"
    return {"verdict": VERDICT_HEALTHY, "note": note}


def decide_restarts(count: int) -> dict:
    """Петля рестартов: >= 3 за 20 минут — болен (инцидент 29.08)."""
    if count >= RESTART_LOOP_MIN:
        return {"verdict": VERDICT_SICK, "note": f"петля рестартов: {count} за 20 минут"}
    if count > 0:
        return {"verdict": VERDICT_WATCH, "note": f"рестартов за 20 минут: {count}"}
    return {"verdict": VERDICT_HEALTHY, "note": "рестартов нет"}


def decide_quiet(quiet_min: float | None, undelivered: int) -> dict:
    """Тишина модели. Глухота ПРИ долге — болезнь (два прибора сошлись:
    события пришли, ответов нет). Тишина без долга — наблюдение: спокойный
    агент, к которому никто не пишет, молчит часами — и это норма."""
    if quiet_min is None:
        return {"verdict": VERDICT_WATCH,
                "note": "вызовов модели не было вовсе — или журнал вызовов не пишется"}
    if quiet_min > QUIET_DEAF_MIN and undelivered > 0:
        return {"verdict": VERDICT_SICK,
                "note": f"тишина {quiet_min:.0f} мин при {undelivered} "
                        "недоставленных событиях"}
    if quiet_min > QUIET_LONG_MIN:
        return {"verdict": VERDICT_WATCH,
                "note": f"ни одного вызова модели {quiet_min:.0f} мин — если владельцу "
                        "не отвечали, смотреть runner.log"}
    return {"verdict": VERDICT_HEALTHY,
            "note": f"последний вызов модели {quiet_min:.0f} мин назад"}


def decide_skips(skips: int) -> dict:
    """Шторм откладываний восприятия (defer-петля 20 Гц, инцидент 28.08)."""
    if skips >= SKIP_STORM_MIN:
        return {"verdict": VERDICT_SICK,
                "note": f"{skips} откладываний за 5 минут — похоже на defer-петлю"}
    if skips > 0:
        return {"verdict": VERDICT_WATCH, "note": f"откладываний за 5 минут: {skips}"}
    return {"verdict": VERDICT_HEALTHY, "note": "откладываний нет"}


def read_log_facts(folder: Path, table: dict[str, tuple[str, str]],
                    now: float) -> list[dict]:
    """Какие журналы есть и насколько свежие (карта HELENE-MAP.md).

    Свежесть журнала — подсказка «кто ещё жив», а не вердикт: молчащий журнал
    бывает и у здоровой установки (ничего не происходило). Хвост отдаётся
    отдельной функцией и только при неблагополучии.
    """
    out = []
    for key, (name, title) in table.items():
        path = Path(folder) / name
        try:
            stat = path.stat()
        except OSError:
            out.append({"id": key, "title": title, "exists": False})
            continue
        out.append({"id": key, "title": title, "exists": True,
                    "size": stat.st_size,
                    "age_s": round(now - stat.st_mtime, 1)})
    return out


def tail_log(folder: Path, key: str, lines: int = TAIL_LINES) -> str:
    """Хвост журнала из закрытого списка; обрезанная первая строка не показывается
    — она врала бы видом целой (приём из control.tail)."""
    table = INSTALL_LOGS if key in INSTALL_LOGS else TREE_LOGS
    spec = table.get(key)
    if spec is None:
        return ""
    path = Path(folder) / spec[0]
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - TAIL_BYTES))
            blob = fh.read()
    except OSError:
        return ""
    text = blob.decode("utf-8", errors="replace")
    if size > TAIL_BYTES:
        text = text.split("\n", 1)[-1]
    return "\n".join(text.splitlines()[-max(1, int(lines)):])


def worst(*verdicts: str) -> str:
    """Худший из вердиктов: sick > watch > healthy."""
    if not verdicts:
        return VERDICT_HEALTHY
    return max(verdicts, key=lambda v: _RANK.get(v, 0))


# --------------------------------------------------------------------------- #
#  Сборка диагноза
# --------------------------------------------------------------------------- #

def diagnose_agent(tree: Path, now: float | None = None) -> dict:
    """Полный диагноз одного дерева агента: приборы + решения + итог."""
    now = now if now is not None else time.time()
    checks: dict[str, dict] = {}
    checks["supervisor"] = decide_beat(read_supervisor(tree), SUPERVISOR_STALE_S, now,
                                       "надзор (serverboot)")
    checks["updater"] = decide_beat(read_updater(tree), UPDATER_STALE_S, now,
                                    "исполнитель обновлений")
    checks["reader"] = decide_reader(read_reader(tree), now)
    relay = read_probe(Path(tree) / "relay/auth-status.json")
    if relay["state"] != "missing":
        checks["relay_auth"] = decide_relay_auth(relay)
    restarts = read_restarts(tree, now)
    checks["restarts"] = decide_restarts(restarts)
    quiet_raw = read_quiet_and_skips(tree, now)
    checks["quiet"] = decide_quiet(quiet_raw["quiet_min"], quiet_raw["undelivered"])
    checks["skips"] = decide_skips(quiet_raw["skips_5m"])
    overall = worst(*(c["verdict"] for c in checks.values()))
    result = {
        "tree": str(tree),
        "verdict": overall,
        "verdict_word": _VERDICT_WORDS[overall],
        "checks": checks,
        "measures": {
            "restarts_20m": restarts,
            "skips_5m": quiet_raw["skips_5m"],
            "undelivered": quiet_raw["undelivered"],
            "quiet_min": quiet_raw["quiet_min"],
        },
        "logs": read_log_facts(tree, TREE_LOGS, now),
    }
    if overall != VERDICT_HEALTHY:
        # У здорового журнал не читаем: тишина — не повод показывать владельцу
        # случайные строки. Больному хвост раннера объясняет диагноз делом.
        tail = tail_log(tree, "runner")
        if tail:
            result["runner_tail"] = tail
    return result


def decide_relay_auth(probe: dict) -> dict:
    if probe["state"] != "ok":
        return {"verdict": VERDICT_WATCH, "note": "Прибор входа реле пуст или повреждён; вход не подтверждён"}
    data = probe["data"]
    notes = []
    watch = False
    if not data.get("login_present"):
        notes.append("В relay/local_auth нет сохранённого входа ChatGPT")
        watch = True
    else:
        notes.append("Файл входа есть в relay/local_auth")
    if data.get("relay_running") is False:
        watch = True
        notes.append("Процесс реле сейчас не поднят")
    phase = data.get("phase")
    if phase in ("queued", "saved", "failed", "conflict"):
        watch = True
    if phase:
        # Only known state labels; never trust arbitrary text from a patient file.
        notes.append({"queued": "Ждём окончания хода", "saved": "Вход сохранён, запуск реле не подтверждён",
                      "active": "Надзор сохранил вход и поднял реле", "failed": "Приём входа не завершился",
                      "conflict": "На сервере другой вход; требуется явная замена"}.get(phase, "Неизвестный результат приёма"))
    mount = data.get("persistence")
    if mount == "container-layer":
        watch = True
        notes.append("Данные находятся в слое контейнера: при пересоздании вход будет потерян. Нужен постоянный том")
    elif mount == "unknown":
        watch = True
        notes.append("Постоянное хранение не подтверждено")
    elif isinstance(mount, str) and mount.startswith("mounted:"):
        notes.append("Папка данных находится на смонтированной файловой системе; сохранность тома при обновлении зависит от конфигурации Docker")
    return {"verdict": VERDICT_WATCH if watch else VERDICT_HEALTHY, "note": "; ".join(notes)}


def diagnose_install(base: Path, agent_id: str | None = None) -> dict:
    """Диагноз установки: ростер + дерево каждого агента (или одного по --agent)."""
    base = Path(base)
    roster = agents.roster(base)
    if agent_id:
        wanted = (agent_id or "").strip().lower()
        roster = [a for a in roster if a.id == wanted]
        if not roster:
            return {"schema": SCHEMA, "base": str(base),
                    "error": f"агента с id «{wanted}» в этой установке нет", "agents": []}
    out = []
    for agent in roster:
        entry: dict = {"id": agent.id, "name": agent.name, "enabled": agent.enabled,
                       "port": agent.port, "conflict": agent.conflict,
                       "tree": str(agent.tree)}
        if agent.conflict:
            entry.update({"verdict": VERDICT_SICK,
                          "verdict_word": _VERDICT_WORDS[VERDICT_SICK],
                          "checks": {}, "measures": {},
                          "note": f"порт {agent.port} занят агентом {agent.conflict}"})
        else:
            diag = diagnose_agent(agent.tree)
            entry.update({k: diag[k] for k in ("verdict", "verdict_word",
                                               "checks", "measures", "logs")})
            if "runner_tail" in diag:
                entry["runner_tail"] = diag["runner_tail"]
        out.append(entry)
    report = {"schema": SCHEMA, "base": str(base),
              "install_logs": read_log_facts(base, INSTALL_LOGS, time.time()),
              "agents": out}
    return report


def explain_report(report: dict) -> str:
    """Тот же отчёт — словами владельца, по-русски."""
    lines: list[str] = []
    verdicts: list[str] = []
    for agent in report.get("agents", []):
        head = f"{agent['name']} ({agent['id']})"
        if not agent.get("enabled", True):
            head += " — погашен"
        if agent.get("conflict"):
            head += " — спор за порт"
        lines.append(f"## {head} — {agent.get('verdict_word', '?')}")
        if agent.get("note"):
            lines.append(f"  {agent['note']}")
        for name, check in (agent.get("checks") or {}).items():
            lines.append(f"  [{_VERDICT_WORDS[check['verdict']]}] {name}: {check['note']}")
        logs = agent.get("logs") or []
        fresh = [f"{row['title']} ({row['age_s']:.0f} с назад)" for row in logs
                 if row.get("exists") and row.get("age_s") is not None]
        if fresh:
            lines.append("  журналы живы: " + ", ".join(fresh))
        if agent.get("runner_tail"):
            lines.append("  последние строки runner.log:")
            for row in agent["runner_tail"].splitlines():
                lines.append(f"    | {row}")
        verdicts.append(agent.get("verdict", VERDICT_HEALTHY))
        lines.append("")
    if report.get("error"):
        lines.append(f"!! {report['error']}")
    lines.append(f"Итог установки: {_VERDICT_WORDS[worst(*verdicts)]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="Доктор Hélène: диагноз установки по готовым приборам (уровень 0).")
    parser.add_argument("command", choices=("status", "explain"),
                        help="status — машиночитаемый JSON; explain — словами владельца")
    parser.add_argument("--base", default=".",
                        help="корень установки (где helene.json; умолчание — текущая папка)")
    parser.add_argument("--agent", default=None,
                        help="диагноз только этого агента (id из карточки «Агенты»)")
    args = parser.parse_args(argv)

    report = diagnose_install(Path(args.base), args.agent)

    if report.get("error"):
        # неизвестный агент — ошибка и в status, и в explain; код 2, как у CLI агентов
        body = (json.dumps(report, ensure_ascii=False) if args.command == "status"
                else report["error"])
        print(body, file=sys.stderr)
        return 2

    if args.command == "status":
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(explain_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
