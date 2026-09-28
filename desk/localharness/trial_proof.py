# -*- coding: utf-8 -*-
"""Доказательство делом на испытании обновления (1.2.5, слово Егора 28.09).

После обновления агент проверяет себя в новой версии и говорит `update_request accept`
или `reject`. До 1.2.5 «принимаю» было только словом: модель могла ответить accept, ни
разу не позвав ни одной руки, — и новая версия закрывалась «проверенной». Егор:
«ужесточить приёмку»; договорились — принимать только с доказательством делом.

Доказательство — то, что агент СДЕЛАЛ в новой версии с начала испытания:
  * руки работают: хоть одна рабочая рука (не речь и не сама `update_request`) вернула
    результат, а не отказ;
  * память находится: `recall` хоть раз что-то нашёл.
Думает ли он — видно по самому ходу. Расширения репетирует установщик/исполнитель, правки
кода переносит он же; судить, работают ли они, — дело агента, и это он пишет в report.

Как узнаю: обёртка над единственной воронкой исполнения рук дерева
(`agent._call_tool_with_ceiling`) — пока в расписке открыто испытание, каждый вызов руки
ложится в `memory/.state/update-trial-proof.json` (ключ испытания, имя, итог коротко).
Вне испытания обёртка только читает расписку (кэш по mtime) и ничего не пишет.

Отказ — не слово агента, а проверка руки: `accept` без доказательства отвечает, чего не
хватает. `reject` доказательства не требует: откат безопасен. Владелец кнопкой в окне
принимает без доказательства — его слово поверх.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("helene.trial")

PROOF_FILE = ("memory", ".state", "update-trial-proof.json")
RECEIPT_FILE = ("memory", ".control", "update-plan.receipt.json")

#: Не рабочие руки: речь, покой, сама рука обновления и память (память — отдельной строкой).
NOT_WORK = frozenset({"reply", "say", "end_turn", "stay_silent", "narrate", "react", "focus",
                      "rest", "update_request", "recall", "remember", "journal"})
#: Так начинаются отказы и сбои рук — это не дело, а его отсутствие.
FAILED_HEADS = ("[рука ", "[tool_error", "[предел", "[рука не вернулась", "ошибка", "error",
                "traceback", "update_request:", "нет доступа", "отказано")
#: `recall` без находки.
RECALL_EMPTY = ("Ничего не вспомнилось", "Пустой запрос")
KEEP = 20


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Proof:
    """Журнал дел одного дома агента на время испытания."""

    def __init__(self, tree: Path, clock: Callable[[], float] = time.time):
        self.tree = Path(tree)
        self.clock = clock
        self._receipt_mtime = -1.0
        self._key = ""

    @property
    def path(self) -> Path:
        return self.tree.joinpath(*PROOF_FILE)

    def trial_key(self) -> str:
        """Ключ открытого испытания или "" (расписку перечитываю, только если она менялась)."""
        path = self.tree.joinpath(*RECEIPT_FILE)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._receipt_mtime, self._key = -1.0, ""
            return ""
        if mtime != self._receipt_mtime:
            receipt = _read_json(path)
            trial = receipt.get("trial") if isinstance(receipt.get("trial"), dict) else {}
            open_ = (receipt.get("state") == "trial"
                     and str(receipt.get("phase") or "trial") == "trial")
            self._key = str(trial.get("key") or "") if open_ else ""
            self._receipt_mtime = mtime
        return self._key

    def record(self, name: str, call_input: dict, out) -> None:
        key = self.trial_key()
        if not key or name in ("update_request",):
            return
        text = "" if out is None else str(out)
        failed = isinstance(out, BaseException) or not text.strip() or \
            text.strip().lower().startswith(FAILED_HEADS)
        proof = _read_json(self.path)
        if proof.get("key") != key:
            proof = {"key": key, "hands": [], "recall": []}
        row = {"at": round(self.clock(), 1), "name": str(name)[:60],
               "said": " ".join(text.split())[:160]}
        if name == "recall":
            query = str((call_input or {}).get("query") or "")[:120]
            hit = not failed and not text.strip().startswith(RECALL_EMPTY)
            proof["recall"] = (proof.get("recall") or [])[-(KEEP - 1):] + [{**row, "query": query, "hit": hit}]
        elif name not in NOT_WORK:
            proof["hands"] = (proof.get("hands") or [])[-(KEEP - 1):] + [{**row, "ok": not failed}]
        else:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(proof, ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.debug("журнал испытания не записался", exc_info=True)

    def evidence(self, key: str) -> dict:
        """Что доказано делом за испытание `key`: {ok, hands, recall, missing, line}."""
        proof = _read_json(self.path)
        if not key or proof.get("key") != key:
            proof = {}
        hands = [r for r in proof.get("hands") or [] if isinstance(r, dict) and r.get("ok")]
        recall = [r for r in proof.get("recall") or [] if isinstance(r, dict) and r.get("hit")]
        missing = []
        if not hands:
            missing.append("ни одна рабочая рука ещё не вернула результат (позови shell, "
                           "fs_read или то, чем пользуешься чаще)")
        if not recall:
            missing.append("recall ещё ни разу ничего не нашёл (вспомни последний разговор "
                           "с владельцем или свою запись о себе)")
        names = sorted({str(r.get("name")) for r in hands})
        line = ("делом: руки " + (", ".join(names) or "—") + "; recall "
                + (f"нашёл по «{recall[-1].get('query')}»" if recall else "—"))
        return {"ok": not missing, "hands": names, "recall": len(recall), "missing": missing,
                "line": line}


_PROOFS: dict[str, Proof] = {}


def proof_for(tree: Path) -> Proof:
    key = str(Path(tree).resolve())
    if key not in _PROOFS:
        _PROOFS[key] = Proof(Path(tree))
    return _PROOFS[key]


def install(agent_mod, tree: Path) -> bool:
    """Обернуть воронку рук дерева: на испытании каждый вызов ложится в журнал дел."""
    original = getattr(agent_mod, "_call_tool_with_ceiling", None)
    if not callable(original):
        log.warning("испытание: у дерева нет _call_tool_with_ceiling — доказательства делом не будет")
        return False
    if getattr(original, "_trial_proof", False):
        return True
    proof = proof_for(tree)

    def call_tool(name, impl, call_input):
        out = original(name, impl, call_input)
        try:
            proof.record(str(name), call_input if isinstance(call_input, dict) else {}, out)
        except Exception:                       # журнал никогда не роняет руку
            log.debug("испытание: запись дела упала", exc_info=True)
        return out

    call_tool._trial_proof = True
    call_tool.__wrapped__ = original
    agent_mod._call_tool_with_ceiling = call_tool
    return True
