# -*- coding: utf-8 -*-
"""Окно Frame как транспорт агента: тот же шов, на котором висит Telethon.

`agent._TELETHON` — обычный словарь вызываемых, который живой раннер наполняет на
старте (mtproto_runner: reply, send_message, read_chat, get_id, transport_state…).
Ни одна её рука не знает, кто там лежит: она зовёт «отправить» и получает расписку.
Значит десктопный продукт не нуждается ни в одной правке её кода — он просто кладёт
в этот словарь свои функции. Это тот же приём, что труба `praxis.desk.v1` для
оболочки: один шов, за которым может стоять что угодно.

Что здесь есть по-настоящему:
  * доставка её реплики в окно (архив комнаты + событие жизни + расписка ей);
  * чтение своей же комнаты (`read_context`, `read_chat`, поиск по переписке);
  * честный отказ на адресатов, которых в этом продукте нет.

Чего здесь НЕТ и почему: крючки Telegram-специфики (вступить в чат, реакции, аватар,
модерация, telegram_account) НЕ регистрируются. Её тулы на отсутствующем крючке
отвечают «Telegram-мост сейчас недоступен» — это правда об этом продукте, и она
лучше, чем заглушка, которая делает вид, что действие состоялось.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("frame.transport")

# Реестр комнат окна — чистый модуль канала (deskd/rooms.py, без зависимостей):
# один и тот же для канала и харнесса, чтобы имена и ключи не разъезжались.
import sys as _sys
_APP_DIR = str(Path(__file__).resolve().parent.parent)
if _APP_DIR not in _sys.path:
    _sys.path.append(_APP_DIR)
from deskd import rooms  # noqa: E402

# Комнаты окна (задача A §3; те же значения в ui-kit/contract.json, сверка —
# tests/t_contract.py). Ключ комнаты = имя потока = имя файла архива
# `memory/groups/<ключ>.jsonl`: без двоеточий и пробелов. Комната по умолчанию
# `window` зовётся именем агента и не удаляется; новые — `window-<8 hex>`.
ROOM_DEFAULT = rooms.ROOM_DEFAULT
ROOM_PATTERN = rooms.ROOM_PATTERN
ROOM_ARCHIVE_DIR = rooms.ROOM_ARCHIVE_DIR
ROOM_LEGACY = rooms.ROOM_LEGACY      # надгробие ключа до 10.09.2026, см. deskd/rooms.py
is_room = rooms.is_room


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _append_jsonl(path: Path, row: dict) -> None:
    """Одна строка в архив комнаты — под тем же замком, что и квитанции.

    ⚠ Здесь замка не было (ревью 06.09, §5): поток приёма бота (`Rooms.record`)
    и главный поток руннера (`Desk.archive`, ответ на границе) дописывают один
    `groups/<комната>.jsonl`, и две строки могли склеиться в одну битую — окно
    её пропускает молча, а лента модели теряет реплику. Строка собирается ДО
    захвата замка, под замком — только запись и flush.
    """
    line = json.dumps(row, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        with path.open("a", encoding="utf-8", newline="\n") as sink:
            sink.write(line)
            sink.flush()


def _path_lock(path: Path) -> threading.Lock:
    """Замок на файл в пределах процесса: два потока не публикуют один файл разом."""
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


def _write_json(path: Path, data: dict) -> None:
    """Атомарная запись квитанции. Временное имя — СВОЁ у каждого писателя.

    ⚠ Здесь стояло `".tmp-" + path.name` — одно имя на всех. Писателей у одного
    файла двое и больше (поток приёма botapi и главный поток руннера пишут
    group_context/<комната>.json на каждом сообщении; heartbeat и _set_busy пишут
    .reader.json), и они затирали чужой недописанный файл: замер 4404 провала
    os.replace и 535 опубликованных БИТЫХ json за 4 секунды. Битая квитанция
    читателя = окно объявляет живой руннер мёртвым и предлагает перезапуск.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{os.getpid()}-{threading.get_ident()}-{path.name}")
    with _path_lock(path):
        _publish(tmp, path, data)


