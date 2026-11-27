"""Per-chat буферы «Имя: строка» и их мета — restart-proof бэкстоп раннера.

Переписан с нуля (задача code-473f9dd3, этап 2); поведение заморожено
характеристическими тестами test_bufstore.py и дифференциальным прогоном
против прежней версии (checkpoint 88098e7c — граница «до»).

Слой данных. Два атомарных хранилища JSON без схемы БД:

    memory/.buffers/<chat>.json   — список строк буфера (gitignored);
    memory/.state/buf_meta.json   — {chat: {last_ts, last_author, is_dm, name}}.

Инварианты, поднятые из живых инцидентов (номера PASS — прецеденты):

  * идентичная запись не является изменением (25.08): иначе бут-синк,
    помечающий dirty все ключи разом, переписывал сотни байт-в-байт
    одинаковых зеркал на каждом рестарте, и mtime лгал о живости ключа;
  * мета пишется синхронно с приёмом, буфер — дебаунсом (23.09): поэтому
    именно мета, а не файл, знает, кто сказал последним; рассинхрон
    «файл не видел ответа» не должен воскрешать отвеченное ЛС;
  * last_ts монотонен: поздно прибывшее подтверждение старого исполнения
    не отменяет уже принятого более свежего автора.

Публичный API прежней версии сохранён: BASE/BUF_DIR/STATE_DIR/META_PATH
(читаются в момент вызова — тестируемы подстановкой), _safe, path_for,
save, load, load_all, meta_load, meta_update, missed_dm_candidates.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from self_model import FileLock

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
BUF_DIR = BASE / "memory" / ".buffers"
STATE_DIR = BASE / "memory" / ".state"
META_PATH = STATE_DIR / "buf_meta.json"

_SELF_MARKER = "praxis"          # casefold-имя, которым помечаем свои строки
_SELF_PREFIX = "Praxis:"         # префикс строки буфера, пишущийся тем же кодом

_META_LOCK = threading.RLock()


# --------------------------------------------------------------------------- #
#  Примитивы
# --------------------------------------------------------------------------- #

def _safe(chat_id) -> str:
    r"""Ключ файла: [\w-]; пустой результат — 'chat'."""
    return re.sub(r"[^\w-]", "_", str(chat_id)) or "chat"


def path_for(chat_id) -> Path:
    return BUF_DIR / f"{_safe(chat_id)}.json"


def _atomic_write_json(path: Path, payload: str) -> None:
    """tmp + replace: читатель видит либо старый, либо новый цельный файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    """JSON или None: битый/отсутствующий файл — не событие, а пустота."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  Буферы строк
# --------------------------------------------------------------------------- #

def save(chat_id, lines) -> None:
    """Записать буфер чата; байт-в-байт тот же payload файл не трогает.

    Граница честного mtime (25.08): ключ «менялся» значит менялось
    содержимое, а не то, что кто-то посмотрел и переписал то же самое.
    """
    payload = json.dumps([str(l) for l in lines], ensure_ascii=False)
    p = path_for(chat_id)
    if p.exists() and _read_json(p) is not None:
        try:
            if p.read_text(encoding="utf-8") == payload:
                return
        except OSError:
            pass
    _atomic_write_json(p, payload)


def load(chat_id) -> list[str]:
    data = _read_json(path_for(chat_id))
    return [str(x) for x in data] if isinstance(data, list) else []


def load_all() -> dict[str, list[str]]:
    """{ключ-файла: [строки]}; битые и не-списки молча пропускаются."""
    out: dict[str, list[str]] = {}
    if not BUF_DIR.exists():
        return out
    for f in BUF_DIR.glob("*.json"):
        data = _read_json(f)
        if isinstance(data, list):
            out[f.stem] = [str(x) for x in data]
    return out


# --------------------------------------------------------------------------- #
#  Мета: кто сказал последним и когда
# --------------------------------------------------------------------------- #

@dataclass
class _MetaEntry:
    last_ts: float = 0.0
    last_author: str = ""
    is_dm: bool = False
    dm_known: bool = False   # писала ли мета is_dm явно (иначе — фолбэк знака)
    name: str = ""

    @classmethod
    def from_any(cls, raw) -> "_MetaEntry":
        m = raw if isinstance(raw, dict) else {}
        try:
            ts = float(m.get("last_ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return cls(last_ts=ts,
                   last_author=str(m.get("last_author") or ""),
                   is_dm=bool(m.get("is_dm", False)),
                   dm_known="is_dm" in m,
                   name=str(m.get("name") or ""))

    def merge(self, author: str, is_dm: bool | None, name: str | None,
              observed_ts: float) -> "_MetaEntry | None":
        """Новая запись поверх текущей; None — наблюдение устарело.

        Явные поля берутся из наблюдения, пропущенные наследуются; is_dm
        без указания при первом появлении выводится из знака ключа
        (группы Telegram отрицательны) — сигнал вызывающей стороны.
        """
        if self.last_ts > observed_ts:
            return None  # старое исполнение не отменяет принятого автора
        self.last_ts = observed_ts
        # author="" — тоже сигнал: явно пустой автор СТИРАЕТ прежнего
        # (semantics старой версии, зона PASS 23.09: «кто сказал последним»
        # определяется последним наблюдением, а не самым говорливым).
        self.last_author = str(author or "")
        if is_dm is not None:
            self.is_dm = bool(is_dm)
            self.dm_known = True
        if name is not None:
            self.name = str(name)
        return self

    def as_dict(self) -> dict:
        return {"last_ts": self.last_ts,
                "last_author": self.last_author,
                "is_dm": self.is_dm,
                "name": self.name}


def meta_load() -> dict:
    data = _read_json(META_PATH)
    return data if isinstance(data, dict) else {}


def meta_update(chat_id, author: str = "", is_dm: bool | None = None,
                name: str | None = None, ts: float | None = None) -> None:
    """Слить одно наблюдение с мета-файлом (файл-лок + поток-лок)."""
    observed_ts = float(ts if ts is not None else time.time())
    key = _safe(chat_id)
    with _META_LOCK, FileLock(META_PATH.with_suffix(".lock"), timeout=30.0,
                              stale_after=300.0, heartbeat=5.0):
        meta = meta_load()
        entry = _MetaEntry.from_any(meta.get(key))
        if is_dm is None and not entry.dm_known:
            entry.is_dm = not key.startswith("-")  # первый взгляд: знак id
            entry.dm_known = True
        merged = entry.merge(str(author or ""), is_dm, name, observed_ts)
        if merged is None:
            return
        meta[key] = merged.as_dict()
        _atomic_write_json(META_PATH, json.dumps(meta, ensure_ascii=False))


# --------------------------------------------------------------------------- #
#  Boot-sweep: кого разбудил рестарт
# --------------------------------------------------------------------------- #

@dataclass
class _Candidate:
    chat_id: str
    name: str
    age_hours: float

    def as_dict(self) -> dict:
        return {"chat_id": self.chat_id, "name": self.name,
                "age_hours": self.age_hours}


def _looks_answered(lines: list[str], meta_author: str) -> bool:
    """ЛС не кандидат, если последняя строка/автор — сама Praxis.

    Два независимых источника: файл буфера (дебаунс — может отставать) и
    мета (синхронная). Достаточно любого: лучше пропустить будильник,
    чем ответить второй раз на уже отвеченное (23.09).
    """
    if not lines or lines[-1].startswith(_SELF_PREFIX):
        return True
    return meta_author.strip().casefold() == _SELF_MARKER


def missed_dm_candidates(max_age_hours: float = 48.0,
                         now: float | None = None) -> list[dict]:
    """DM, чья последняя строка не наша и свежая: [{"chat_id","name","age_hours"}].

    Только личные чаты: группа не ждёт ответа лично от Praxis. Свежесть —
    по мете (фолбэк mtime файла); окно по умолчанию 48 ч — позже будить
    человека попросту поздно. Без мутаций диска.
    """
    import step_control  # поздний импорт: тесты и живой код мокают точечно

    now = time.time() if now is None else now
    meta = {k: _MetaEntry.from_any(v) for k, v in meta_load().items()}
    out: list[_Candidate] = []
    if not BUF_DIR.exists():
        return out
    for f in BUF_DIR.glob("*.json"):
        cid = f.stem
        m = meta.get(cid, _MetaEntry())
        is_dm = m.is_dm if m.dm_known else not cid.startswith("-")
        if not is_dm:
            continue
        lines = load(cid)
        if _looks_answered(lines, m.last_author):
            continue
        try:
            last_ts = float(m.last_ts) if m.last_ts else f.stat().st_mtime
        except (TypeError, ValueError, OSError):
            continue
        age_h = max(0.0, (now - last_ts) / 3600.0)
        if age_h >= max_age_hours:
            continue  # поезд ушёл — не дёргаем спустя двое суток
        if step_control.suppresses_replay(cid, last_ts, base=BASE):
            continue
        out.append(_Candidate(chat_id=cid, name=m.name, age_hours=age_h))
    return [c.as_dict() for c in out]
