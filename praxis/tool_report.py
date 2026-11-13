# -*- coding: utf-8 -*-
"""Отчёт руки о её исходе: для журнала и экрана, а не для текста модели (09.11.2026).

Рука возвращает модели строку, и отказ в журнале выглядел как успех: в 1715 ответах не
нашлось ни одного поля исхода. Здесь рука называет исход отдельно. Модель получает тот же
текст, что и раньше.

Слот один на вызов. Он лежит в ContextVar как изменяемый словарь, поэтому его видит и рабочий
поток потолка руки, куда контекст копируется. Вне вызова все функции молчат: рука работает
одинаково и из тестов, и из служебного кода.

Исход `unknown` значит «рука не сказала»: успех из молчания здесь не выводится.
"""
from __future__ import annotations

import os
import sys
from contextvars import ContextVar
from typing import Callable

OUTCOMES = frozenset({"confirmed", "refused", "in_doubt", "partial", "ok", "failed", "unknown"})
MAX_EXECUTIONS = 8
MAX_OPERATIONS = 40
_MAX_TEXT = 300

_SLOT: ContextVar[dict | None] = ContextVar("tool_report_slot", default=None)
_sink: Callable[..., None] | None = None
_audit_installed = False


def new() -> dict:
    return {"outcome": "", "target": "", "operations": [], "operations_cut": 0, "executions": []}


def attach(slot: dict):
    return _SLOT.set(slot)


def detach(token) -> None:
    _SLOT.reset(token)


def set_sink(sink: Callable[..., None] | None) -> None:
    """Куда уходит запись об исполнении, пока вызов идёт (агент: журнал хода)."""
    global _sink
    _sink = sink


def note(outcome: str = "", target: str = "") -> None:
    slot = _SLOT.get()
    if slot is None:
        return
    if outcome in OUTCOMES:
        slot["outcome"] = outcome
    if target:
        slot["target"] = str(target)[:_MAX_TEXT]


def current() -> str:
    """The outcome already noted in this call; '' outside a call."""
    slot = _SLOT.get()
    return str(slot.get("outcome") or "") if slot is not None else ""


def execution(receipt: dict) -> None:
    """Что реально запущено: argv или команда, папка, код выхода."""
    slot = _SLOT.get()
    if slot is None:
        return
    if len(slot["executions"]) < MAX_EXECUTIONS:
        slot["executions"].append(dict(receipt))
    if _sink is not None:
        try:
            _sink("tool_execution", receipt=dict(receipt))
        except Exception:
            pass


def summary(slot: dict, *, error: BaseException | None = None, expired: bool = False) -> dict:
    """Поля для `tool_result.metadata`. Падение руки — `failed`, потолок — `in_doubt`."""
    outcome = slot.get("outcome") or "unknown"
    if error is not None:
        outcome = "failed"
    elif expired:
        outcome = "in_doubt"
    out = {"outcome": outcome}
    if slot.get("target"):
        out["target"] = slot["target"]
    if slot.get("operations"):
        out["operations"] = list(slot["operations"])
    if slot.get("operations_cut"):
        out["operations_cut"] = slot["operations_cut"]
    return out


# Что считать действием руки. Содержимое файлов и окружение процессов сюда не попадают.
_SKIP_PREFIXES = (sys.prefix, sys.base_prefix, "/proc/", "/sys/", "/dev/", "/usr/lib/", "/usr/local/lib/", "/etc/ssl/")
_SKIP_SUFFIXES = (".py", ".pyc", ".so", ".pyi")
# Свой журнал хода и служебное состояние — не действие руки, а её собственный учёт.
_SKIP_PARTS = ("__pycache__", "/.git/", ".lock", "/memory/runs/", "/memory/.state/", "/memory/.forge/")


def _uninteresting(path: str) -> bool:
    return (path.startswith(_SKIP_PREFIXES) or path.endswith(_SKIP_SUFFIXES)
            or any(part in path for part in _SKIP_PARTS))


def _record(slot: dict, row: list) -> None:
    if len(slot["operations"]) >= MAX_OPERATIONS:
        slot["operations_cut"] += 1
        return
    if slot["operations"] and slot["operations"][-1] == row:
        return
    slot["operations"].append(row)


def _text(value) -> str:
    try:
        return os.fsdecode(value)[:_MAX_TEXT]
    except Exception:
        return str(value)[:_MAX_TEXT]


def _audit(event: str, args: tuple) -> None:
    try:
        slot = _SLOT.get()
        if slot is None:
            return
        if event == "open":
            path, mode, flags = args
            if not isinstance(path, (str, bytes, os.PathLike)):
                return
            name = _text(path)
            if _uninteresting(name):
                return
            writes = bool(isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            _record(slot, ["write" if writes else "read", name])
        elif event == "os.rename":
            # Атомарная запись манифеста хода (tmp → manifest.json) шла сюда без фильтра,
            # и экран показывал её как действие руки. Цель переименования решает, чьё оно.
            if not _uninteresting(_text(args[1])):
                _record(slot, ["replace", _text(args[0]), _text(args[1])])
        elif event in ("os.remove", "os.rmdir", "os.mkdir"):
            name = _text(args[0])
            if not _uninteresting(name):
                _record(slot, [event[3:], name])
        elif event == "subprocess.Popen":
            executable, argv = args[0], args[1]
            _record(slot, ["process", _text(executable), [_text(a) for a in list(argv)[:12]]])
        elif event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple) and len(address) >= 2:
                _record(slot, ["connect", f"{address[0]}:{address[1]}"])
    except Exception:
        return


def install_audit() -> bool:
    """Включить наблюдение. Хук в процессе нельзя снять, поэтому он пуст вне вызова руки."""
    global _audit_installed
    if _audit_installed or str(os.getenv("PRAXIS_TOOL_AUDIT", "on")).strip().lower() in {"0", "off", "false", "no"}:
        return _audit_installed
    sys.addaudithook(_audit)
    _audit_installed = True
    return True