def _publish(tmp: Path, path: Path, data: dict) -> None:
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8", newline="\n")
        # На Windows os.replace отказывает (WinError 5/32), пока файл открыт
        # читателем — а читателей у квитанций двое (окно и телефон) и они
        # опрашивают их постоянно. Короткие повторы, потом честная ошибка наверх.
        for attempt in range(4):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.03)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _registry(tree: Path, stream: str, archive: Path, *,
              participants: int = 2, topics: int = 0) -> None:
    """Реестр комнаты для окна: сколько сообщений, когда последнее.

    ⚠ Две правки против прежней версии.
      1. Ошибка чтения архива писала В РЕЕСТР НОЛЬ поверх верных чисел: окно
         показывало комнату с нулём сообщений и роняло её в конец списка. Теперь
         при ошибке реестр не трогается вовсе — прежние числа честнее нуля.
      2. Счёт шёл полным перечитыванием файла на КАЖДОМ сообщении. Считаем
         приращением от прошлой расписки, а перечитываем только когда сверить
         не с чем (первая запись, файл усох, реестра нет).
    """
    try:
        stat = archive.stat()
    except OSError as exc:
        log.warning("реестр комнаты %s не обновлён (архив не читается): %s", stream, exc)
        return
    path = tree / "memory" / ".state" / "group_context" / (str(stream) + ".json")
    prev: dict = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            prev = loaded
    except (OSError, ValueError):
        prev = {}
    count = -1
    try:
        if prev.get("archive") and int(prev.get("archive_size") or 0) < stat.st_size:
            count = int(prev.get("message_count") or 0) + 1
    except (TypeError, ValueError):
        count = -1
    if count < 0:
        try:
            with archive.open(encoding="utf-8", errors="replace") as src:
                count = sum(1 for _ in src)
        except OSError as exc:
            log.warning("реестр комнаты %s не обновлён: %s", stream, exc)
            return
    try:
        _write_json(path, {"peer_id": str(stream),
                           "archive": "memory/groups/" + str(stream) + ".jsonl",
                           "message_count": count,
                           "participant_count": int(participants),
                           "topic_count": int(topics),
                           "archive_mtime_ns": stat.st_mtime_ns,
                           "archive_size": stat.st_size})
    except OSError as exc:
        log.warning("реестр комнаты %s не записался: %s", stream, exc)


