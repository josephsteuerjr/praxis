# -*- coding: utf-8 -*-
"""Окно Frame как транспорт агента: тот же шов, на котором висит Telethon.

`agent._TELETHON` — обычный словарь вызываемых, который живой раннер наполняет на
старте (mtproto_runner: reply, send_message, read_chat, get_id, transport_state…).
Ни одна её рука не знает, кто там лежит: она зовёт «отправить» и получает расписку.
Значит десктопный продукт не нуждается ни в одной правке её кода — он просто кладёт
в этот словарь свои функции. Это тот же приём, что труба `praxis.desk.v1` для
оболочки: один шов, за которым может стоять что угодно.

Что здесь есть по-настоящему:
  * доставка её реплики в Пульт (архив комнаты + событие жизни + расписка ей);
  * чтение своей же комнаты (`read_context`, `read_chat`, поиск по переписке);
  * честный отказ на адресатов, которых в этом продукте нет.

Чего здесь НЕТ и почему: крючки Telegram-специфики (вступить в чат, реакции, аватар,
модерация, telegram_account) НЕ регистрируются. Её тулы на отсутствующем крючке
отвечают «Telegram-мост сейчас недоступен» — это правда об этом продукте, и она
лучше, чем заглушка, которая делает вид, что действие состоялось.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("frame.transport")


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as sink:
        sink.write(json.dumps(row, ensure_ascii=False) + "\n")


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


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
    """Одна комната продукта: разговор владельца с ней в окне Пульта."""

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
                sender: str = "", system: bool = False) -> None:
        """Лента комнаты в её формате: memory/groups/<поток>.jsonl + реестр состояния.

        Пульт читает комнаты именно отсюда (deskd.readers.chats/chat_tail).

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
        if system:
            # Служебная плашка продукта, а не слово агента: окно её показывает,
            # память жизни (и значит кадр модели) её не получает.
            row["system"] = True
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
    def deliver(self, text: str, *, source_id: str = "", label: str = "",
                system: bool = False) -> str:
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
        self.archive(body, outgoing=True, now=now, system=system)
        if system:
            return f"Показано в окне → {label or self.title} (плашка продукта)"
        self.life(body, direction="out", actor=self.agent_name,
                  source_id=source_id or f"deliver-{int(time.time() * 1000)}", now=now)
        self.sent.append(body)
        # Канал назван в расписке НЕ для красоты: когда транспортов стало два, а
        # адресат один и тот же человек, безадресное «Отправила → владелец» позволило
        # ей честно поверить, что слово ушло в Telegram, когда оно легло в окно.
        return f"Отправлено → {label or self.title} (окно Frame, id {len(self.sent)})"


def _honest_group_context(agent_mod, desk: Desk) -> None:
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
            path = (desk.tree / "memory" / ".state" / "group_context"
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


def install(agent_mod, desk: Desk) -> None:
    """Положить Пульт в `agent._TELETHON`. Вызывается один раз на старте раннера."""

    hooks = agent_mod._TELETHON

    def _reply(chat_id, text, reply_to="") -> str:
        if str(chat_id) != desk.stream:
            return agent_mod.DirectSendRefusal(
                f"не отправила: в этом продукте одна комната — «{desk.title}», "
                f"а адрес «{chat_id}» ей не принадлежит.")
        return desk.deliver(text, label=desk.speaker)

    def _send_message(to, text) -> str:
        target = str(to or "").strip()
        if target in ("", desk.stream, desk.speaker, desk.title):
            return desk.deliver(text, label=desk.speaker)
        # ⚠ Отказ СТРОКОЙ особого типа, а не исключением и не обычной квитанцией:
        # `DirectSendRefusal` — её способ отличить «не ушло» от «ушло», не нюхая текст.
        # Обычная строка здесь засчиталась бы как доставка, и её запись хода соврала бы.
        return agent_mod.DirectSendRefusal(
            f"не отправила: наружу писать нечем — в этом продукте нет транспорта до "
            f"«{target}». Есть только окно Пульта; чтобы сказать это владельцу, отвечай "
            f"обычной рукой ответа.")

    def _send_file(path, caption="", to="", media_kind="document",
                   voice_note=False) -> str:
        src = Path(str(path))
        if not src.is_file():
            return f"Нет файла {path}."
        note = f"[файл] {src.name} — {src}"
        if str(caption or "").strip():
            note += "\n" + str(caption).strip()
        # v1: файл остаётся на месте, в окно уезжает названный путь. Показ вложений
        # внутри чата Пульта — отдельная работа; обещать её распиской нельзя.
        return desk.deliver(note, label=desk.speaker)

    def _fetch_context(chat_id, limit: int = 50) -> str:
        if str(chat_id) != desk.stream:
            return "(нет такого чата)"
        return "\n".join(desk.lines(int(limit)))

    def _read_chat(chat_ref, limit: int = 30) -> str:
        if str(chat_ref) not in (desk.stream, desk.title, desk.speaker):
            return ("(не нашла такой чат — в этом продукте одна комната: "
                    f"«{desk.title}»)")
        return "\n".join(desk.lines(int(limit)))

    def _search_chats(query: str) -> str:
        needle = str(query or "").strip().lower()
        if not needle or needle in desk.title.lower() or needle in desk.stream.lower():
            return f"{desk.title}: {desk.stream}"
        return "(ничего не нашла — здесь одна комната: " + desk.title + ")"

    def _search_private_messages(query: str, limit: int = 20) -> str:
        needle = str(query or "").strip().lower()
        if not needle:
            return "Нужна непустая строка поиска."
        hits = [line for line in desk.lines(2000) if needle in line.lower()]
        if not hits:
            return "(ничего не нашла)"
        return "\n".join(hits[-max(1, int(limit)):])

    def _get_id(name_or_username: str):
        ref = str(name_or_username or "").strip()
        if ref in (desk.stream, desk.title, desk.speaker):
            return desk.stream
        return None

    _honest_group_context(agent_mod, desk)

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
    log.info("транспорт: Пульт вложен в её шов _TELETHON (%d крючка)", len(hooks))
