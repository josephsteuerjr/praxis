"""Реестр её фоновых процессов из shell: кто запустил, когда, жив ли (25.09, поток G §4.2).

Живой случай 24.09: два окна `task_window` вели один счёт и убивали процессы друг друга
как «чужой клон» — `nohup … &` из шелла шёл мимо всякого учёта, и ни одно окно не знало,
что процесс запустило другое ЕЁ окно. Здесь запуск в фон из руки `shell` записывается:
pid, команда, run запустившего; строка «твои фоновые процессы» едет в изменчивый хвост
кадра каждого ввода модели. Процесс без записи о рождении здесь называется «не в моём
реестре», а не «чужой».

Только учёт, никаких действий над процессами. Файл — `memory/.state/processes.json`,
запись атомарная; мёртвые pid вычищаются при чтении.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent.parent)
STATE_FILE = BASE / "memory" / ".state" / "processes.json"

_LOCK = threading.Lock()
CAP = 100
MARKER = "[praxis-bg-pid]"
_MARKER_RE = re.compile(r"^\[praxis-bg-pid\]\s+(\d+)\s*$", re.M)


def wrap_background_launch(command: str) -> tuple[str, bool]:
    """Команда с запуском в фон (последняя команда заканчивается на `&`) получает хвост,
    печатающий pid последнего фонового задания. Остальные — без изменений.

    Честно о границе: учитывается ТОЛЬКО хвостовой `&`. `setsid x` без `&`, `nohup x &  # note`
    и конвейер `a | tee log &` (pid у tee) остаются мимо реестра — она видит их через ps."""
    text = str(command or "")
    stripped = text.rstrip()
    if not stripped.endswith("&") or stripped.endswith("&&"):
        return text, False
    tail = ('\n__praxis_bg=$!; [ -n "$__praxis_bg" ] && echo "%s $__praxis_bg"' % MARKER)
    return stripped + tail, True


def take_pid_marker(output: str) -> tuple[str, int | None]:
    """Снять строку-маркер из вывода shell → (вывод без маркера, pid или None)."""
    text = str(output or "")
    match = _MARKER_RE.search(text)
    if not match:
        return text, None
    pid = int(match.group(1))
    clean = _MARKER_RE.sub("", text).rstrip("\n")
    return clean + ("\n" if text.endswith("\n") and clean else ""), pid


_WRAPPERS = {"nohup", "setsid", "exec", "sudo", "env", "time", "stdbuf", "unbuffer"}
_INTERPRETERS = {"python", "python3", "python3.12", "python3.13", "python3.14", "node",
                 "bash", "sh", "zsh", "perl", "ruby", "uv", "pypy3"}


def display_name(command: str) -> str:
    """Короткое имя процесса для строки кадра: сама программа, не обёртка вокруг неё.

    `nohup python -u mr_ifub2.py > log 2>&1 &` → `mr_ifub2.py`; `python -m http.server`
    → `http.server`; `./run.sh` → `run.sh`. Обёртки (nohup/setsid/env/VAR=x) и флаги
    интерпретатора пропускаются; интерпретатор без скрипта называется сам.
    """
    text = " ".join(str(command or "").split())
    tokens = text.replace("&&", " ").replace(";", " ").split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        low = token.lower()
        if low in _WRAPPERS or ("=" in token and not token.startswith("-")):
            i += 1
            continue
        if low == "cd" and i + 1 < len(tokens):
            i += 2            # `cd <dir> && …` — обёртка, не программа (A10 F10)
            continue
        base = os.path.basename(token)
        if base.lower() in _INTERPRETERS:
            j = i + 1
            while j < len(tokens):
                nxt = tokens[j]
                if nxt in {"&", "&&", ";", "|", ">", "2>&1", "<"} or nxt.startswith(">"):
                    break
                if nxt == "-m" and j + 1 < len(tokens):
                    return tokens[j + 1][:60]
                if nxt.startswith("-"):
                    j += 1
                    continue
                return os.path.basename(nxt)[:60] or base[:60]
            return base[:60]
        return base[:60] or text[:60]
    return text[:60]


def _load() -> list[dict]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [row for row in (items or []) if isinstance(row, dict)]


def _save(items: list[dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema": "praxis.processes.v1", "items": items},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def register(pid: int, command: str, *, run_id: str = "", run_kind: str = "",
             ts: float | None = None) -> dict:
    row = {"pid": int(pid), "name": display_name(command),
           "command": " ".join(str(command or "").split())[:300],
           "run_id": str(run_id or ""), "run_kind": str(run_kind or ""),
           "ts": float(ts or time.time())}
    with _LOCK:
        items = [r for r in _load() if int(r.get("pid") or 0) != row["pid"]]
        items.append(row)
        _save(items[-CAP:])
    return row


def live(*, alive=_alive) -> list[dict]:
    """Живые записи; мёртвые pid вычищаются из файла."""
    with _LOCK:
        items = _load()
        keep = [r for r in items if alive(int(r.get("pid") or 0))]
        if len(keep) != len(items):
            _save(keep)
    return keep


def state_line(*, alive=_alive) -> str:
    rows = live(alive=alive)
    if not rows:
        return ""
    parts = []
    for row in rows[-8:]:
        when = time.strftime("%H:%M", time.localtime(float(row.get("ts") or 0)))
        launcher = str(row.get("run_id") or "")
        by = (f"запустило {row.get('run_kind') or 'run'} {launcher[-12:]}" if launcher
              else "запущен вне run")
        parts.append(f"{row.get('name')} (pid {row.get('pid')}, {by}, {when})")
    more = len(rows) - len(rows[-8:])
    return ("твои фоновые процессы из shell: " + "; ".join(parts)
            + (f"; …и ещё {more}" if more else "")
            + ". Процесс без записи здесь — «не в моём реестре», а не «чужой клон».")