class Desk:
    """Одна комната продукта: разговор владельца с ней в окне."""

    def __init__(self, tree: Path, stream: str, speaker: str, title: str,
                 memory_life=None, agent_name: str = "Агент"):
        self.tree = Path(tree)
        self.stream = str(stream)
        self.speaker = str(speaker)
        self.title = str(title)
        self.agent_name = str(agent_name)
        self._life = memory_life
        self.sent: list[str] = []          # что ушло рукой в ЭТОМ ходе

    # ------------------------------------------------------------- запись
    def archive(self, text: str, *, outgoing: bool, now: dt.datetime | None = None,
                sender: str = "", system: bool = False, kind: str = "",
                media_path: str = "", media_kind: str = "") -> None:
        """Лента комнаты в её формате: memory/groups/<поток>.jsonl + реестр состояния.

        Окно читает комнаты именно отсюда (deskd.readers.chats/chat_tail).

        ⚠ Здесь стояло обещание «а её `group_context` — из того же архива, один файл
        на обоих читателей». Оно неверно: её `group_context` читает
        `memory/groups/<слаг>/archive.jsonl` (live/group_context.py:104), то есть
        ДРУГОЙ путь. Обещание снято; сама рука честно отвечает про эту комнату через
        подмену в `install` ниже.

        `sender_name` пишется и у ИСХОДЯЩИХ строк: имя агента правится в настройках,
        а без подписи в строке вся прошлая переписка задним числом становилась
        сказанной новым именем — и в окне, и в ленте, которую читает модель.
        """
        stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
        row = {"timestamp": stamp, "outgoing": bool(outgoing), "text": str(text),
               "sender_name": (self.agent_name if outgoing else (sender or self.speaker))}
        # Вложение — ПУТЁМ ОТ ДЕРЕВА и видом, а не строкой в тексте. Окно по этим
        # двум полям рисует проигрыватель, канал по первому отдаёт байты, и ни
        # одно из двух не разбирает человеческую фразу «[файл] имя — путь».
        if media_path:
            row["media_path"] = str(media_path)
            row["media_kind"] = str(media_kind or "file")
        if system:
            # Служебная плашка продукта, а не слово агента: окно её показывает,
            # память жизни (и значит кадр модели) её не получает. `kind` — вид
            # плашки для окна: "silence" (молчание/ход без слова, серым),
            # пусто — тревога (⚠/⏸), как раньше.
            row["system"] = True
            if kind:
                row["kind"] = str(kind)
        archive = self.tree / "memory" / "groups" / (self.stream + ".jsonl")
        _append_jsonl(archive, row)
        _registry(self.tree, self.stream, archive)

    def life(self, text: str, *, direction: str, actor: str, source_id: str,
             now: dt.datetime | None = None) -> None:
        """Реплика — событие жизни (kind=conversation_message) в ЕЁ памяти дерева.

        Так же, как это делает живой транспорт: восприятие пишет память, кадр читает
        горячий слой. Без этого разговор жил бы только в архиве комнаты, а её кадр
        собирался бы из пустоты.
        """
        if self._life is None:
            return
        moment = now or dt.datetime.now(dt.timezone.utc)
        try:
            self._life.record_message(
                self.stream, text, actor=actor, direction=direction, source="window",
                source_id=source_id, is_dm=True, ts=moment.timestamp(),
                dedupe_key=f"window:{source_id}:{direction}")
        except Exception:
            log.exception("событие жизни не записалось (ход продолжается)")

    # ------------------------------------------------------------- чтение
    def rows(self, limit: int = 200) -> list[dict]:
        archive = self.tree / "memory" / "groups" / (self.stream + ".jsonl")
        # ⚠ errors="replace" — не косметика. Строгий декодер бросал
        # UnicodeDecodeError (подкласс ValueError, а не OSError) мимо этого
        # except, и ОДИН битый байт в архиве глушил агента навсегда: каждое
        # следующее сообщение владельца съедалось падением хода, а окно
        # показывало чат целым — оно читает тот же файл с заменой.
        try:
            with archive.open(encoding="utf-8", errors="replace") as src:
                lines = src.readlines()[-max(1, int(limit)):]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out

    def lines(self, limit: int = 200) -> list[str]:
        """Лента строками «Имя: текст» — тот же вид, в каком её видит живой раннер.

        Имя берётся ИЗ СТРОКИ архива, текущее — только для старых строк без него.
        Иначе переименование агента в настройках переписывало авторство всей
        прошлой переписки, включая ход, где он представился прежним именем.

        Служебные плашки продукта (`system`) в ленту модели не идут: их пишет
        харнесс, а не агент, и подписаны они не им.
        """
        out = []
        for row in self.rows(limit):
            if row.get("system"):
                continue
            who = str(row.get("sender_name") or "").strip() or (
                self.agent_name if row.get("outgoing") else self.speaker)
            text = str(row.get("text") or "").strip()
            if text:
                out.append(f"{who}: {text}")
        return out

    # ------------------------------------------------------------ доставка
    def deliver_once(self, text: str, *, key: str) -> str:
        """Принять конкретную доставку один раз, даже после рестарта процесса."""
        if not key:
            raise ValueError("delivery key must not be empty")
        body = str(text)
        now = dt.datetime.now(dt.timezone.utc)
        archive = self.tree / "memory" / "groups" / (self.stream + ".jsonl")
        message_id = "helene-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        with _path_lock(archive):
            archive.parent.mkdir(parents=True, exist_ok=True)
            existing = None
            if archive.exists():
                with archive.open(encoding="utf-8", errors="replace") as source:
                    for line in source:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if row.get("delivery_key") == key:
                            existing = row
                            break
            if existing is not None:
                if existing.get("text") != body or existing.get("outgoing") is not True:
                    raise ValueError("delivery key already belongs to different content")
                message_id = existing["message_id"]
                now = dt.datetime.fromisoformat(existing["timestamp"])
            else:
                row = {"timestamp": now.isoformat(timespec="seconds"), "outgoing": True,
                       "text": body, "sender_name": self.agent_name,
                       "delivery_key": key, "message_id": message_id}
                data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
                with archive.open("ab+") as sink:
                    if sink.tell():
                        sink.seek(-1, os.SEEK_END)
                        if sink.read(1) != b"\n":
                            data = b"\n" + data
                    sink.write(data)
                    sink.flush()
                    os.fsync(sink.fileno())
        _registry(self.tree, self.stream, archive)
        self.life(body, direction="out", actor=self.agent_name, source_id=key, now=now)
        return str(message_id)

    def deliver(self, text: str, *, source_id: str = "", label: str = "",
                system: bool = False, kind: str = "") -> str:
        """Её слово доехало до окна. Возвращаем расписку в том же виде, что транспорт.

        Расписка — не косметика: рука `reply` дописывает к ней подсказку про `end_turn`,
        а `send_message` отличает по типу отказ от квитанции. Пустая или невнятная
        расписка сделала бы её ход слепым к тому, состоялась ли отправка.

        `system=True` — плашка харнесса («⚠ ход не состоялся», «⏸ ход приостановлен»).
        ⚠ Раньше они шли этим же путём как ЕЁ СЛОВО: в память жизни ложилось, что
        агент сказал «⚠ ход не состоялся», и на следующем ходу модель читала это
        своей репликой (а recall — своей мыслью). Плашка теперь живёт только в
        окне: владелец её видит, память агента — нет.
        """
        now = dt.datetime.now(dt.timezone.utc)
        body = str(text or "")
        self.archive(body, outgoing=True, now=now, system=system, kind=kind)
        if system:
            return f"Показано в окне → {label or self.title} (плашка продукта)"
        self.life(body, direction="out", actor=self.agent_name,
                  source_id=source_id or f"deliver-{int(time.time() * 1000)}", now=now)
        self.sent.append(body)
        # Канал назван в расписке НЕ для красоты: когда транспортов стало два, а
        # адресат один и тот же человек, безадресное «Отправила → владелец» позволило
        # ей честно поверить, что слово ушло в Telegram, когда оно легло в окно.
        return f"Отправлено → {label or self.title} (окно Frame, id {len(self.sent)})"


