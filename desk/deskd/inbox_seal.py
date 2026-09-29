# -*- coding: utf-8 -*-
"""Журнал записок владельца: записку в приёмную агента кладёт только канал (1.2.5).

Отчёт Йоны (агент Дмитрия, сервер) 29.09, дословно по сути: «агент может исполнять команды
с правами root в том же контейнере, где находятся код, секреты и канал входящих сообщений
владельца. Поэтому метка owner в записке inbox не служит границей против такого агента».
Так и было: раннер брал любой файл из `memory/.control/desk_inbox` как слово владельца
(`owner=True`), а файл мог положить и shell самого агента.

Теперь на сервере (надзор ставит `HELENE_INBOX_SEALED=1`) канал, прежде чем опубликовать
записку, заносит её в журнал — `inbox-ledger.jsonl` в СВОЕЙ папке (`HELENE_DESK_STATE`,
`data/.channel`), куда агенту хода нет (другой пользователь, 0700). Раннер, прежде чем
вести ход по записке, спрашивает канал по петле: «эта записка твоя?» — имя и sha256 её
байтов. Нет в журнале, не сходится отпечаток или она уже обработана — не ход владельца.
После хода раннер говорит «обработана» (`done`), и повтор той же записки отвергается.

Раннеру ключ не нужен: вопрос «твоя ли записка» владельческих прав не даёт, ответ — да/нет.
Спросить может и сам агент — толку ему нет: записать в журнал может только канал.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

NAME = "inbox-ledger.jsonl"
#: Столько строк журнала держим; дальше он переписывается: необработанные — все, прочие — хвост.
KEEP = 4000
#: Записка старше — «просрочена»: ход по ней уже не слово владельца «сейчас».
MAX_AGE = 14 * 24 * 3600


def sealed() -> bool:
    return os.environ.get("HELENE_INBOX_SEALED") == "1"


def ledger_path() -> Path | None:
    home = (os.environ.get("HELENE_DESK_STATE") or "").strip()
    return Path(home) / NAME if home and sealed() else None


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


class Ledger:
    """Журнал записок канала: `seal` при публикации, `claim` — вопрос раннера."""

    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        self._lines = 0
        self._load()

    def _load(self) -> None:
        try:
            text = self.path.read_text("utf-8")
        except OSError:
            return
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not row.get("name"):
                continue
            self._lines += 1
            name = str(row["name"])
            if row.get("kind") == "done":
                if name in self._rows:
                    self._rows[name]["done"] = True
            else:
                self._rows[name] = {"sha": str(row.get("sha") or ""), "at": float(row.get("at") or 0),
                                    "via": str(row.get("via") or ""), "done": False}

    def _append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._lines += 1
        if self._lines > KEEP * 2:
            self._compact()

    def _compact(self) -> None:
        now = self.clock()
        keep = sorted(self._rows.items(), key=lambda kv: kv[1]["at"])
        keep = [kv for kv in keep if not kv[1]["done"] or now - kv[1]["at"] < MAX_AGE][-KEEP:]
        self._rows = dict(keep)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for name, row in keep:
                fh.write(json.dumps({"kind": "note", "name": name, "sha": row["sha"], "at": row["at"],
                                     "via": row["via"]}, ensure_ascii=False) + "\n")
                if row["done"]:
                    fh.write(json.dumps({"kind": "done", "name": name, "at": row["at"]}) + "\n")
        os.replace(tmp, self.path)
        self._lines = sum(2 if r["done"] else 1 for _, r in keep)

    def seal(self, name: str, blob: bytes, via: str) -> None:
        """Канал публикует записку `name` с этими байтами — занести ДО публикации."""
        with self._lock:
            row = {"kind": "note", "name": str(name), "sha": digest(blob), "at": self.clock(),
                   "via": str(via or "")}
            self._append(row)
            self._rows[str(name)] = {"sha": row["sha"], "at": row["at"], "via": row["via"], "done": False}

    def claim(self, name: str, sha: str, done: bool = False) -> dict:
        """Вопрос раннера: эта записка — канала? -> {ok, why, via}. `done` — ход по ней прошёл."""
        with self._lock:
            row = self._rows.get(str(name or ""))
            if row is None:
                return {"ok": False, "why": "записки нет в журнале канала — её положил не канал"}
            if str(sha or "") != row["sha"]:
                return {"ok": False, "why": "записка изменена после того, как её положил канал"}
            if row["done"]:
                return {"ok": False, "why": "записка уже обработана — повтор не принимается"}
            if self.clock() - row["at"] > MAX_AGE:
                return {"ok": False, "why": "записка просрочена"}
            if done:
                self._append({"kind": "done", "name": str(name), "at": self.clock()})
                row["done"] = True
            return {"ok": True, "why": "", "via": row["via"]}