class Desks:
    """Комнаты окна: один `Desk` на ключ, память жизни — одна на всех.

    Задача A §3: без Telegram окно — основной канал, и одной комнаты мало.
    Реестр комнат (имена, создание, архив) ведёт канал (`deskd/rooms.py`);
    здесь — только чтение имён и ленивое рождение `Desk` под ключ. Имя комнаты
    перечитывается на каждом `get`: владелец переименовал в окне — следующий
    ход уже идёт с новым именем в кадре.
    """

    def __init__(self, tree: Path, speaker: str, title: str, memory_life=None,
                 agent_name: str = "Агент"):
        self.tree = Path(tree)
        self.speaker = str(speaker)
        self.title = str(title)            # имя продукта — подпись расписок
        self.agent_name = str(agent_name)
        self._life = memory_life
        self._by_key: dict[str, Desk] = {}
        self.default = self.get(ROOM_DEFAULT)

    def room_title(self, key: str) -> str:
        return rooms.title(self.tree, key, self.agent_name)

    def get(self, key: str) -> Desk:
        key = str(key or ROOM_DEFAULT)
        if not is_room(key):
            raise ValueError(f"не комната окна: {key!r}")
        desk = self._by_key.get(key)
        if desk is None:
            desk = Desk(self.tree, key, self.speaker, self.room_title(key),
                        memory_life=self._life, agent_name=self.agent_name)
            self._by_key[key] = desk
        else:
            desk.title = self.room_title(key)
        return desk

    def keys(self) -> list[str]:
        return [ROOM_DEFAULT] + sorted(k for k in rooms.load(self.tree) if k != ROOM_DEFAULT)

    def find(self, ref) -> "Desk | None":
        """Комната по ключу или по имени (без учёта регистра). None — не наша."""
        ref = str(ref or "").strip()
        if not ref:
            return None
        if is_room(ref) and (ref == ROOM_DEFAULT or ref in rooms.load(self.tree)
                             or rooms.archive_path(self.tree, ref).exists()):
            return self.get(ref)
        low = ref.lower()
        for key in self.keys():
            if self.room_title(key).lower() == low:
                return self.get(key)
        return None

    def current(self, agent_mod) -> Desk:
        """Комната текущего хода (по `_TURN_CHANNEL` дерева), иначе — по умолчанию."""
        try:
            ctx = agent_mod._TURN_CHANNEL.get()
        except Exception:
            ctx = None
        chat_id = str(getattr(ctx, "chat_id", "") or "") if ctx is not None else ""
        return self.get(chat_id) if is_room(chat_id) else self.default

    def listing(self) -> str:
        return ", ".join(f"«{self.room_title(k)}» ({k})" for k in self.keys())


def _honest_group_context(agent_mod, tree: Path) -> None:
    """Рука ориентирования в комнате не должна врать про этот продукт.

    Её `group_context` читает каноническую раскладку дерева —
    `memory/groups/<слаг>/archive.jsonl` (live/group_context.py:104), — а продукт
    пишет переписку одним файлом `memory/groups/<id>.jsonl`. В личке (в том числе
    в окне) рука честно отказывает сама («доступен только внутри текущей группы»),
    а вот в ГРУППЕ Telegram она отвечала «0 сообщений, 0 тем» о живой комнате и
    заводила рядом пустую карту MAP.md.

    Подменяем реализацию (тот же приём, что у песочницы с TOOL_IMPL): вместо
    выдуманного нуля — правда о раскладке и настоящее число сообщений.
    """
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if not isinstance(impl, dict) or "group_context" not in impl:
        return
    original = impl["group_context"]

    def _group_context(action: str = "context", query: str = "",
                       topic_id: int = 0, limit: int = 20) -> str:
        ctx = None
        try:
            ctx = agent_mod._TURN_CHANNEL.get()
        except Exception:
            ctx = None
        if ctx is None or getattr(ctx, "is_dm", True):
            return original(action=action, query=query, topic_id=topic_id, limit=limit)
        chat_id = str(getattr(ctx, "chat_id", "") or "")
        count = 0
        try:
            path = (Path(tree) / "memory" / ".state" / "group_context"
                    / (chat_id + ".json"))
            loaded = json.loads(path.read_text(encoding="utf-8"))
            count = int(loaded.get("message_count") or 0) if isinstance(loaded, dict) else 0
        except (OSError, ValueError, TypeError):
            count = 0
        return ("Карты комнаты в этом продукте нет: переписка лежит одним архивом "
                f"memory/groups/{chat_id}.jsonl, а не в раскладке group_context. "
                f"Сообщений в комнате: {count}. Читай ленту рукой fetch_context "
                "или read_chat — они читают тот самый архив.")

    _group_context.__name__ = getattr(original, "__name__", "tool_group_context")
    _group_context.__doc__ = getattr(original, "__doc__", "")
    impl["group_context"] = _group_context


def install(agent_mod, desks: Desks) -> None:
    """Положить окно в `agent._TELETHON`. Вызывается один раз на старте раннера.

    Комнат окна много (задача A §3): адрес — ключ (`window`, `window-<hex>`)
    или имя комнаты; без адреса — комната текущего хода. Telegram-адреса здесь
    не наши: на них честный отказ особого типа.
    """

    hooks = agent_mod._TELETHON

    def _deliver(desk, text, *, label=""):
        scope = getattr(agent_mod, "_TOOL_EXECUTION", None)
        execution = scope.get() if scope is not None else None
        key = str((execution or {}).get("idempotency_key") or "")
        if key:
            message_id = desk.deliver_once(text, key=key)
            desk.sent.append(str(text))
            return f"Отправлено → {label or desk.title} (окно Hélène, id {message_id})"
        return desk.deliver(text, label=label)

    def _refusal(target: str):
        # ⚠ Отказ СТРОКОЙ особого типа, а не исключением и не обычной квитанцией:
        # `DirectSendRefusal` — её способ отличить «не ушло» от «ушло», не нюхая текст.
        # Обычная строка здесь засчиталась бы как доставка, и её запись хода соврала бы.
        return agent_mod.DirectSendRefusal(
            f"не отправила: наружу писать нечем — в этом продукте нет транспорта до "
            f"«{target}». Есть только комнаты окна: {desks.listing()}; чтобы сказать "
            f"это владельцу, отвечай обычной рукой ответа.")

    def _reply(chat_id, text, reply_to="") -> str:
        desk = desks.find(chat_id)
        if desk is None:
            return agent_mod.DirectSendRefusal(
                f"не отправила: адрес «{chat_id}» — не комната окна. "
                f"Комнаты окна: {desks.listing()}.")
        return _deliver(desk, text, label=desks.speaker)

    def _send_message(to, text) -> str:
        target = str(to or "").strip()
        if target in ("", desks.speaker):
            return _deliver(desks.current(agent_mod), text, label=desks.speaker)
        desk = desks.find(target)
        if desk is not None:
            return _deliver(desk, text, label=desks.speaker)
        return _refusal(target)

    def _send_file(path, caption="", to="", media_kind="document",
                   voice_note=False) -> str:
        src = Path(str(path))
        if not src.is_file():
            return f"Нет файла {path}."
        target = str(to or "").strip()
        desk = desks.current(agent_mod) if target in ("", desks.speaker) else desks.find(target)
        if desk is None:
            return _refusal(target)
        # Файл ВНУТРИ дерева окно умеет показать само: канал отдаёт байты по пути
        # от дерева (`/api/media`), окно рисует проигрыватель. Так приезжает голос
        # агента: `media_audio` кладёт WAV в `<дерево>/media/tts`.
        #
        # ⚠ Файл СНАРУЖИ дерева остаётся строкой с путём, и это не лень: канал,
        # отдающий любой путь с диска, — файловый сервер на весь компьютер, а не
        # окно агента. Владелец откроет такой файл сам.
        rel = ""
        try:
            rel = src.resolve().relative_to(Path(desks.tree).resolve()).as_posix()
        except (ValueError, OSError):
            rel = ""
        kind = str(media_kind or "document")
        if voice_note or kind in ("audio", "voice"):
            kind = "audio"
        note = f"[{'голос' if kind == 'audio' else 'файл'}] {src.name}"
        if not rel:
            note += f" — {src}"
        if str(caption or "").strip():
            note += "\n" + str(caption).strip()
        if rel:
            desk.archive(note, outgoing=True, media_path=rel, media_kind=kind)
            desk.life(note, direction="out", actor=desk.agent_name,
                      source_id=f"file-{int(time.time() * 1000)}")
            desk.sent.append(note)
            return f"Отправлено → {desks.speaker} (окно Hélène, вложение {src.name})"
        return _deliver(desk, note, label=desks.speaker)

    def _fetch_context(chat_id, limit: int = 50) -> str:
        desk = desks.find(chat_id)
        if desk is None:
            return "(нет такого чата)"
        return "\n".join(desk.lines(int(limit)))

    def _read_chat(chat_ref, limit: int = 30) -> str:
        desk = desks.find(chat_ref)
        if desk is None:
            return f"(не нашла такой чат — комнаты окна: {desks.listing()})"
        return "\n".join(desk.lines(int(limit)))

    def _search_chats(query: str) -> str:
        needle = str(query or "").strip().lower()
        hits = [f"{desks.room_title(k)}: {k}" for k in desks.keys()
                if not needle or needle in desks.room_title(k).lower() or needle in k.lower()]
        if not hits:
            return f"(ничего не нашла — комнаты окна: {desks.listing()})"
        return "\n".join(hits)

    def _search_private_messages(query: str, limit: int = 20) -> str:
        needle = str(query or "").strip().lower()
        if not needle:
            return "Нужна непустая строка поиска."
        keys = desks.keys()
        hits = []
        for key in keys:
            title = desks.room_title(key)
            for line in desks.get(key).lines(2000):
                if needle in line.lower():
                    hits.append(f"[{title}] {line}" if len(keys) > 1 else line)
        if not hits:
            return "(ничего не нашла)"
        return "\n".join(hits[-max(1, int(limit)):])

    def _get_id(name_or_username: str):
        desk = desks.find(name_or_username)
        return desk.stream if desk is not None else None

    _honest_group_context(agent_mod, desks.tree)

    hooks["reply"] = _reply
    hooks["send_message"] = _send_message
    hooks["send_file"] = _send_file
    hooks["fetch_context"] = _fetch_context
    hooks["read_chat"] = _read_chat
    hooks["search_chats"] = _search_chats
    hooks["search_private_messages"] = _search_private_messages
    hooks["get_id"] = _get_id
    hooks["resolve_entity"] = _get_id
    # Сенсор транспорта для сторожа тишины: локальное окно всегда «на связи», и
    # окна намеренного разрыва (как у Telegram при перелогине) здесь не бывает.
    hooks["transport_state"] = lambda: {"connected": True, "intentional_window": False}
    log.info("транспорт: окно вложено в её шов _TELETHON (%d крючка)", len(hooks))
