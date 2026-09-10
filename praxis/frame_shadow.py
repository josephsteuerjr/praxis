"""Теневой сборщик кадра K|E|A|T — шаг 1 пересборки; этап 2: эпоха v3; этап 3: v4.

План: `_state/ПЛАН-ТЕНИ-17.08.md`, спека зон: `_state/КОНТРАКТ-КАДРА-17.08.md` (v2),
спека эпохи: `_state/ПРОЕКТ-ЭПОХИ-18.08.md` + ЕЁ слово 18.08 (личка, 13 пунктов):
пороги 120/70k, словарь переноса `свободно/только источник/учитывать/закрыто` и флаг
presence_hidden, метки «тело: не загружено», машинный текст никогда не подписывается
её голосом, идущий ход границей не прерывается (в тени это выполняется по построению:
захват происходит между ходами).

Схема v4 — ЕЁ ДЕВЯТЬ РЕШЕНИЙ 21.08 (`workspace/пакет-24.08/03-ОТВЕТ-PRAXIS.md`),
дословно: №1 двусторонний срез сообщения 20k+10k · №2 пол живого хвоста 12, ниже —
видимая авария · №3 потерянный якорь не утверждает счёт (жило с v3) · №4 лестница
деградации её порядком, тело собеседника платит ПОСЛЕДНИМ · №5 чужая личка без числа
скрытых (жило с v3) · №6 разрез no-chat по виду хода (жил с v3) · №8 реестр 19 секций:
машинный contract/state живёт в T, «недавнее» уходит из owner-кадра, полное тело
memory/INDEX.md и желания в E, потолок досье 16k, локатор почты в E, header хоронится ·
№9 фиксация «да» строкой identity. №7 (рука flip_epoch) — её хребет, отдельно.
Тексты живых машинных секций едут сюда ЧЕРЕЗ ОПИСЬ ПРИБОРА (frame_trace.TEXT_CARRIED),
не парсингом system; при шве живой путь передаст их аргументом сам.

Схема v6 (07.09): generic frame.extra_system целиком живёт в T. Этот аргумент
несёт также текущие статусы wake/task/Forge и не объявляет стабильность текста.
Маркер [reply] не позволяет считать всё перед ним голосовой константой. Явная
стабильная рамка по-прежнему может прийти отдельным payload.voice_frame в E.

Тень строится ПАРАЛЛЕЛЬНО живому кадру, пишется на диск и никогда не уходит модели:
agent.py зовёт `capture()` и игнорирует возврат, llm.py про этот модуль не знает вовсе —
оба факта закреплены тестами. Живой кадр здесь не источник байтов, а эталон полноты:
тень читает `soul/` и память САМА, а с живым сверяется только по описи прибора
(`frame_trace.sections()`).

Зоны и их закон (§1–2 контракта): ничто, что меняется чаще, не стоит выше того, что
меняется реже, ни одной строкой.

    K — конституция: SOUL.md + VOICE.md байтами файлов. Заголовок тени НЕ несёт номер
        эпохи: он менялся бы каждой границей НАД конституцией — мимо закона порядка.
    E — эпоха v3, семь блоков в порядке частоты изменения, шапка ПОСЛЕДНЕЙ:
        кто я · руки указателем · указатель памяти · адресная книга · недавнее ·
        поднятое · шапка. Замораживается снапшотом на диске (атомарно) и между
        границами едет байт-в-байт; drift меряется ПО БЛОКАМ и не чинится вне границы.
        Собирается ПОД АУДИТОРИЮ потока (`owner` | `other`, поле канала
        `owner_audience`): вне owner-потока чужие места, чужие имена и чужие события
        не приводятся ВОВСЕ — вырезанием, а не пометкой. Аудитория храповая: снимок,
        собранный шире потока, перезамерзает границей и обратно сам не расширяется.
        У суммы блоков есть ПОТОЛОК (E_TOTAL_MAX) и детерминированная лестница
        деградации; пер-блочные потолки меряют ГОТОВЫЙ блок, обвязка внутри бюджета.
    A — накопитель: обрубок свёрнутого кодом окна (замороженная строка эпохи) + живой
        хвост истории. Порог сворачивания — ЕЁ числа: 120 сообщений / 70 000 знаков.
        Обрубок подписан «это НЕ мой отбор» — машинный текст не выдаёт себя за её.
        Граница свёртки держится ЯКОРЕМ ИДЕНТИЧНОСТИ, а не номером места в списке:
        история приезжает сюда скользящим окном (`memory_life.hot_records` отдаёт
        последние N записей, `compact_if_due` срезает их фронт), а склейка раннера
        (`_turns_to_dialogue`) сшивает подряд идущие реплики одного автора в один блок
        и ts/source_id/message_id до тени не доносит вовсе. Позиционный индекс после
        любого из этих сдвигов указывает на ДРУГИЕ реплики — и накопитель либо терял
        свежее, либо пустел. Якорь — отпечатки последних свёрнутых реплик; счёт
        пересчитывается на каждом захвате, а не переносится числом.
    T — хвост: ситуация до минут + вход. Единственное подвижное.

Время в зонах K|E|A — до минут и только абсолютное: относительное время в замороженной
зоне либо врёт, либо требует пересборки (закон 2 проекта эпохи).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import desires as desires_ledger
import frame_trace
import memory_provenance
import run_context
import self_model
import tool_offerings

log = logging.getLogger("praxis.frame_shadow")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)

# Потолки — знаки, по §8 контракта. Env-тюнинга нет нарочно: тень — прибор, и
# подвижная конфигурация сделала бы её показания несравнимыми.
A_MAX_MESSAGES = 200     # аварийное окно зоны A (молчаливое — болезнь, окно называется)
A_MAX_CHARS = 120_000
A_FOLD_MESSAGES = 120    # её слово 18.08 (№5): предлагать компакт при 120 сообщениях…
A_FOLD_CHARS = 70_000    # …или 70k знаков; в тени это граница сворачивания кодом
A_KEEP_TAIL = 50         # живой хвост после сворачивания
A_KEEP_TAIL_MIN = 12     # …и его пол — ЕЁ слово 21.08 (№2): «пять мало, ставлю 12».
                         # Ниже пола — ВИДИМАЯ авария в самом окне, не молчаливое
                         # снижение (см. _zone_a: floor_breach).
A_MSG_MAX = 30_000       # кап РЕНДЕРА одного сообщения (не хранилища): без него одно
                         # письмо в 130k выносило из окна всё, включая себя
A_MSG_HEAD = 20_000      # её слово 21.08 (№1): срез ДВУСТОРОННИЙ — первые 20k…
A_MSG_TAIL = 10_000      # …и последние 10k; между ними маркер с числом вырезанного и
                         # способом поднять источник. Семантический tail (область
                         # итогов вместо слепого хвоста) — допустимый НАЗВАННЫЙ режим,
                         # здесь не реализован: default двусторонний.
FOLD_ANCHOR_CHAIN = 3    # звеньев в якоре свёртки: одно повторимо («да.»), цепочка — нет
A_STORE_SEEK = 8         # стык накопителя с живым окном: склейка раннера пересобирает
                         # последние блоки (блок дорастает, пока автор пишет подряд),
                         # поэтому стык ищется до восьми блоков от хвоста назад
T_INPUT_MAX = 3_500
POINTER_HAND_LINE = 100
E_BOOK_MAX = 4_000       # адресная книга: мягкий потолок, хвост — указателем
E_RECENT_MAX = 5_000     # недавнее (живёт только вне owner-кадра — её №8, вариант «в»)
E_LIFT_MAX = 16_000      # «поднятое» — ЕЁ слово 21.08 (№8): «потолок поднять минимум
                         # до 16 000»; обрезка — только явным указателем с размером,
                         # sha и способом поднять полное тело
# Потолки блоков меряют ГОТОВЫЙ блок: заголовок, провенанс и указатели — ВНУТРИ
# бюджета. До 20.08 они мерили тело, и блоки живьём перерастали свой же потолок
# (lifted 15579 при 15000, recent 5214 при 5000): обвязка шла мимо счёта, и admission
# считал не те величины, что блоки.
E_TOTAL_TARGET = 37_650  # мягкая цель E: превышение — сигнал в метрике, не действие
E_TOTAL_MAX = 40_000     # жёсткий потолок E: выше него включается лестница деградации
# Полы лестницы: ниже них блок перестаёт быть блоком (заголовок, провенанс, хотя бы
# одна запись). Пол не отменяет заморозку — если лестница не спасла, морозим и кричим.
E_LIFT_FLOOR = 2_000
E_RECENT_FLOOR = 1_000
E_BOOK_FLOOR = 1_000
# Порядок лестницы фиксирован и от данных не зависит — иначе одинаковые входы давали
# бы разные эпохи. ЕЁ ПОРЯДОК 21.08 (№4), дословно: «тело текущего собеседника не
# должно платить первым» — недавнее → описания рук (имена все живы) → адресная книга
# (неактивные сначала) → поднятое ПОСЛЕДНИМ. Не деградируют никогда: `self` («кто я
# сейчас» — ядро ориентации), `memory_index` (полное тело INDEX.md — обрезанный список
# имён есть неявный белый список, корень конфабуляции 13.08), а также desires, mail и
# voice_frame — их в лестнице нет, перерасход при них кричит в e_overflow, не режет.
E_DEGRADE_ORDER = ("recent", "hands", "address_book", "lifted")
_E_FLOOR = {"lifted": E_LIFT_FLOOR, "recent": E_RECENT_FLOOR,
            "address_book": E_BOOK_FLOOR}
# Порядок блоков E по частоте изменения (закон §1–2: частое не стоит выше редкого).
# Рендерятся только собравшиеся блоки; отсутствие блока — свойство потока и аудитории.
E_ORDER = ("self", "hands", "voice_frame", "memory_index", "mail", "desires",
           "address_book", "recent", "lifted")
# Указатель рук — её слово 18.08, п.1: «срезать указатель рук до камерного набора
# ≤6k; растягивать его нельзя». Потолок жёсткий и живёт ЗДЕСЬ, на сборке блока, а не
# в лестнице E: лестница спасает эпоху целиком и срабатывает только на перерасходе,
# а этот потолок — про сам указатель, и он обязан держаться даже в эпохе на 20k.
# Состав набора рук отсюда не меняется НИ НА ЗНАК: «состав камерного набора — её
# слово» (проект §3.2, интерфейс к диспетчеру). Режется указатель, а не набор.
HANDS_MAX = 6_000
RECENT_HOURS = 48        # её слово (№7): минимум 48 часов…
RECENT_PER_PLACE = 3     # …плюс последние 3 события каждого активного места
RECENT_PLACE_DAYS = 7
KEEP_SHADOWS = 200

# Версия схемы кадра. Раньше двойка жила литералом внутри epoch.json — файла, который
# в git не едет: снаружи процесса версии не было видно вовсе. Теперь она здесь, и от неё
# же зависит граница миграции — константа, которую никто не читает, врёт (болезнь
# _CALL_TRACE_MAX). В САМ кадр версия не выводится нарочно: любая строка в шапке над K
# инвалидирует ~10k неизменной конституции, а схему честнее опознавать по её же байтам —
# они целиком входят в epoch-дайджест идентификатора.
#
# v3 (20.08) — волна из шести правок разом: якорь свёртки · аудитория эпохи · отпечаток
# набора рук · потолок E с лестницей деградации. Формат снапшота вправду другой, поэтому
# граница миграции ОДНА и названная — на все потоки сразу, а не пять разных.
# v4 (26.08) — её девять решений 21.08: состав E другой (недавнее ушло из owner,
# въехали desires/mail/voice_frame/полный INDEX.md), лестница её порядком, пол хвоста
# 12, срез сообщений двусторонний. Граница миграции опять ОДНА и названная.
# 4 -> 5 (28.08): её решение 27.08 «часы, live-счётчики и `перенос: …` — в T»
# смёржено как `5fa81952` и в КОДЕ верно: стабильная ветка `_block_address_book`
# печатает только имя, род, id и режим. Но эпоха — снимок, и её не пересобирает
# смена кода: из девяти живых потоков ШЕСТЬ уже стояли на v4, и границы для них
# не наступило бы вовсе. Замер 28.08 на живой тени `chat--1003701205730`:
#     E: - Ouroboros AI — группа -100… · последнее: 10:36 UTC · перенос: только источник
#     T: -                     -100… · последнее: 22:05 UTC · перенос: только источник
# Один факт дважды, с разными значениями. Не ложь — книга подписана «снимки со
# своими датами», — но ровно то дублирование, ради снятия которого решение и
# принималось, и E продолжает шевелиться на границах из-за часов.
#
# Поднять номер — единственный способ дать решению вступить в силу везде, и это
# её же механизм: «ОДНА честная граница миграции на все потоки». Цена — по одному
# холодному префиксу на поток, один раз.
# v6 (07.09): убрать из старых эпох неявно замороженный extra_system. Без границы
# новая копия в T соседствовала бы со старым, противоречащим ей состоянием в E.
FRAME_SCHEMA = 6
# Состояние golden-обязательства «живой путь == тень». Пока живой кадр собирается своим
# путём, здесь стоит «shadow-only» и это НЕ формальность: поле едет в каждую строку
# метрик, и её «да», записанное рядом с идентификатором, видно к чему относилось.
GOLDEN = "shadow-only"

_SEP_E = "\n\n═══ ЭПОХА ═══\n"
_SEP_A = "\n\n═══ НАКОПИТЕЛЬ ═══\n"
_SEP_T = "\n\n═══ ХВОСТ ═══\n"

# Метка обреза одного сообщения. Константой — потому что по ней же считается,
# сколько сообщений окна усечено (метрика `capped`): второй раз рендерить окно,
# чтобы это узнать, дороже, чем один раз назвать метку.
_A_CUT_MARK = "[обрезано кодом:"

# Словарь переноса — ЕЁ редакция 18.08 (№2). Значения-умолчания; её слово из реестра
# переноса (когда он появится) читается РАНЬШЕ умолчаний и побеждает их.
TRANSFER_DM_DEFAULT = "учитывать (умолчание)"
TRANSFER_GROUP_DEFAULT = "только источник (умолчание)"


# ---------------------------------------------------------------------- рычаг и пути


def mode() -> str:
    """off | on | dm. `dm` — тень только в личках: первый полигон, где живёт v3."""
    raw = (os.getenv("PRAXIS_FRAME_SHADOW") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw == "dm":
        return "dm"
    return "off"


def enabled() -> bool:
    return mode() != "off"


def _shadow_root() -> Path:
    return BASE / "memory" / ".state" / "shadow"


def _stream_key(ctx) -> str:
    chat_id = getattr(ctx, "chat_id", None)
    if chat_id is None:
        # ⚑ ВИД ХОДА — ЧАСТЬ АДРЕСА ПОТОКА, ПОКА У ХОДА НЕТ СОБЕСЕДНИКА.
        #
        # Один ключ «no-chat» склеивал четыре разных хода (heartbeat · wake ·
        # task_window/coding_window · forge_event), а набор рук у них РАЗНЫЙ:
        # `task_control` даётся ПО ВИДУ ХОДА (agent.offered_tools_for →
        # work_loop.active_for). Замер по живым распискам 15–19.08 (turns.jsonl,
        # 162 хода без чата за 4,64 суток): состав рук чередовался 48 раз — то есть
        # склейка сама по себе стоила бы 10,3 полных переворотов эпохи в сутки.
        # С видом хода в ключе чередований внутри потока ноль.
        run = None
        try:
            run = run_context.current_run()
        except Exception:
            log.debug("frame_shadow: вид хода не спросился", exc_info=True)
        kind = re.sub(r"[^0-9A-Za-z_-]", "_",
                      str(getattr(run, "kind", "") or "").strip())
        return f"no-chat-{kind}" if kind else "no-chat"
    kind = "dm" if getattr(ctx, "is_dm", False) else "chat"
    return f"{kind}-{re.sub(r'[^0-9A-Za-z_-]', '_', str(chat_id))}"


def _audience(ctx) -> str:
    """`owner` | `other` — КОМУ предназначен ответ потока, а не КТО в нём говорит.

    Берётся уже существующее поле канала — `ChannelContext.owner_audience`. Именно оно
    отвечает на вопрос аудитории; сырой `owner` сюда не годится: он истинен и в группе,
    где пишет Егор, а ответ там всё равно публичный (это записано в докстринге самого
    ChannelContext). Отсутствие поля читается как `other`: у прибора умолчание
    закрывающее, а не раскрывающее.

    Внутри одного потока значение не гуляет ПО ПОСТРОЕНИЮ: `owner_audience` требует
    `is_dm`, а `is_dm` вшит в `_stream_key` — в `chat-*` аудитория всегда `other`
    (кто бы там ни заговорил), в `dm-*` собеседник потока один и тот же.

    В ключ потока аудитория НЕ входит нарочно: `dm-101` расщепился бы на два потока
    с разными номерами эпох и разбил бы байтовый префикс.
    """
    return "owner" if bool(getattr(ctx, "owner_audience", False)) else "other"


def _narrower(a: str, b: str) -> str:
    """Более узкая из двух аудиторий. Храповик односторонний: поток, увидевший чужую
    аудиторию, сам обратно не расширяется — только её рукой (`flip_epoch`)."""
    return "owner" if a == "owner" and b == "owner" else "other"


# ------------------------------------------------------------------------- утварь


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def _text_of(content) -> str:
    """Текст из строки или списка блоков. Не-текстовые блоки называются, а не исчезают:
    «не загружено» не имеет права превращаться в «не существует» (§3)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(f"[вложение: {block.get('type') or 'unknown'}]")
        return "\n".join(parts)
    return str(content or "")


def _minutes_utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _clock_line(now: datetime) -> str:
    try:
        offset = int(os.getenv("PRAXIS_TZ_OFFSET_H", "4"))
    except ValueError:
        offset = 4
    local = now.astimezone(timezone.utc) + timedelta(hours=offset)
    return f"{_minutes_utc(now)} · {local.strftime('%H:%M')} по Самаре"


def _first_diff(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit if len(a) != len(b) else -1


def _lcp_bytes(a: bytes, b: bytes) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def _write_atomic(path: Path, text: str) -> None:
    """tmp + replace: рваный файл эпохи откатывал счётчик в 1 и молча перезамораживал
    (находка durable-панели 18.08). newline="\\n" — виндовая мина \\r\\n."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _shrink_rows(text: str, limit: int, pointer: str) -> str:
    """Ужать ГОТОВЫЙ блок-список до limit знаков: записи («- …») снимаются с хвоста,
    заголовок и провенанс остаются на месте, а на месте снятого встаёт явный указатель
    с числом и способом поднять. Потолок меряет блок целиком — обвязка внутри бюджета.

    Молчаливого удаления не бывает ни в одном исходе: даже когда не осталось ни одной
    записи, строка «… ещё N» стоит и называет число. Подпись — машинная (её №6)."""
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    rows = [i for i, line in enumerate(lines) if line.startswith("- ")]
    if not rows:
        return text
    head, tail = lines[:rows[0]], lines[rows[-1] + 1:]
    for keep in range(len(rows) - 1, -1, -1):
        note = (f"… ещё {len(rows) - keep} свёрнуто кодом (потолок блока {limit} зн.; "
                f"это НЕ мой отбор): {pointer}")
        out = "\n".join(head + [lines[i] for i in rows[:keep]] + [note] + tail)
        if len(out) <= limit or keep == 0:
            return out
    return text


def _shrink_book(text: str, limit: int, here: str) -> str:
    """Деградация адресной книги — ЕЁ порядок 21.08 (№4): «сначала неактивные места
    и людей, сохраняя текущего адресата, активные нити и метки границ переноса».

    Жертвы — строки с самой старой последней активностью («—» старше любого времени);
    строка текущего места не снимается никогда; выжившие строки едут ЦЕЛИКОМ — вместе
    с режимом и метками переноса, потому что срезать хвост строки значит срезать как
    раз границу переноса. При равной активности порядок решает байтовое сравнение
    строк: одинаковые входы обязаны давать одинаковые эпохи. Снятое названо числом."""
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    rows = [i for i, line in enumerate(lines) if line.startswith("- ")]
    if not rows:
        return text
    head, tail = lines[:rows[0]], lines[rows[-1] + 1:]
    protected = {
        i for i in rows
        if here and re.search(rf"(?:личка|группа) {re.escape(here)}(?:\s|·|$)",
                              lines[i])
    }

    def activity(i: int) -> str:
        m = re.search(r"последнее: (.+?) · перенос:", lines[i])
        stamp = (m.group(1) if m else "—").strip()
        return "" if stamp == "—" else stamp

    victims = sorted((i for i in rows if i not in protected),
                     key=lambda i: (activity(i), lines[i]))
    kept, removed = set(rows), 0

    def assemble() -> str:
        note = ([f"… ещё {removed} мест снято кодом (потолок блока {limit} зн.; "
                 "неактивные сначала — её №4; это НЕ мой отбор): search_chats"]
                if removed else [])
        return "\n".join(head + [lines[i] for i in rows if i in kept] + note + tail)

    out = assemble()
    for victim in victims:
        if len(out) <= limit:
            break
        kept.discard(victim)
        removed += 1
        out = assemble()
    return out


def _fit_body(assemble, body: str, limit: int) -> tuple[str, str]:
    """Подобрать длину показанного тела так, чтобы ГОТОВЫЙ блок влез в limit знаков.

    Указатель обрезки сам занимает знаки, и его длина зависит от числа внутри него —
    поэтому подбор итеративный. Он детерминирован и сходится: шаг строго уменьшает
    бюджет и не опускается ниже нуля. Возврат — (текст блока, показанное тело)."""
    text = assemble(body, False)
    if len(text) <= limit:
        return text, body
    allow = max(0, limit - (len(text) - len(body)))
    shown = body[:allow]
    for _ in range(8):
        text = assemble(shown, True)
        if len(text) <= limit or allow == 0:
            break
        allow = max(0, allow - (len(text) - limit))
        shown = body[:allow]
    return text, shown


def _strip_hand_lines(text: str) -> str:
    """Деградация рук: описания снимаются, ИМЕНА остаются целиком. Имя руки — это
    способность; снять имя значит соврать про себя, снять описание — только сузить
    подсказку (тела схем и так помечены «не загружены»). Срез назван числом."""
    lines = text.split("\n")
    out, saved, cut = [], 0, 0
    for line in lines:
        if line.startswith("- ") and " — " in line:
            short = line.split(" — ", 1)[0]
            saved += len(line) - len(short)
            cut += 1
            out.append(short)
        else:
            out.append(line)
    if not cut:
        return text
    out.insert(len(out) - 1,
               f"[описания {cut} рук сняты кодом ради потолка эпохи: −{saved} зн.; "
               "имена целиком, тела схем — по требованию; это НЕ мой отбор]")
    return "\n".join(out)


# ------------------------------------------------- идентификатор состояния сборки


@lru_cache(maxsize=1)
def _serializer_sha() -> str:
    """sha256 собственного исходника. Один изменённый байт сборщика — другой кадр,
    и идентификатор обязан это показать раньше, чем покажет разговор."""
    try:
        return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()[:12]
    except OSError:
        return "?"


@lru_cache(maxsize=1)
def _git_sha_of_tree() -> str:
    """Раз за процесс: git на каждый захват — это не прибор, а налог."""
    try:
        done = subprocess.run(["git", "-C", str(BASE), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        return done.stdout.strip() or "?"
    except Exception:
        log.debug("frame_shadow: HEAD не прочитался", exc_info=True)
        return "?"


def _git_sha() -> str:
    """Короткий HEAD дерева. Тот же вызов, что у bootguard.head_sha, но СВОЙ: импорт
    bootguard вешает на корневой логгер свой файловый обработчик, а раннеру он не нужен —
    прибор не имеет права менять чужое логирование ради одной строки.

    В контейнере это работает: docker-compose монтирует `./:/app` целиком, вместе с
    `.git` (`.dockerignore` режет только COPY-слой образа), а Dockerfile заранее делает
    `git config --global --add safe.directory /app`. Дерева git рядом может и не быть —
    тогда честное «?», а не выдуманный номер.

    PRAXIS_HEAD_SHA читается КАЖДЫЙ раз и мимо кэша: перекрытие, которое кэшируется,
    ведёт себя по-разному первым и вторым вызовом — это уже не перекрытие, а лотерея."""
    return (os.getenv("PRAXIS_HEAD_SHA") or "").strip() or _git_sha_of_tree()


def _tools_digest(tools) -> str:
    """Состав И ПОРЯДОК рук ЦЕЛИКОМ (схемы, а не имена). Порядок здесь не косметика:
    у провайдера схемы рук стоят в кэшируемом префиксе, и молчаливый реордер повторил
    бы «тающий лимит» через руки.

    Это НЕ то же, что `tool_offerings.fingerprint`: тот хэширует ИМЕНА в порядке выдачи
    и служит границей эпохи (правка описания не переворачивает эпоху), а этот хэширует
    схемы целиком и служит идентификатором состояния сборки. Два разных вопроса — два
    разных дайджеста; смешать их значило бы либо переворачивать эпоху на правке текста,
    либо не замечать её в идентификаторе."""
    rows = []
    for tool in list(tools or ()):
        rows.append(json.dumps(tool, ensure_ascii=False, sort_keys=True, default=str)
                    if isinstance(tool, dict) else str(tool))
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:12]


def identity(*, tools=(), e_text: str = "", epoch_n: int = 0) -> dict:
    """Короткий идентификатор состояния сборки — шесть полей, ноль церемонии.

    Он отвечает на один вопрос: «то ли это, на что она сказала да». Её слово пишется
    рядом с этой строкой, и этого достаточно — git-тег, аннотация и подписанный отчёт
    приёмки превратили бы личный проект в авиационную сертификацию.

        git        — HEAD дерева;
        schema     — версия схемы кадра (FRAME_SCHEMA);
        serializer — sha256 исходника сборщика;
        tools      — дайджест состава и порядка рук;
        epoch      — номер эпохи и sha256 её замороженного текста;
        golden     — состояние обязательства «живой путь == тень».
    """
    frozen = hashlib.sha256((e_text or "").encode("utf-8")).hexdigest()[:12]
    return {"git": _git_sha(), "schema": FRAME_SCHEMA,
            "serializer": _serializer_sha(), "tools": _tools_digest(tools),
            "epoch": f"{int(epoch_n or 0)}:{frozen}", "golden": GOLDEN}


def identity_line(ident: dict) -> str:
    """Одна строка человеку: рядом с ней и записывается её «да»."""
    return " · ".join(f"{k}={ident.get(k)}" for k in
                      ("git", "schema", "serializer", "tools", "epoch", "golden"))


# ------------------------------------------------------------------------ зона K


def _zone_k() -> str:
    soul = _read(BASE / "soul" / "SOUL.md")
    voice = _read(BASE / "soul" / "VOICE.md")
    parts = [soul]
    if voice:
        parts.append("\n\n---\n" + voice)
    return "".join(parts)


# ------------------------------------------------------------------ блоки эпохи v3


def _block_self() -> str:
    current_self = ""
    try:
        current_self = str(self_model.current_prompt(BASE) or "").strip()
    except Exception:
        log.exception("frame_shadow: current_prompt отказал; эпоха без «кто я сейчас»")
    if not current_self:
        current_self = "(кто я сейчас: источник не прочитался — это отказ прибора, не пустота меня)"
    return (current_self
            + "\n[из: soul/self/CURRENT.md · отбор: её · зачем: ядро ориентации эпохи]")


_HAND_CLAUSE = re.compile(r"[.:;(]|\s—\s|\s-\s")


def _hand_clause(line: str) -> str:
    """Головная фраза однострочника — то, что руку НАЗЫВАЕТ, до перечисления
    подрежимов, оговорок и примеров вызова. Указатель отвечает на «что я умею
    здесь», а не «как это позвать»: тела схем в нём и так помечены «не загружены».

    У тире тут второе дно: строка склеивается из имени и описания через « — », и
    описание, которое само содержит « — », читается как ещё одна рука."""
    m = _HAND_CLAUSE.search(line)
    return (line[:m.start()] if m else line).strip()


def _hand_trim(text: str, limit: int) -> str:
    """Обрез по границе слова: обрубок посреди слова — это байты, а не смысл, а её
    условие ровно об этом («укладывание в потолок само по себе не успех»).

    По счётчику режем В ЕДИНСТВЕННОМ случае — когда в окне нет ни одного пробела,
    то есть слово одно и оно длиннее порога. Порог от этого не страдает: он ищется
    max-min ПОСЛЕ обреза, и чем короче выходят строки, тем выше он поднимается."""
    if len(text) <= limit:
        return text
    space = text.rfind(" ", 0, limit)
    cut = text[:space] if space > 0 else text[:limit]
    return cut.rstrip(" ,;:—-") + "…"


def _block_hands(tools, meta: dict | None = None) -> str:
    """Указатель рук под её потолок ≤HANDS_MAX (слово 18.08, п.1).

    Ступени применяются ТОЛЬКО по нужде и каждая названа в самом блоке числом:
    полный однострочник → головная фраза → общий порог по границе слова → одни
    имена. Имя не сокращается ни на одной ступени: имя руки это способность, и
    спрятать имя значит соврать о себе (корень 13.08 — обрезанный список имён есть
    неявный белый список). Набор рук не трогается вовсе — его состав её.

    Порог общей ступени ищется как max-min: короткие фразы доживают целыми, платят
    длинные. Выбранный порог перепроверяется рендером, поэтому поиск честен даже
    там, где длина строки-указателя не строго монотонна по порогу."""
    pairs: list[list[str]] = []
    for tool in list(tools or ()):
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            # Hosted-поиск в OpenAI-форме приезжает БЕЗ имени (только `type`), и
            # молчаливый `continue` выкидывал из блока ровно ту руку, которая
            # появляется и исчезает переключением мозга. Называем типом в скобках —
            # тем же начертанием, что в отпечатке набора.
            #
            # ⚠ 27.08 Я ХОТЕЛ ЗДЕСЬ БРАТЬ ИМЯ ИЗ РЕЕСТРА СПОСОБНОСТЕЙ — и это было
            # неверно. Довод был «рука одна, а кадр зовёт её двумя именами, значит
            # врёт». Но в `tool_offerings.offered_names` стоит её слово: слить две
            # формы одной способности в одно имя значило бы соврать, У НИХ РАЗНЫЕ
            # БАЙТЫ. Формы правда разные — по-разному едут провайдеру и по-разному
            # кэшируются, и отпечаток различает их НАМЕРЕННО.
            #
            # Переименование в отображении, не тронув отпечаток, развело бы два
            # прибора об одном факте: кадр говорил бы «рука та же», отпечаток —
            # «набор сменился», и эпоха переворачивалась бы без видимой в кадре
            # причины. Поймали два её ревьюера. Смена мозга — НАСТОЯЩАЯ смена
            # набора, и переворот эпохи здесь не паразит, а работа прибора.
            hosted = str(tool.get("type") or "").strip()
            if not hosted:
                continue
            name = f"[{hosted}]"
        first = str(tool.get("description") or "").strip().splitlines()
        line = (first[0] if first else "").strip()
        if len(line) > POINTER_HAND_LINE:
            line = line[:POINTER_HAND_LINE - 1] + "…"
        pairs.append([name, line])

    def assemble(rows: list[list[str]], note: str = "") -> str:
        body = ("руки (тела схем: не загружены — по требованию):\n"
                + "\n".join(f"- {n}" + (f" — {d}" if d else "") for n, d in rows)
                if rows else "руки: в этом ходе не предложены")
        return (body + (f"\n{note}" if note else "")
                + "\n[из: реестр рук по способностям канала · зачем: что я умею здесь]")

    def render(rows: list[list[str]], stage: str, trim_at: int) -> str:
        if stage == "полный":
            return assemble(rows)
        cut = sum(1 for (_, was), (_, now) in zip(pairs, rows) if was != now)
        saved = sum(len(d) for _, d in pairs) - sum(len(d) for _, d in rows)
        how = ("сняты кодом целиком: даже одни имена не влезают в камерный набор"
               if stage == "имена" else
               f"ужаты кодом до головной фразы и ≤{trim_at} зн. ради камерного набора"
               if trim_at else
               "ужаты кодом до головной фразы ради камерного набора")
        return assemble(rows, f"[описания {cut} рук {how} ≤{HANDS_MAX} зн.: "
                              f"−{saved} зн.; имена целиком, тела схем — по "
                              f"требованию; это НЕ мой отбор]")

    rows, stage, trim_at = pairs, "полный", 0
    text = render(rows, stage, trim_at)
    if len(text) > HANDS_MAX:
        rows, stage = [[n, _hand_clause(d)] for n, d in pairs], "фразы"
        text = render(rows, stage, trim_at)
    if len(text) > HANDS_MAX:
        short, lo, hi = rows, 0, max((len(d) for _, d in rows), default=0)
        while lo <= hi:
            mid = (lo + hi) // 2
            trial = [[n, _hand_trim(d, mid)] for n, d in short]
            if len(render(trial, "порог", mid)) <= HANDS_MAX:
                trim_at, lo = mid, mid + 1
            else:
                hi = mid - 1
        rows = [[n, _hand_trim(d, trim_at)] for n, d in short]
        stage, text = "порог", render(rows, "порог", trim_at)
    if len(text) > HANDS_MAX:
        rows, stage, trim_at = [[n, ""] for n, _ in pairs], "имена", 0
        text = render(rows, stage, trim_at)
        if len(text) > HANDS_MAX:
            # Имена не сокращаются даже здесь: превышение видно числом и кричит в
            # лог — та же развязка, что у потолка E, и по той же причине.
            log.error("frame_shadow: указатель рук %d зн. выше камерного набора %d "
                      "даже одними именами (%d рук) — оставляю имена целиком",
                      len(text), HANDS_MAX, len(rows))
    if meta is not None:
        meta.update({
            "tools": len(pairs), "stage": stage, "chars": len(text),
            "cap": HANDS_MAX, "trim_at": trim_at,
            # Её условие: мерить полезные байты и раздувание ОТДЕЛЬНО. Имена —
            # несжимаемое ядро указателя, описания — то, что он тратит на подсказку,
            # `full_chars` — чем блок был бы без потолка.
            "names_chars": sum(len(n) + 2 for n, _ in pairs),
            "desc_chars": sum(len(d) + 3 for _, d in rows if d),
            "full_chars": len(render(pairs, "полный", 0)),
            "over": max(0, len(text) - HANDS_MAX),
        })
    return text


def _block_memory_index(audience: str = "other", ctx=None) -> str:
    """В owner-потоке — ПОЛНОЕ каноническое тело `memory/INDEX.md` (её слово 21.08,
    №8: «полное каноническое тело в E, не альтернативный сгенерированный список
    имён»). Синтетический указатель молча отменял бы её с Егором решение 10.08 класть
    карту в кадр целиком. Отсутствие файла — названный отказ источника, НЕ повод
    вернуть генерацию.

    Вне owner-потока полнота бьётся о другое. Тело INDEX.md несёт ИМЕНА живых людей,
    и в чужой личке оно рассказывает собеседнику, кого я знаю. Тогда имён нет
    вовсе — кроме его собственного, если привязка однозначна, — и вместо них не число,
    а названная граница: «ещё 45» тоже сведение о третьих людях, только арифметикой.
    Навыки остаются: это мои способности, а не чужие данные."""
    if audience == "owner":
        body = _read(BASE / "memory" / "INDEX.md").rstrip()
        if not body:
            body = ("(карта памяти: memory/INDEX.md не прочитался — это отказ "
                    "источника, не пустота памяти; синтетический список вместо "
                    "канона не собирается — её №8)")
        return (body
                + "\n[из: memory/INDEX.md — полное каноническое тело (её №8) · "
                  "тела: не загружены — поднимаются рукой; «не загружено» не значит "
                  "«не существует»]")
    people_dir = BASE / "memory" / "people"
    names = sorted(p.stem for p in people_dir.glob("*.md")
                   if not p.stem.startswith("_")) if people_dir.exists() else []
    skills_dir = BASE / "soul" / "skills"
    skills = sorted(p.stem for p in skills_dir.glob("*.md")
                    if p.stem != "INDEX") if skills_dir.exists() else []
    own = _dossier_for(str(getattr(ctx, "chat_id", "") or ""))
    mine = own.stem if own is not None and own.stem in names else ""
    lines = ["указатель памяти (тела: не загружены — поднимаются рукой; перечень людей "
             "сужен по аудитории потока — отбор: код, НЕ мой):"]
    lines.append("люди: " + (f"{mine} · " if mine else "")
                 + "остальные имена не приводятся вне owner-потока — ни списком, "
                   "ни числом")
    if skills:
        lines.append("навыки: " + " · ".join(skills))
    lines.append("[из: memory/people/ + soul/skills/ · зачем: карта того, что у меня "
                 "есть; «не загружено» не значит «не существует», а «не приведено» "
                 "не значит «не помню»]")
    return "\n".join(lines)


def _block_desires() -> tuple[str, bool]:
    """Желания в эпохе — ЕЁ слово 21.08 (№8): «желания — E/desires; границу эпохи
    вызывать по содержательному изменению ledger, не по служебному шуму».

    Источник — её же append-only леджер (desires.DesireLedger), тот самый, из
    которого живой путь собирает тир «Canonical desire continuity»: два разных
    рендера одного канона, не две правды. Служебный шум (пересборка проекции,
    timestamps, refs) в рендер не входит — поэтому «блок изменился» и есть
    «содержательное изменение», и граница эпохи меряется ровно этим текстом.

    Возврат (текст, ok): при отказе источника ok=False — граница по отказу НЕ
    объявляется (молчание прибора не факт о желаниях), а текст называет отказ."""
    try:
        states = [
            state for state in desires_ledger.DesireLedger(BASE).list(
                statuses=("active", "latent", "blocked"))
            if memory_provenance.desire_state_normative_eligible(state)
        ]
    except Exception:
        log.exception("frame_shadow: леджер желаний не прочитался")
        return ("мои живые намерения: источник не прочитался — это отказ прибора, "
                "не пустота намерений\n"
                "[из: memory/desires/events.jsonl · её №8: желания живут в эпохе]",
                False)
    shown = states[:10]
    rows = []
    for state in shown:
        row = (f"- {state.get('id')} [{state.get('stage')}/{state.get('status')}]: "
               f"{str(state.get('statement') or '').strip()}")
        if state.get("next_move"):
            row += f"; next: {str(state.get('next_move'))[:500]}"
        rows.append(row)
    lines = ["мои живые намерения (снимок на момент заморозки; внутреннее, "
             "не обещание аудитории):"]
    lines.extend(rows if rows else ["- живых намерений в леджере сейчас нет"])
    if len(states) > len(shown):
        lines.append(f"Показано {len(shown)} из {len(states)} — остальные целиком "
                     "через manage_desire(action=get, desire_id=…).")
    lines.append("[из: memory/desires/events.jsonl (append-only канон) · отбор: код, "
                 "НЕ мой · её №8: желания живут в эпохе, граница — содержательное "
                 "изменение леджера]")
    return "\n".join(lines), True


def _block_mail() -> str | None:
    """Локатор почты — ЕЁ слово 21.08 (№8): «почтовый локатор — E». Локатор, не
    индекс: тела писем и список — руками, здесь только «ящик существует и чем его
    открыть» плюс счёт на момент заморозки (снимок, не живое число)."""
    box = BASE / "memory" / "mailbox.json"
    if not box.exists():
        return None
    try:
        data = json.loads(_read(box) or "{}")
        letters = str(len(data)) if isinstance(data, dict) else "0"
    except Exception:
        log.debug("frame_shadow: mailbox.json не разобрался", exc_info=True)
        letters = "не прочитался (отказ прибора, не пустота ящика)"
    return ("почтовый ящик — локатор, не индекс. Писем на момент заморозки: "
            f"{letters}. Индекс не в кадре; письмо — mail_read, список — "
            "manage_mail; индекс приедет сам, если речь зайдёт о почте.\n"
            "[из: memory/mailbox.json · её №8: локатор живёт в эпохе]")


def _block_voice_frame(payload: dict | None) -> str | None:
    """Стабильная голосовая рамка — ЕЁ слово 21.08 (№8): «можно оставить в E как
    явно машинный системный контракт». Стабильность объявлена отдельным аргументом
    payload.voice_frame; из generic frame.extra_system она не угадывается.
    Нет явно переданного текста — нет блока."""
    text = str((payload or {}).get("voice_frame") or "").strip()
    if not text:
        return None
    return (text
            + "\n[из: живой путь, явно стабильный payload.voice_frame · машинный "
              "системный контракт, НЕ мой голос (её №8); текущий extra_system — в T]")


def _room_last_activity(chat_id: str) -> str:
    """Последняя запись архива места — днём и минутой, абсолютно. Дорогое чтение только
    хвоста файла; отсутствие архива — честное «—», не ноль и не ошибка."""
    groups = BASE / "memory" / "groups"
    if not groups.exists():
        return "—"
    for d in groups.iterdir():
        if not d.name.startswith(f"{chat_id}-"):
            continue
        arch = d / "archive.jsonl"
        if not arch.exists():
            return "—"
        try:
            with arch.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read().decode("utf-8", "ignore").strip().splitlines()
            for line in reversed(tail):
                try:
                    ts = str(json.loads(line).get("timestamp") or "")
                except Exception:
                    continue
                if ts:
                    return ts[:16].replace("T", " ") + " UTC"
        except OSError:
            return "—"
    return "—"


def _block_address_book(ctx, audience: str = "other",
                        limit: int = E_BOOK_MAX, *, moving: bool = False) -> str:
    """Карта МЕСТ из durable-источников (профили комнат), без сети. Факты — из кода,
    формулы переноса — её (пока её слова нет — умолчание, помеченное умолчанием).
    Потолок применяется к ГОТОВОМУ блоку: провенанс и заголовок внутри бюджета.

    Аудитория режет книгу ВЫРЕЗАНИЕМ, а не пометкой. Пометка `presence_hidden` жила
    аннотацией на строке — но строка с id уже была сказана, и «существование не
    подтверждается» подтверждало его самим фактом печати. Вне owner-потока в книге
    остаётся ровно одно место — это, где идёт разговор; собеседник и так в нём стоит.
    Числа «ещё N мест» здесь нет нарочно: посчитать скрытые комнаты значит подтвердить
    их существование арифметикой, не посчитать — назвать числом не то, что оно значит.
    Поэтому вместо числа — граница словами."""
    rooms_dir = BASE / "memory" / "rooms"
    rows = []
    here = str(getattr(ctx, "chat_id", "") or "")
    try:
        import rooms as rooms_mod
        parse = rooms_mod.parse_profile
    except Exception:
        parse = None
    if rooms_dir.exists():
        for path in sorted(rooms_dir.glob("*.md")):
            chat_id = path.stem
            if audience != "owner" and chat_id != here:
                continue  # не аннотация — отсутствие
            raw = _read(path)
            title, mode_word, until, header = chat_id, "", "", {}
            if parse is not None:
                try:
                    prof = parse(raw)
                    title = (prof.get("title") or "").lstrip("# ").strip() or chat_id
                    mode_word = str(prof.get("mode") or "")
                    until = str(prof.get("mode_until") or "")
                    header = dict(prof.get("header") or {})
                except Exception:
                    log.debug("frame_shadow: профиль %s не разобрался", chat_id,
                              exc_info=True)
            kind = "личка" if not chat_id.startswith("-") else "группа"
            # ЕЁ слово из реестра переноса читается РАНЬШЕ умолчаний (её №2, 18.08).
            her_word = str(header.get("transfer") or "").strip()
            if her_word:
                at = str(header.get("transfer_at") or "")[:10]
                transfer = f"{her_word} (моё слово" + (f", {at}" if at else "") + ")"
            else:
                transfer = (TRANSFER_DM_DEFAULT if kind == "личка"
                            else TRANSFER_GROUP_DEFAULT)
            hidden = (" · существование не подтверждается вне owner-потоков"
                      if audience == "owner"
                      and str(header.get("presence_hidden") or "") == "yes" else "")
            # ⚠ ДВЕ ПРОЕКЦИИ ОДНОГО ОБХОДА, А НЕ ДВА ОБХОДА (её слово 27.08:
            # «часы, live-счётчики и `перенос: …` — в T. Это текущая обстановка, не
            # слой идентичности/способностей»). Стабильная строка — кто это место:
            # имя, род, id, режим, граница присутствия. Подвижная — когда там в
            # последний раз говорили и по какой формуле оттуда переносится; срок
            # режима туда же, это дата. Два независимых обхода разошлись бы молча,
            # и книга в E называла бы одни места, а обстановка в T — другие.
            mode_part = (f" · режим: {mode_word}"
                         if mode_word and mode_word != "normal" else "")
            if moving:
                last = _room_last_activity(chat_id)
                until_part = (f" · режим до {until}"
                              if until and mode_word and mode_word != "normal" else "")
                rows.append((chat_id, f"- {chat_id} · последнее: {last} · "
                                      f"перенос: {transfer}{until_part}"))
            else:
                rows.append((chat_id,
                             f"- {title} — {kind} {chat_id}{mode_part}{hidden}"))
    rows.sort(key=lambda r: (r[0] != here, r[0]))
    if moving:
        # Живёт в T и пересобирается каждый ход — этим и отличается от книги.
        if not rows:
            return ""
        return "\n".join(
            ["обстановка мест (пересобирается каждый ход; кто эти места — в эпохе):"]
            + [line for _cid, line in rows]
            + ["[из: архивы мест + реестр переноса · зачем: когда там в последний раз "
               "говорили и куда оттуда можно переносить]"])
    lines = (["адресная книга (состояние на момент заморозки; список мест полный, "
              "сведения — снимки со своими датами):"] if audience == "owner" else
             ["адресная книга (вне owner-потока — только это место; остальные места "
              "не приводятся ни именами, ни id, ни числом — отбор: код, НЕ мой):"])
    lines.extend(line for _cid, line in rows)
    if not rows:
        lines.append("- мест в реестре нет" if audience == "owner"
                     else "- это место в реестре комнат ещё не заведено")
    lines.append("[из: memory/rooms/ (профили) + архивы мест · зачем: карта «где я "
                 "живу» и границ переноса; тела разговоров — рукой read_chat]"
                 if audience == "owner" else
                 "[из: memory/rooms/ (профиль этого места) · зачем: границы переноса "
                 "здесь; карта остальных мест живёт только в owner-потоке]")
    return _shrink_rows("\n".join(lines), limit, "search_chats")


def _iter_outbox_sends():
    """(ts_iso, peer_id, message_id) доставленных отправок — из durable-леджера."""
    entries = BASE / "memory" / ".state" / "telegram_outbox" / "entries"
    if not entries.exists():
        return
    for path in entries.glob("*.jsonl"):
        peer, at, msg_id, accepted = "", "", "", False
        try:
            with path.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    data = row.get("data") or {}
                    if row.get("kind") == "intent":
                        peer = str((data.get("delivery") or {}).get("peer_id") or "")
                        at = str(row.get("at") or "")
                    elif row.get("kind") == "accepted":
                        accepted = True
                        msg_id = str((data.get("message_id")
                                      if isinstance(data, dict) else "") or "")
        except OSError:
            continue
        if accepted and peer and at:
            yield at, peer, msg_id


def _block_recent(now: datetime, titles: dict[str, str] | None = None,
                  audience: str = "other", here: str = "",
                  limit: int = E_RECENT_MAX) -> str:
    """Недавнее — структурные записи, не проза (фильтр аудитории по прозе неисполним —
    корень classify). ЕЁ правило отбора (№7): минимум 48 часов + последние 3 события
    каждого активного места за 7 суток. Отбор: код (черновик) — не её голос.

    Структурность не спасает от аудитории: строка «место X: сказала» называет ЧУЖУЮ
    комнату id-ом. Вне owner-потока горизонт сужается до одного места — этого."""
    sends = sorted(_iter_outbox_sends(), reverse=True)
    cutoff_48h = (now - timedelta(hours=RECENT_HOURS)).isoformat()
    cutoff_7d = (now - timedelta(days=RECENT_PLACE_DAYS)).isoformat()
    picked: list[tuple[str, str, str]] = []
    per_place: dict[str, int] = {}
    for at, peer, msg_id in sends:
        if at < cutoff_7d:
            break
        if audience != "owner" and str(peer) != str(here):
            continue  # чужая комната не называется вовсе
        fresh = at >= cutoff_48h
        quota = per_place.get(peer, 0) < RECENT_PER_PLACE
        if fresh or quota:
            picked.append((at, peer, msg_id))
            per_place[peer] = per_place.get(peer, 0) + 1
    picked.sort(reverse=True)
    lines = (["недавнее (на момент заморозки; отбор: код (черновик), НЕ мой; "
              "полные тексты — рукой):"] if audience == "owner" else
             ["недавнее (вне owner-потока — только это место; события других мест не "
              "приводятся; отбор: код (черновик), НЕ мой; полные тексты — рукой):"])
    for at, peer, msg_id in picked:
        label = (titles or {}).get(peer) or peer
        stamp = at[:16].replace("T", " ")
        lines.append(f"- {stamp} UTC · {label}: сказала"
                     + (f" (#{msg_id})" if msg_id else ""))
    if not picked:
        lines.append("- журнал доставки пуст за горизонт" if audience == "owner"
                     else "- журнал доставки по этому месту пуст за горизонт")
    lines.append("выброшено из среза: события старше горизонта и виды, которых журнал "
                 "не ведёт (входящие, чтения) — блок неполон ПО ИСТОЧНИКУ")
    lines.append("[из: durable-леджер отправок · зачем: ориентация «где что "
                 "произошло»; горизонт: 48 ч + 3 на место за 7 суток]")
    return _shrink_rows("\n".join(lines), limit, "журнал доставки")


def _dossier_for(chat_id: str) -> Path | None:
    """Досье по Telegram-id только по строгой структурной привязке.

    Личный DM — не место для эвристики. Упоминание id в свободной прозе, даже в
    единственном legacy-файле, доказывает только то, что файл говорит о человеке;
    оно не доказывает, что файл принадлежит ему. Поднимать такой файл означало бы
    переносить чужое досье в его кадр. Для старого досье без binding честный исход —
    «неоднозначно, не поднимаю» до отдельной аутентифицированной миграции.
    """
    people = BASE / "memory" / "people"
    if not people.exists() or not chat_id:
        return None
    header_hits: list[Path] = []
    target = re.compile(rf"^telegram_id\s*:\s*{re.escape(chat_id)}\s*$",
                        re.M | re.IGNORECASE)
    declaration = re.compile(r"^telegram_id\s*:", re.M | re.IGNORECASE)
    for path in sorted(people.glob("*.md")):
        raw = _read(path)
        # Преамбула и регистр совпадают с authoritative `people.telegram_id()`:
        # тело — пересказ, а не удостоверение личности.
        preamble = re.split(r"^##\s", raw, maxsplit=1, flags=re.M)[0]
        bound = declaration.findall(preamble)
        if len(bound) != 1:
            continue
        if target.search(preamble):
            header_hits.append(path)
    return header_hits[0] if len(header_hits) == 1 else None


# Начало СЛЕДУЮЩЕЙ записи на том же отступе: маркер списка, заголовок, цитата,
# ограда кода, строка таблицы, тематический разрыв. Всё прочее на том же отступе —
# ленивое продолжение предыдущего абзаца, то есть ЧАСТЬ той же записи.
_NEW_BLOCK = re.compile(
    r"(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|>|```|~~~|\||-{3,}\s*$|\*{3,}\s*$|_{3,}\s*$)")
_LIST_MARK = re.compile(r"(?:[-*+]|\d+[.)])\s")


def strip_private_blocks(text: str) -> tuple[str, int]:
    """Снять записи с пометкой `[private]` ЦЕЛИКОМ — вместе с продолжениями.

    26.08, остаток после первого repair: блок снимался, но обрывался на первой же
    строке ТОГО ЖЕ отступа. В markdown соседняя непустая строка без своего маркера —
    ленивое продолжение того же абзаца; приватная заметка, написанная обычным
    абзацем (а не пунктом списка), уезжала собеседнику со второй строки.

    Запись кончается там, где начинается СЛЕДУЮЩАЯ: дедент, новый маркер, заголовок,
    цитата, ограда — а для абзаца ещё и пустая строка.

    ⚠ ⚠ С c82f38c8 ленивое продолжение снимается И У ПУНКТА СПИСКА тоже:
    условие ниже больше не смотрит на `listish`. Прежний текст этого абзаца
    описывал СНЯТОЕ поведение и противоречил коду сутки: он говорил, что
    строка вплотную под `- [private] …` БЕЗ отступа остаётся видимой и что это
    «названная граница». Сегодня граница другая. Цена этого выбора названа там же:
    открытый текст, приклеенный к приватному пункту, теперь исчезает. Её решение
    обратимо в одну строку — но только В ОБОИХ фильтрах разом, иначе они разойдутся
    снова; сторож — `test_private_filters_agree.py`.

    Возвращает тело и число снятых СТРОК (оно же `private_hidden`).
    """
    kept: list[str] = []
    hidden = 0
    heading_level: int | None = None
    indent: int | None = None
    listish = False
    blank_seen = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        col = len(line) - len(stripped)
        head = re.match(r"^(#{1,6})\s", stripped)
        level = len(head.group(1)) if head else None
        if heading_level is not None:
            if level is not None and level <= heading_level:
                heading_level = None
            else:
                hidden += 1
                continue
        if indent is not None:
            if not stripped.strip():
                if listish:
                    hidden += 1
                    blank_seen = True
                    continue
                indent = None            # абзац кончился пустой строкой
            elif col > indent:
                hidden += 1
                blank_seen = False
                continue
            elif (col == indent and not blank_seen and not _NEW_BLOCK.match(stripped)):
                hidden += 1              # ленивое продолжение той же записи
                continue
            else:
                indent = None
        if "[private]" in line:
            hidden += 1
            if level is not None:
                heading_level = level
            else:
                indent = col
                listish = bool(_LIST_MARK.match(stripped))
                blank_seen = False
            continue
        kept.append(line)
    return "".join(kept), hidden


def _lifted_source(ctx, audience: str = "other") -> tuple[str, dict | None]:
    """«Поднятое» с ПЕРВЫМ настоящим телом — досье собеседника потока.

    ЕЁ условия №13, дословно исполненные: тело едет ПОБАЙТОВО (никакой молчаливой
    нормализации CRLF/Unicode — decode c errors=replace единственное признанное
    искажение, и оно видно по sha); provenance — обвязкой РЯДОМ, не вставками в текст;
    hash и размер видны; обрезка — только явный указатель с размером и способом
    поднять; служебные ограждения структурно не маскируются под её документ.
    Подъём делает код на границе — подпись «поднято кодом (черновик)», не её голос
    (её №6). Тела едут только в dm-потоках и только досье СОБЕСЕДНИКА этого потока:
    чужого человека сюда не поднять по построению — привязка идёт от chat_id правилом
    свидетелей.

    Аудитория здесь режет не файл, а приватные markdown-записи целиком через
    `strip_private_blocks`: вместе с принадлежащими записи продолжениями. Байтовость
    её №13 остаётся там, где она её и ставила, — в owner-потоке; вне его тело
    подписано как отфильтрованное, а sha256 по-прежнему считан ПО ФАЙЛУ ЦЕЛИКОМ,
    чтобы обрезка не выдавала себя за оригинал.

    Здесь только ЧТЕНИЕ — ровно одно на заморозку. Вёрстка отдана `_render_lifted`,
    чтобы лестница деградации пересобирала блок под меньший потолок из уже
    прочитанных байт: два чтения одного файла в одной заморозке разошлись бы по sha.
    Снятие `[private]` происходит ЗДЕСЬ, до вёрстки: лестница обязана резать уже
    отфильтрованное тело, иначе на полу 2000 знаков в кадр уехало бы то, что аудитория
    сняла.
    """
    header = ("поднято в эпоху (тела ниже — байты файлов в служебных ограждениях; "
              "результаты рук живут в накопителе; всё остальное в кадре — указатели):")
    foot = "[зачем: реестр моего внимания; снятие — на границе, вслух]"
    chat_id = str(getattr(ctx, "chat_id", "") or "")
    if not (getattr(ctx, "is_dm", False) and chat_id and not chat_id.startswith("-")):
        return (header + "\nничего: у потока нет собеседника-человека\n" + foot), None
    path = _dossier_for(chat_id)
    if path is None:
        return (header + f"\nдосье собеседника: привязка id {chat_id} → файл не "
                "однозначна — не поднимаю, чтобы не угадывать (правило свидетелей)\n"
                + foot), None
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    body = raw.decode("utf-8", errors="replace")
    private_hidden = 0
    if audience != "owner":
        body, private_hidden = strip_private_blocks(body)
    try:
        import rooms as rooms_mod
        her = rooms_mod.transfer_of(chat_id)
        transfer = (f"{her['transfer']} (моё слово)" if her["transfer"]
                    else TRANSFER_DM_DEFAULT)
    except Exception:
        transfer = TRANSFER_DM_DEFAULT
    return header, {"path": path, "raw": raw, "sha": sha, "foot": foot,
                    "body": body, "transfer": transfer,
                    "private_hidden": private_hidden}


def _render_lifted(header: str, src: dict | None,
                   limit: int = E_LIFT_MAX) -> tuple[str, list[dict]]:
    """Свёрстанное «поднятое» под потолок ГОТОВОГО блока: ограждения, provenance и
    указатель обрезки считаются внутри limit. Обрезка — только явным указателем
    с числом знаков, числом байт и способом поднять целиком; снятие приватных строк —
    отдельной строкой с числом, потому что молчаливая фильтрация есть ровно та болезнь,
    от которой блок и подписан хэшем."""
    if src is None:
        return header, []
    path, raw, sha, body = src["path"], src["raw"], src["sha"], src["body"]
    private_hidden = int(src.get("private_hidden") or 0)

    def assemble(shown: str, cut: bool) -> str:
        # ЕЁ слово 21.08 (№8): «вся будущая обрезка только явно, с исходным размером,
        # sha и способом поднять полное тело».
        cut_note = ((f"\n[обрезано: показано {len(shown)} из {len(body)} знаков "
                     f"({len(raw)} байт) · sha256 полного тела {sha[:12]} · "
                     f"целиком: memory/people/{path.name} — поднять рукой чтения "
                     f"файла]")
                    if cut else "")
        if private_hidden:
            cut_note += (f"\n[вне owner-потока снято строк с пометкой [private]: "
                         f"{private_hidden} — тело показано не байт-в-байт; sha256 выше "
                         f"считан по файлу целиком]")
        return (header + "\n"
                f"↓ [досье собеседника · {path.name} · {len(raw)} байт · "
                f"sha256 {sha[:12]} · перенос: {src['transfer']}]\n"
                "   поднято кодом (черновик): собеседник этого потока · на границе эпохи\n"
                f"——— тело {path.name}, байты как в файле ———\n"
                f"{shown}{cut_note}\n"
                f"——— конец тела {path.name} · sha256 {sha[:12]} ———\n"
                + src["foot"])

    block, shown = _fit_body(assemble, body, limit)
    markup = {"bold": shown.count("**") // 2,
              "list_lines": sum(1 for l in shown.splitlines()
                                if l.lstrip().startswith(("- ", "* ")))}
    meta = [{"name": path.name, "bytes": len(raw), "sha8": sha[:8],
             "shown_chars": len(shown), "total_chars": len(body),
             "private_hidden": private_hidden, "markup": markup}]
    return block, meta


def _e_total(blocks: dict[str, str]) -> int:
    """Размер E по ГОТОВЫМ блокам — ровно те величины, что уедут в снапшот."""
    return sum(len(str(v)) for v in blocks.values())


def _apply_e_ceiling(blocks: dict[str, str], *, rebuild) -> list[dict]:
    """Лестница деградации E: фиксированный порядок, каждая ступень — с явным
    указателем и числом внутри блока и поимённой записью в метрике.

    Порядок (E_DEGRADE_ORDER): поднятое → недавнее → адресная книга → описания рук.
    `self` и `memory_index` не трогаются никогда — её слово.

    Лестница не имеет права отказать в заморозке. Если после всех ступеней E всё ещё
    выше E_TOTAL_MAX — а так бывает: указатель памяти без капа нарочно и в пределе
    перерастает потолок один, — блоки замораживаются КАК ЕСТЬ, превышение кричит
    log.error и едет в метрику `e_overflow.over`. Ни в одном исходе нет ни
    молчаливого удаления, ни отказа заморозить: эпоха без заморозки — это откат
    номера и потеря байтового префикса, цена выше перерасхода."""
    degraded: list[dict] = []
    total = _e_total(blocks)
    if total <= E_TOTAL_MAX:
        return degraded
    for name in E_DEGRADE_ORDER:
        if total <= E_TOTAL_MAX:
            break
        before = blocks.get(name)
        if before is None:
            continue
        target = max(_E_FLOOR.get(name, 0), len(before) - (total - E_TOTAL_MAX))
        after = rebuild(name, target)
        if after is None or len(after) >= len(before):
            continue
        blocks[name] = after
        degraded.append({"block": name, "было": len(before), "стало": len(after)})
        total = _e_total(blocks)
    if total > E_TOTAL_MAX:
        log.error("frame_shadow: E=%d знаков выше потолка %d даже после лестницы %s "
                  "— морожу как есть, превышение видно в метрике e_overflow; "
                  "блоки: %s", total, E_TOTAL_MAX, [d["block"] for d in degraded],
                  {k: len(v) for k, v in blocks.items()})
    return degraded


def _epoch_blocks(ctx, tools, now: datetime,
                  audience: str = "other",
                  payload: dict | None = None) -> tuple[dict[str, str], list[dict],
                                                        list[dict]]:
    """Блоки E v4 в порядке частоты изменения (E_ORDER), опись поднятых тел и опись
    деградаций потолка. Шапка добавляется при заморозке — она знает номер и причину.

    Состав — ЕЁ реестр 21.08 (№8): в owner-потоке «недавнее» НЕ собирается (вариант
    «в»: недавние действия доступны рукой recent_turns, а не постоянным блоком),
    зато живут желания, локатор почты и полное тело INDEX.md. Вне owner-потока
    состав прежний узкий (её слово: групповую половину реестра принимать только
    после реальных замеров тени в группах). Голосовая рамка едет из payload живого
    пути — тень машинный контракт не сочиняет.

    Аудитория едет ОДНИМ аргументом через все блоки: разные её толкования в разных
    блоках — это и есть та дыра, которой книга уезжала в чужой поток. Умолчание
    закрывающее: забытый проброс даёт УЗКИЙ кадр, а не широкий.

    Потолок E применяется ЗДЕСЬ, а не в _freeze_epoch: drift обязан мерить
    замороженное против того, что собралось бы сейчас ПО ТЕМ ЖЕ правилам. Иначе
    сработавшая лестница показывала бы вечный drift в блоках, которые не менялись."""
    here = str(getattr(ctx, "chat_id", "") or "")
    book = _block_address_book(ctx, audience)
    lift_header, lift_src = _lifted_source(ctx, audience)
    lifted_block, lifted_meta = _render_lifted(lift_header, lift_src)
    hands_meta: dict = {}
    blocks = {
        "self": _block_self(),
        "hands": _block_hands(tools, hands_meta),
        "memory_index": _block_memory_index(audience, ctx),
        "address_book": book,
        "lifted": lifted_block,
    }
    voice = _block_voice_frame(payload)
    if voice is not None:
        blocks["voice_frame"] = voice
    if audience == "owner":
        mail = _block_mail()
        if mail is not None:
            blocks["mail"] = mail
        blocks["desires"] = _block_desires()[0]
    else:
        titles: dict[str, str] = {}
        for line in book.splitlines():
            m = re.match(r"- (.+?) — (?:личка|группа) (-?\d+)", line)
            if m:
                titles[m.group(2)] = m.group(1)
        blocks["recent"] = _block_recent(now, titles, audience, here)

    def rebuild(name: str, target: int) -> str | None:
        """Пересборка одного блока под меньший потолок БЕЗ повторного чтения диска:
        списки ужимаются из уже собранного текста, тело — из уже прочитанных байт."""
        if name == "lifted":
            text, meta = _render_lifted(lift_header, lift_src, target)
            lifted_meta[:] = meta
            return text
        if name == "recent":
            return _shrink_rows(blocks["recent"], target, "журнал доставки")
        if name == "address_book":
            return _shrink_book(blocks["address_book"], target, here)
        if name == "hands":
            return _strip_hand_lines(blocks["hands"])
        return None

    degraded = _apply_e_ceiling(blocks, rebuild=rebuild)
    # Лестница E имеет право пройтись по рукам ПОСЛЕ камерного потолка. Тогда мера
    # обязана говорить про замороженные байты, а не про те, что были до лестницы:
    # иначе метрика указателя разойдётся с `e_blocks` того же захвата и обе станут
    # непроверяемыми.
    if hands_meta and hands_meta.get("chars") != len(blocks["hands"]):
        hands_meta.update({"chars": len(blocks["hands"]), "stage": "лестница E",
                           "over": max(0, len(blocks["hands"]) - HANDS_MAX)})
    return blocks, lifted_meta, degraded, hands_meta


def _epoch_text(blocks: dict[str, str], header_line: str) -> str:
    return "\n\n".join([blocks[k] for k in E_ORDER if k in blocks] + [header_line])


def _zone_e_current(tools, ctx=None, now: datetime | None = None,
                    audience: str = "other", payload: dict | None = None) -> str:
    """Э, какой она собралась бы СЕЙЧАС (без шапки — та рождается на границе).
    В кадр едет замороженный снапшот; расхождение — метрика drift по блокам."""
    now = now or datetime.now(timezone.utc)
    blocks, _meta, _degraded, _hands = _epoch_blocks(ctx, tools, now, audience,
                                                     payload)
    return "\n\n".join(blocks[k] for k in E_ORDER if k in blocks)


# --------------------------------------------------------------- эпоха: заморозка


def _epoch_path(stream_dir: Path) -> Path:
    return stream_dir / "epoch.json"


def _max_epoch_in_metrics(stream_dir: Path) -> int:
    """Восстановление номера из журнала метрик: рваный epoch.json больше не имеет
    права молча начать эпохи с единицы (номер — для сверки, он монотонен)."""
    best = 0
    path = stream_dir / "metrics.jsonl"
    if not path.exists():
        return best
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    best = max(best, int(json.loads(line).get("epoch") or 0))
                except Exception:
                    continue
    except OSError:
        pass
    return best


def _freeze_epoch(stream_dir: Path, ctx, tools, now: datetime, n: int,
                  reason: str, fold_count: int, fold_line: str,
                  fold_anchor: dict | None = None,
                  audience: str = "other",
                  payload: dict | None = None) -> dict:
    blocks, lifted_meta, degraded, hands_meta = _epoch_blocks(
        ctx, tools, now, audience, payload)
    # Сводка прошлых ходов — ЕЁ реестр №8: «сводка — A». Снимается на ГРАНИЦЕ из
    # живого пути (тень своих компактов не варит — этап 2) и стоит в голове A
    # байт-в-байт до следующей границы: вставка, меняющаяся между границами,
    # ломала бы префикс без права границы.
    recap_text = str((payload or {}).get("recap") or "")
    recap = ({"text": recap_text, "at": _minutes_utc(now)} if recap_text else None)
    header = (f"[эпоха {n} · заморожена {_minutes_utc(now)} · "
              f"поток: {stream_dir.name} · граница: {reason}]")
    saved = {
        "v": FRAME_SCHEMA, "n": n, "frozen_at": _minutes_utc(now), "reason": reason,
        # Аудитория стоит РЯДОМ с reason, до blocks: снапшот отвечает не только
        # «когда и почему заморожен», но и «для кого собран».
        "audience": audience,
        "blocks": blocks, "header": header,
        "e_text": _epoch_text(blocks, header),
        "lifted_meta": lifted_meta,
        # Мера указателя рук ЭТОЙ эпохи. Её условие 18.08: «отдельно измерять
        # полезные байты и раздувание каждого блока — укладывание в потолок само по
        # себе не успех». Живёт в снапшоте, а не считается на лету, потому что
        # метрика обязана описывать замороженное, а не то, что собралось бы сейчас.
        "hands_meta": hands_meta,
        "e_degraded": degraded,
        # Отпечаток набора рук ЭТОЙ эпохи: имена в порядке выдачи. Порядок в хэше
        # не роскошь — у Anthropic cache_control breakpoint висит на ПОСЛЕДНЕМ туле
        # (её слово 18.08: `end_turn` последний), а у OpenAI-совместимых схемы едут
        # ВЫШЕ system, и любой реордер стоит столько же, сколько смена состава.
        "tools_sha": tool_offerings.fingerprint(tools),
        # fold_count остаётся снимком «сколько было на границе» — для сверки и для
        # чтения старыми глазами; позиционно его больше никто не применяет.
        "fold_count": int(fold_count), "fold_line": str(fold_line or ""),
        "fold_anchor": fold_anchor,
        "recap": recap,
    }
    stream_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(_epoch_path(stream_dir),
                  json.dumps(saved, ensure_ascii=False, indent=1))
    return saved


def _load_epoch(stream_dir: Path) -> dict | None:
    path = _epoch_path(stream_dir)
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("frame_shadow: epoch.json нечитаем — аварийная перезаморозка "
                      "с восстановлением номера из метрик")
        return None
    return saved if isinstance(saved, dict) else None


def _epoch_drift(saved: dict, ctx, tools, now: datetime,
                 audience: str = "other", payload: dict | None = None) -> dict | None:
    """Drift по блокам: какой именно блок устарел и на сколько знаков.

    Аудитория берётся ТА ЖЕ, под которой эпоха заморожена: иначе drift мерил бы не
    старение источника, а смену собеседника — и звал бы к перезаморозке шире потока."""
    frozen_blocks = saved.get("blocks")
    if not isinstance(frozen_blocks, dict):
        frozen = str(saved.get("e_text") or "")
        current = _zone_e_current(tools, ctx, now, audience, payload)
        if frozen == current:
            return None
        return {"at": _first_diff(frozen, current),
                "frozen_chars": len(frozen), "current_chars": len(current)}
    current_blocks, _m, _d, _h = _epoch_blocks(ctx, tools, now, audience, payload)
    changed = {}
    for name, cur in current_blocks.items():
        old = str(frozen_blocks.get(name) or "")
        if old != cur:
            changed[name] = {"frozen_chars": len(old), "current_chars": len(cur)}
    for name, old in frozen_blocks.items():
        # Замороженный блок, который сегодня не собрался бы вовсе, — тоже drift:
        # молчание о нём выдавало бы исчезновение источника за здоровье.
        if name not in current_blocks and str(old or ""):
            changed[name] = {"frozen_chars": len(str(old)), "current_chars": 0}
    if not changed:
        return None
    total_old = sum(len(str(v)) for v in frozen_blocks.values())
    total_new = sum(len(v) for v in current_blocks.values())
    return {"blocks": changed, "frozen_chars": total_old, "current_chars": total_new}


def flip_epoch(ctx) -> int:
    """Ручная граница (её рука / наш рычаг). Следующий захват заморозит E заново под
    номером n+1; сворачивание накопителя решается на захвате его же порогами.

    Её рука — единственное, что снимает храповик аудитории обратно вверх."""
    stream_dir = _shadow_root() / _stream_key(ctx)
    saved = _load_epoch(stream_dir) or {}
    n = int(saved.get("n") or _max_epoch_in_metrics(stream_dir) or 0) + 1
    stream_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(_epoch_path(stream_dir), json.dumps(
        {"v": FRAME_SCHEMA, "n": n, "frozen_at": None, "e_text": None,
         "pending_reason": "её слово (flip_epoch)"}, ensure_ascii=False))
    return n


# ------------------------------------------------------------------------ зона A


def _a_message(message) -> str:
    """Одно сообщение окна, с капом РЕНДЕРА и честным указателем на полный материал.

    Без капа одно сообщение тяжелее A_MAX_CHARS выносило из окна ВСЁ (цикл `pop(0)`
    ниже доходил до kept=0, унося и само письмо, и свежайшие реплики) — проверено
    прогоном 20.08: 31 сообщение с одним на 130k давали окно в 54 знака.

    Адрес в указателе не выдуман: история этого потока собирается раннером из
    `memory_life.hot_records`, а те записи — дословный `text` событий
    `memory/life/events/<день>.jsonl` (kind `conversation_message`, `append_event`
    ничего не режет). Подпись — кодовая: это НЕ её отбор (её слово №6)."""
    if not isinstance(message, dict):
        return ""
    role = str(message.get("role") or "?")
    text = _text_of(message.get("content"))
    if len(text) > A_MSG_MAX:
        # ЕЁ слово 21.08 (№1): срез ДВУСТОРОННИЙ — «сохранять начало и конец, между
        # ними явный маркер с числом вырезанных знаков и точным способом поднять
        # полный источник». Обрезка только хвоста прятала бы именно ту область, где
        # у результатов рук живут итоги и ошибки.
        total = len(text)
        cut = total - (A_MSG_HEAD + A_MSG_TAIL)
        text = (text[:A_MSG_HEAD]
                + f"\n{_A_CUT_MARK} первые {A_MSG_HEAD} и последние {A_MSG_TAIL} из "
                  f"{total} знаков этого сообщения · вырезано {cut} зн. посередине · "
                  "срез двусторонний · это НЕ мой отбор · целиком: события "
                  "memory_life этого потока — memory/life/events/*.jsonl, kind "
                  "conversation_message]\n"
                + text[-A_MSG_TAIL:])
    return f"\n[{role}]\n{text}\n"


def _turn_sha(message) -> str:
    """Отпечаток одной реплики истории — единственная идентичность, которая доживает.

    До тени доезжает ровно `{role, content}`: склейка раннера (`_turns_to_dialogue`)
    сшивает подряд идущие реплики одного автора в один блок и выбрасывает ts, source_id
    и message_id вместе со счётом (замер: 125 горячих записей → 107 блоков). Опереться
    позиционному индексу не на что, отпечатку — есть.
    """
    if not isinstance(message, dict):
        return ""
    role = str(message.get("role") or "?")
    body = (role + "\n" + _text_of(message.get("content"))).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _fold_anchor(history, fold_count: int, now: datetime) -> dict | None:
    """Якорь границы: отпечатки последних свёрнутых реплик + время заморозки."""
    rows = list(history or ())[:fold_count]
    chain = [s for s in (_turn_sha(m) for m in rows[-FOLD_ANCHOR_CHAIN:]) if s]
    if not chain:
        return None
    return {"chain": chain, "at": _minutes_utc(now), "count_at_freeze": int(fold_count)}


def _resolve_fold(history, saved: dict) -> tuple[int, str]:
    """(сколько реплик свёрнуто В ЭТОЙ истории, чем найдено). Считается КАЖДЫЙ захват.

    Якоря нет в окне — значит фронт срезали свёрткой места, и всё оставшееся новее
    якоря: правильный ответ ровно 0, и счёт не утверждается вовсе (врать про число
    свёрнутого хуже, чем показать лишнее старое). Файлы эпохи без якоря (число вместо
    цепочки) читаются прежним числом — один последний раз, до приживления якоря.

    Поиск идёт С НАЧАЛА, а не с конца. Ошибка обязана быть в сторону ПОКАЗАТЬ ЛИШНЕЕ
    СТАРОЕ, никогда — спрятать свежее (закон этого якоря); поиск с конца при дословном
    повторе внутри свёрнутого префикса уводил границу ВПЕРЁД и прятал живые реплики.
    Замерено на сведении 20.08: шесть одинаковых тяжёлых реплик, свёртка 1 → поиск
    с конца отвечал 6 и опустошал зону A целиком.
    """
    rows = list(history or ())
    anchor = (saved or {}).get("fold_anchor")
    if not isinstance(anchor, dict):
        count = min(int((saved or {}).get("fold_count") or 0), len(rows))
        return count, ("число прежней схемы" if count else "свёртки нет")
    chain = [str(x) for x in (anchor.get("chain") or ()) if str(x)]
    if not chain:
        return 0, "якорь пуст"
    shas = [_turn_sha(m) for m in rows]
    k = len(chain)
    for i in range(0, len(shas) - k + 1):
        if shas[i:i + k] == chain:
            return i + k, "якорь"
    return 0, "якорь вне окна"


# ------------------------------------------------------------- накопитель окна A


def _window_path(stream_dir: Path) -> Path:
    return stream_dir / "window.json"


def _window_load(stream_dir: Path) -> list[dict]:
    """Накопленное окно потока. Ошибка чтения — пустой накопитель с криком в лог:
    ронять захват из-за битого файла нельзя, но и молчать о потере — тоже."""
    try:
        raw = json.loads(_window_path(stream_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        log.exception("frame_shadow: накопитель окна не прочитался — начинаю заново")
        return []
    rows = raw.get("rows") if isinstance(raw, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _window_merge(stored: list[dict], incoming) -> tuple[list[dict], dict]:
    """Слить живое (скользящее) окно в накопленное: НАКОПИТЕЛЬ, а не зеркало.

    Живая лента режется собственным бюджетом и роняет верх на каждом ходе; пока
    зона A её зеркалила, append-only рвалась своей же головой (29.08, комната:
    префикс держал 47k из 123k байт, разрыв в КАЖДОЙ паре захватов — «якорь вне
    окна» с 25.08, а порог свёртки не наступал никогда: бюджет живой ленты тоньше
    порога 70k). Теперь верх окна двигает только свёртка.

    Стык — отпечаток хвостового блока накопителя во входящем, поиск С НАЧАЛА
    входящего: по закону якоря ошибка обязана быть в сторону ПОКАЗАТЬ ЛИШНЕЕ
    СТАРОЕ (дословный повтор старой реплики может дать раннее ложное совпадение —
    тогда старое покажется дважды), никогда — спрятать свежее. Хвост отступает до
    A_STORE_SEEK блоков: склейка раннера пересобирает последние блоки, и доросший
    блок ЗАМЕНЯЕТСЯ входящей версией, а не дублируется. Если входящее несёт НАД
    стыком больше старого, чем держит накопитель (подрезка после свёртки, а живое
    окно ещё помнит), — излишек ПОДКЛЕИВАЕТСЯ сверху: прятать предъявленное
    старое нельзя, а счёт свёрнутого у `_resolve_fold` позиционный и сам найдёт
    границу в удлинившейся истории. Правка старого сообщения задним числом
    остаётся в накопителе прежней версией до ближайшей свёртки — свежие строки
    доезжают всегда, расхождение рендера меряет отчёт пар. Входящее без единого
    стыка — прыжок потока: честный сброс, названный метрикой, старое при прыжке
    не тащится (показать нерелевантное старое — тоже враньё о разговоре)."""
    inc = [m for m in list(incoming or ()) if isinstance(m, dict)]
    if not stored:
        return inc, {"rows": len(inc), "appended": len(inc), "replaced": 0,
                     "reset": False}
    shas = [_turn_sha(m) for m in stored]
    inc_shas = [_turn_sha(m) for m in inc]
    for back in range(1, min(len(stored), A_STORE_SEEK) + 1):
        end = len(stored) - back + 1     # kept = stored[:end], стык на kept[-1]
        target = shas[end - 1]
        # Среди вхождений отпечатка выбирается стык с САМЫМ ДЛИННЫМ обратным
        # прогоном совпадений: одиночный отпечаток повторим («да.», а в пределе —
        # окно из одинаковых реплик, где первое вхождение дублировало хвост на
        # каждом захвате), прогон — нет. При равном прогоне — раннее вхождение:
        # ошибка в сторону показать лишнее старое, по закону якоря.
        best_j, best_run = -1, 0
        for j, sha in enumerate(inc_shas):
            if sha != target:
                continue
            run = 1
            while run < end and run <= j and inc_shas[j - run] == shas[end - 1 - run]:
                run += 1
            if run > best_run:
                best_j, best_run = j, run
        if best_j < 0:
            continue
        fresh = inc[best_j + 1:]
        kept = stored[:end]
        extra = best_j - (len(kept) - 1)
        head = inc[:extra] if extra > 0 else []
        merged = head + kept + fresh
        return merged, {"rows": len(merged), "appended": len(fresh),
                        "replaced": back - 1, "prepended": len(head),
                        "reset": False}
    return inc, {"rows": len(inc), "appended": len(inc), "replaced": 0,
                 "reset": True}


def _window_persist(stream_dir: Path, rows, fold_count: int) -> int:
    """Положить накопитель на диск, подрезав свёрнутый префикс до якорной цепочки.

    Свёрнутое уже пересказано сводкой на границе; держать его целиком — рост без
    предела. Цепочка последних свёрнутых остаётся, чтобы `_resolve_fold` находил
    границу («якорь») в подрезанной истории — счёт свёрнутого у него позиционный,
    в этой истории, ровно под такой случай. Строки нормализуются к
    `{role, content-текст}`: отпечаток и рендер считаются через `_text_of`, так
    что нормализация их не меняет. Возвращает, сколько строк подрезано."""
    keep_from = max(0, int(fold_count) - FOLD_ANCHOR_CHAIN)
    kept = [{"role": str(m.get("role") or "?"),
             "content": _text_of(m.get("content"))} for m in list(rows)[keep_from:]]
    _write_atomic(_window_path(stream_dir),
                  json.dumps({"v": 1, "rows": kept}, ensure_ascii=False))
    return keep_from


def _zone_a(history, fold_count: int, recap_head: str) -> tuple[str, dict, str]:
    """Окно A: свёрнутый обрубок + живой хвост. Выбрасывать умеет только СТАРШИХ и
    никогда — последнее сообщение: окно без последней реплики это не окно, а обломок
    (нынешний цикл доходил до нуля и всё равно писал «старшие ждут границы»)."""
    tail = list(history or ())[fold_count:] if fold_count else list(history or ())
    rows = [_a_message(m) for m in tail[-A_MAX_MESSAGES:]]
    dropped_by_count = max(0, len(tail) - len(rows))
    capped = sum(1 for r in rows if _A_CUT_MARK in r)
    dropped_by_chars = 0
    dropped_chars = 0
    while len(rows) > 1 and sum(len(r) for r in rows) > A_MAX_CHARS:
        dropped_chars += len(rows.pop(0))
        dropped_by_chars += 1
    total = len(list(history or ()))
    kept = len(rows)
    live = total - fold_count
    # ЕЁ слово 21.08 (№2): пол живого хвоста 12 — «если даже 12 не помещаются в
    # аварийный потолок, это отдельная ВИДИМАЯ аварийная деградация, а не молчаливое
    # снижение пола». Пол меряется против живого хвоста: короткая история не авария.
    floor_breach = kept < min(A_KEEP_TAIL_MIN, live)
    # ⚠ ГОЛОВА ОКНА УЕХАЛА В T, И ЭТО ЕЁ РЕШЕНИЕ 27.08: «маркер границы свёртки
    # тоже перенести в T, прямо как ориентир перед подвижной лентой. Он не обязан
    # быть частью стабильного A: его функция — объяснить именно ТЕКУЩУЮ границу окна,
    # а она по природе ездит».
    #
    # Сюда же по тому же правилу («live-счётчики — в T») уехали счёт окна и
    # аварийная пометка пола: оба пересчитываются каждый ход. Замер 27.08: 169
    # разрывов префикса из 656 пришлись на A, и виновники были ровно эти строки —
    # append-only зона рвалась своей же шапкой.
    #
    # В A остаётся СВОДКА прошлых ходов: она снята на границе и между границами не
    # меняется, то есть стабильна по построению (её №8).
    window = ""
    if floor_breach:
        window += (f"⚠ [АВАРИЙНАЯ ДЕГРАДАЦИЯ ОКНА: живой хвост {kept} сообщ. — ниже "
                   f"пола {A_KEEP_TAIL_MIN}; даже пол не влез в аварийный потолок "
                   f"{A_MAX_CHARS} зн. Это названная авария, не молчаливое снижение "
                   f"пола (её №2)]\n")
    if kept < live:
        window += (f"[окно: {kept} из {live} живых сообщений · выброшено кодом "
                   f"{dropped_by_count + dropped_by_chars}: {dropped_by_count} за "
                   f"потолком {A_MAX_MESSAGES} сообщ., {dropped_by_chars} за потолком "
                   f"{A_MAX_CHARS} зн. ({dropped_chars} зн. рендера) · выброшены самые "
                   f"старшие, последнее сообщение остаётся всегда · это НЕ мой отбор]\n")
    head = (recap_head + "\n") if recap_head else ""
    return head + "".join(rows), {
        "kept": kept, "total": total, "folded": fold_count,
        "dropped_by_chars": dropped_by_chars,
        "dropped_by_count": dropped_by_count,
        "dropped_chars": dropped_chars, "capped": capped,
        "floor_breach": floor_breach,
    }, window


def _needs_fold(history, fold_count: int) -> bool:
    tail = list(history or ())[fold_count:]
    if len(tail) > A_FOLD_MESSAGES:
        return True
    chars = sum(len(_a_message(m)) for m in tail)
    return chars > A_FOLD_CHARS


# ------------------------------------------------------------------------ зона T


def _zone_t(ctx, speaker, user_msg, now: datetime,
            payload: dict | None = None, window: str = "",
            places: str = "") -> str:
    """Хвост: машинный контракт хода → ситуация → вход (вход последним — ближе всех
    к ответу).

    Машинный хвост — ЕЁ слово 21.08 (№8, арифметика «б»): «contract.* и
    state.owner_place — в T, а не в E: это машинный действующий контракт/адрес хода,
    он не должен вытеснять живого человека и не должен читаться как мой авторский
    документ». Сюда же state_block, channel_facts, mutable operational continuity и
    [reply]-часть рамки. Цену она назвала сама: эти знаки пересобираются каждый ход
    и не держат кэш — и это принятая цена, не дефект. Тексты приезжают аргументом от
    живого пути; нет текстов — нет хвоста (и coverage честно считает их todo)."""
    # Подвижное, уехавшее сюда 27.08 её решением: состояние окна A (граница
    # свёртки, счёт, авария пола) и обстановка мест (часы последней активности,
    # формулы переноса, сроки режимов). Стоит ПЕРЕД <ТЕКУЩЕЕ> и сразу после ленты —
    # это ориентир к тому, что она только что прочитала.
    moving = ""
    if window:
        moving += window if window.endswith("\n") else window + "\n"
    if places:
        moving += places.rstrip("\n") + "\n"
    if moving:
        moving = ("[состояние окна и обстановка мест — пересобирается каждый ход; "
                  "стабильные слои этого не касаются]\n" + moving + "\n")
    machine = ""
    rows = list((payload or {}).get("machine") or ())
    if rows:
        parts = [f"--- {name} ---\n{text}" for name, text in rows]
        machine = ("[машинный хвост хода — действующий контракт и состояние; собрано "
                   "кодом живого пути · это НЕ мой голос и НЕ эпоха (её №8: "
                   "contract/state живут в T)]\n"
                   + "\n\n".join(parts) + "\n\n")
    chat_id = getattr(ctx, "chat_id", None)
    place = ("личка" if getattr(ctx, "is_dm", False) else "группа")
    if chat_id is not None:
        place += f" {chat_id}"
    who = str(speaker or "").strip() or "—"
    entry = _text_of(user_msg).strip()
    if len(entry) > T_INPUT_MAX:
        entry = entry[:T_INPUT_MAX] + f"\n[обрезано: вход {len(_text_of(user_msg))} зн.]"
    return (moving
            + machine
            + "<ТЕКУЩЕЕ>\n"
            f"место: {place}\n"
            f"говорит: {who}\n"
            f"адрес ответа: {who if who != '—' else place}\n"
            f"время: {_clock_line(now)}\n"
            "работа: —\n"
            "</ТЕКУЩЕЕ>\n"
            "вход:\n"
            f"{entry}\n")


# --------------------------------------------------- тексты живого пути (её №8)


def _live_payload(live_sections) -> dict:
    """Разложить опись прибора на грузы для тени: machine-хвост T,
    сводка прошлых ходов (A на границе) и множество carried —
    имена секций, которые тень в этом захвате ВПРАВДУ несёт (по нему честен
    coverage). Тексты в описи есть только у секций frame_trace.TEXT_CARRIED; их
    отсутствие — не ошибка, а прежний режим: тогда machine пуст и секции остаются
    в todo. Тень не парсит system и не сочиняет машинный контракт — только берёт
    его аргументом; при шве живой путь передаст те же тексты сам."""
    machine: list[tuple[str, str]] = []
    carried: set[str] = set()
    voice_frame = recap = ""
    for row in list(live_sections or ()):
        if not isinstance(row, dict) or not row.get("included"):
            continue
        name = str(row.get("name") or "")
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        label = str(row.get("label") or "")
        if name == "frame.extra_system":
            # extra_system — общий runtime-аргумент: здесь бывают статус связи,
            # задача и почтовые дополнения, не только голосовая рамка. [reply]
            # не является декларацией стабильности предшествующего текста.
            # Текущий контракт переносится целиком; явно стабильная рамка может
            # передаваться через prepare/build отдельным payload.voice_frame.
            machine.append((name, text.strip("\n")))
            carried.add(name)
        elif name.startswith(("contract.", "state.")):
            machine.append((name, text.strip("\n")))
            carried.add(name)
        elif name == "evidence.tier" and label == "Mutable operational continuity":
            machine.append((f"evidence.tier[{label}]", text.strip("\n")))
            carried.add(f"{name}:{label}")
        elif name == "evidence.tier" and label.startswith("Ранее в этом диалоге"):
            recap = text.strip("\n")
            carried.add(f"{name}:{label}")
    return {"machine": machine, "voice_frame": voice_frame, "recap": recap,
            "carried": carried}


# ------------------------------------------------------------------------- сборка


@dataclass
class Frame:
    stream: str
    epoch_n: int
    k: str
    e: str
    a: str
    t: str
    stable_text: str
    text: str
    a_meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """Всё подвижное, разрешённое ДО сборки: номер эпохи, её замороженный текст и
    свёртка накопителя. Граница, drift, опись деградаций и тупик свёртки едут рядом —
    они для метрик, не для байтов; blocks и lifted — тоже: сборщик их не читает,
    читает только `e_text`."""
    epoch_n: int
    e_text: str
    fold_count: int = 0
    fold_line: str = ""
    boundary: dict | None = None
    drift: dict | None = None
    blocks: dict = field(default_factory=dict)
    lifted: list = field(default_factory=list)
    degraded: list = field(default_factory=list)
    hands: dict = field(default_factory=dict)
    audience: str = "other"
    fold: dict = field(default_factory=dict)
    fold_stuck: dict | None = None
    # Сводка прошлых ходов, снятая живым путём на границе (её №8: «сводка — A»);
    # {"text","at"} | None. Едет из снапшота эпохи — байт-в-байт между границами.
    recap: dict | None = None
    # Обстановка мест: часы последней активности и формулы переноса. НЕ заморожена —
    # пересобирается каждый ход и живёт в T (её решение 27.08).
    places_now: str = ""


def prepare(*, ctx, history, tools, now: datetime | None = None,
            payload: dict | None = None) -> Plan:
    """Внешняя половина сборщика — та, что имеет право трогать диск: чтение эпохи,
    заморозка на границе, план свёртки. Всё, что она решила, уезжает в Plan; байты
    кадра из него собирает `build`, и другого пути к байтам нет ни у кого.

    Живой путь при switch зовёт ровно эту пару — prepare + build — и получает те же
    байты, что тень: не «похожие», а те же, потому что функция одна. `payload` —
    тексты живого пути (машинный контракт, рамка, сводка): в тени их даёт опись
    прибора, при шве живой путь передаст их сам."""
    now = now or datetime.now(timezone.utc)
    stream_dir = _shadow_root() / _stream_key(ctx)
    stream_dir.mkdir(parents=True, exist_ok=True)
    saved, drift, boundary, fold, stuck = _epoch_for_capture(
        stream_dir, ctx, tools, history, now, payload)
    blocks = saved.get("blocks") if isinstance(saved.get("blocks"), dict) else {}
    anchor = saved.get("fold_anchor")
    anchor = anchor if isinstance(anchor, dict) else {}
    recap = saved.get("recap")
    return Plan(epoch_n=int(saved.get("n") or 1),
                e_text=str(saved.get("e_text") or ""),
                fold_count=int(fold["count"]),
                fold_line=str(fold["line"] or ""),
                boundary=boundary, drift=drift, blocks=dict(blocks),
                lifted=list(saved.get("lifted_meta") or []),
                degraded=list(saved.get("e_degraded") or []),
                # Пусто у эпох, замороженных до появления камерного потолка: прибор
                # молчит о том, чего не мерил, вместо того чтобы досчитывать задним
                # числом по чужим байтам.
                hands=dict(saved.get("hands_meta") or {}),
                audience=str(saved.get("audience") or "owner"),
                # Чем найдена граница свёртки: «якорь» · «якорь вне окна» (фронт
                # срезан) · «якорь приживлён» (миграция) · «граница» (только что
                # заморожена).
                fold={"count": int(fold["count"]), "by": str(fold["by"]),
                      "at": str(anchor.get("at") or "")},
                fold_stuck=stuck,
                recap=recap if isinstance(recap, dict) else None,
                # Считается ЗДЕСЬ, а не в _epoch_blocks: то, что заморожено, между
                # границами не меняется — а обстановка обязана.
                places_now=_block_address_book(
                    ctx, str(saved.get("audience") or "owner"), moving=True))


def build(*, ctx, history, speaker, user_msg, tools, now: datetime,
          plan: Plan, payload: dict | None = None) -> Frame:
    """Чистая сборка: никакого IO, кроме чтения source-файлов K. Всё подвижное
    (эпоха, свёртка, время) приходит аргументами — на этом стоят тесты детерминизма.

    ЕДИНСТВЕННЫЙ сборщик байтов кадра. Тень зовёт его из `capture`, живой путь при
    switch — из `_voice_impl`; двух сборок с «почти одинаковым» результатом не бывает,
    бывает одна и её копия, разошедшаяся молча."""
    stream = _stream_key(ctx)
    epoch_n, e_text = plan.epoch_n, plan.e_text
    k = _zone_k()
    # Сводка остаётся в A (стабильна между границами); маркер границы свёртки
    # уехал в T вместе с прочим подвижным.
    fold_head = ""
    recap = plan.recap if isinstance(plan.recap, dict) else None
    if recap and str(recap.get("text") or ""):
        # Сводка старше всего в A — стоит первой; снята на границе и не меняется
        # между границами (её №8: «сводка — A»; подпись — машинная, её №6).
        fold_head = (f"[сводка прошлых ходов · снимок живого пути на границе "
                     f"{recap.get('at')} · это НЕ мой отбор]\n"
                     f"{recap.get('text')}").rstrip("\n")
    a, a_meta, window = _zone_a(history, plan.fold_count, fold_head)
    if plan.fold_line:
        window = plan.fold_line.rstrip("\n") + "\n" + window
    t = _zone_t(ctx, speaker, user_msg, now, payload,
                window=window, places=plan.places_now)
    # Номер эпохи из заголовка НАД конституцией убран (18.08): он инвалидировал бы
    # 13k неизменной K на каждой границе. Номер живёт в шапке E и в метриках.
    header = f"# Теневой кадр · {stream}\n"
    stable = header + k + _SEP_E + e_text + _SEP_A + a
    return Frame(stream=stream, epoch_n=epoch_n, k=k, e=e_text, a=a, t=t,
                 stable_text=stable, text=stable + _SEP_T + t, a_meta=a_meta)


# ------------------------------------------------------------- полнота против живого

# Тиры, которые тень несёт ДЕДУПОМ — своим рендером того же источника (её реестр
# №8): совпадение байтов не обещается, расхождение меряет отчёт пар, а coverage
# отвечает на «есть ли у секции дом в тени». Точный ярлык — covered. Ярлык, чья
# ГОЛОВА совпала, а продолжение уехало, — DRIFTED: дом называется, тождество не
# утверждается. Прежде такой ярлык падал в todo молча — и её собственная правка
# реестра («Карта памяти — ВНУТРЕННЯЯ…», 29.08 в комнате) читалась как бездомный
# долг, неотличимый от настоящего. Подделка головы при этом НЕ прячется: drifted
# стоит отдельным счётом с ярлыком и домом-кандидатом, а covered остаётся строгим.
_EXACT_TIER_LABELS = {label: home for label, home in (
    ("Мои досье на людей — присутствующие целиком", "lifted"),
    ("Досье собеседника", "lifted"),
    ("Canonical desire continuity", "desires"),
    ("Мои желания", "desires"),
    ("Почтовый ящик — ЛОКАТОР, не индекс", "mail"),
    ("Карта памяти", "memory_index"),
    ("Эта комната", "address_book"),
)}
# Головы ярлыков для drifted: до « — »/«(» ярлык НАЗЫВАЕТ тир, дальше — наставление,
# которое она правит чаще, чем имя. Голова короче четырёх слов не бывает случайной.
_TIER_HOME_HEADS = (
    ("Мои досье на людей", "lifted"),
    ("Досье собеседника", "lifted"),
    ("Canonical desire continuity", "desires"),
    ("Мои желания", "desires"),
    ("Почтовый ящик", "mail"),
    ("Карта памяти", "memory_index"),
    ("Эта комната", "address_book"),
)
# Кандидатные дома шести СТРОК зоны «СЕЙЧАС» (реестр закрыт —
# frame_trace.SITUATION_ROSTER; имя вне реестра остаётся unknown). Это не
# `covered`: живые строки богаче теневого <ТЕКУЩЕЕ> — например место несёт title,
# scope/mode/disclosure, адрес — reply_to и маршрут, лента — числа и источник.
# Тень знает, ГДЕ должен жить смысл, но пока несёт только подмножество, поэтому
# исход честно `drifted` с названным домом. Открывающий/закрывающий маркеры точны.
# `.gap`-вариант относится к тому же кандидату-дому, но равенство также не заявляет.
_SITUATION_CANDIDATE_HOMES = {
    "situation.place": "t", "situation.speaker": "t",
    "situation.address": "t", "situation.timing": "t",
    "situation.working": "t", "situation.feed": "a",
}
# Похороненное — ЕЁ слово №8: header удалить; легенда живёт только пока существует
# gutter, а в тени gutter-а нет — «при снятии gutter легенда удаляется тем же
# изменением».
_BURIED = {
    "evidence.header": "её №8: удалить",
    "evidence.legend": "её №8: легенда живёт только при gutter — в тени gutter-а нет",
}


def _coverage(live_sections, carried=frozenset(), recap_in_a: bool = False) -> dict:
    """Полнота тени против живой описи. Пять исходов на секцию: covered (у секции
    есть дом в тени — зоной, дедупом или carried-текстом этого захвата) · drifted
    (голова ярлыка тира совпала с известным домом, продолжение уехало — дом назван,
    тождество не утверждается; сверка ярлыка за ней) · buried (её слово: секция
    умирает) · todo (дома нет — честный долг) · unknown (прибор не узнал имени).
    `carried` — множество имён, которые тень В ЭТОМ захвате вправду везёт: секция,
    чей текст не приехал, остаётся todo, а не объявляется покрытой авансом."""
    covered = todo = buried = 0
    todo_rows: list[dict] = []
    buried_rows: list[dict] = []
    drifted_rows: list[dict] = []
    unknown: list[str] = []

    def note(row, name, sink):
        item = {"name": name, "chars": int(row.get("chars") or 0)}
        # ЯРЛЫК ЕДЕТ ВМЕСТЕ С РАЗМЕРОМ (семь тиров звались одинаково, опознание
        # шло по позиции в agent.py); `variant` рядом по той же причине.
        for extra in ("label", "variant"):
            if row.get(extra):
                item[extra] = str(row[extra])[:120]
        sink.append(item)

    for row in list(live_sections or ()):
        if not isinstance(row, dict) or not row.get("included"):
            continue
        name = str(row.get("name") or "")
        label = str(row.get("label") or "")
        if name in {"persona.soul", "persona.voice", "persona.self_current",
                    "situation.channel", "situation.open", "situation.close"}:
            covered += 1
        elif name in _SITUATION_CANDIDATE_HOMES or (
                name.endswith(".gap") and
                name[:-len(".gap")] in _SITUATION_CANDIDATE_HOMES):
            base_name = name[:-len(".gap")] if name.endswith(".gap") else name
            note(row, name, drifted_rows)
            drifted_rows[-1]["home"] = _SITUATION_CANDIDATE_HOMES[base_name]
        elif name in _BURIED:
            buried += 1
            note(row, name, buried_rows)
            buried_rows[-1]["reason"] = _BURIED[name]
        elif name.startswith(("contract.", "state.")) or name == "frame.extra_system":
            if name in carried:
                covered += 1
            else:
                todo += 1
                note(row, name, todo_rows)
        elif name == "evidence.tier":
            if label in _EXACT_TIER_LABELS:
                covered += 1
            else:
                if label == "Mutable operational continuity":
                    if f"{name}:{label}" in carried:
                        covered += 1
                    else:
                        todo += 1
                        note(row, name, todo_rows)
                elif label.startswith("Ранее в этом диалоге"):
                    if recap_in_a:
                        covered += 1
                    else:
                        todo += 1
                        note(row, name, todo_rows)
                else:
                    home = next((h for head, h in _TIER_HOME_HEADS
                                 if label.startswith(head)), "")
                    if home:
                        note(row, name, drifted_rows)
                        drifted_rows[-1]["home"] = home
                    else:
                        todo += 1
                        note(row, name, todo_rows)
        elif name.startswith("evidence."):
            todo += 1
            note(row, name, todo_rows)
        else:
            unknown.append(name)
    return {"covered": covered, "todo": todo, "buried": buried,
            "drifted": len(drifted_rows),
            "unknown": len(unknown), "todo_names": todo_rows[:40],
            "buried_names": buried_rows[:10],
            "drifted_names": drifted_rows[:10], "unknown_names": unknown[:20]}


# ------------------------------------------------------------------------- захват


def _last_path(stream_dir: Path) -> Path:
    return stream_dir / "last.json"


def _rotate(stream_dir: Path) -> None:
    shadows = sorted(stream_dir.glob("*.md"))
    for stale in shadows[:-KEEP_SHADOWS] if len(shadows) > KEEP_SHADOWS else ():
        try:
            stale.unlink()
        except OSError:
            pass


def _fold_plan(history, saved: dict, now: datetime, *,
               force: bool) -> tuple[int, str, dict | None, dict | None]:
    """Сколько сворачивается, какой строкой, не упёрлась ли свёртка и на каком якоре.

    Хвост держим не числом, а весом: берём с конца столько сообщений, сколько влезает
    в A_FOLD_CHARS, но не больше A_KEEP_TAIL и не меньше A_KEEP_TAIL_MIN. Фиксированные
    50 не сходились: если хвост из 50 весит больше порога, `max(old, total-50)` не рос
    ни на шаг, `_needs_fold` оставался True — и КАЖДЫЙ захват объявлял границу
    (прогон 20.08: пять захватов подряд — эпохи 1..5 на одной и той же истории).

    Продвинуть некуда — возвращаем НЕ границу, а числа третьим элементом: названный
    тупик честнее молчаливой границы на каждом ходу.

    Строка обрубка держит ВРЕМЯ границы, а не счёт свёрнутых. Счёт меряется в блоках
    ПОСЛЕ склейки раннера, а склейка пересобирается каждым ходом — замороженное число
    протухало бы молча, и обрубок утверждал бы то, чего проверить нельзя. Время границы
    не протухает; глубина живого хвоста, наоборот, считается ЗДЕСЬ И СЕЙЧАС и потому
    стоит числом. Обрубок подписан кодом: машинный текст не выдаёт себя за её отбор
    (её слово №6)."""
    rows = list(history or ())
    total = len(rows)
    old_fold, _by = _resolve_fold(rows, saved or {})
    old_line = str((saved or {}).get("fold_line") or "")
    old_anchor = (saved or {}).get("fold_anchor")
    old_anchor = old_anchor if isinstance(old_anchor, dict) else None
    if not force and not _needs_fold(rows, old_fold):
        return old_fold, old_line, None, old_anchor
    keep = 0
    kept_chars = 0
    for message in reversed(rows):
        if keep >= A_KEEP_TAIL:
            break
        size = len(_a_message(message))
        if keep >= A_KEEP_TAIL_MIN and kept_chars + size > A_FOLD_CHARS:
            break
        keep += 1
        kept_chars += size
    new_fold = max(0, total - keep)
    if new_fold <= old_fold:
        stuck = None
        if _needs_fold(rows, old_fold):
            stuck = {"fold_count": old_fold, "kept": total - old_fold,
                     "keep_floor": A_KEEP_TAIL_MIN,
                     "tail_chars": sum(len(_a_message(m)) for m in rows[old_fold:]),
                     "fold_chars": A_FOLD_CHARS}
        return old_fold, old_line, stuck, old_anchor
    line = (f"[окно свёрнуто кодом · до {_minutes_utc(now)} · "
            f"в живом хвосте {keep} ({kept_chars} зн. рендера) · это НЕ мой отбор · "
            f"полностью: события memory_life этого потока — "
            f"memory/life/events/*.jsonl, kind conversation_message]")
    return new_fold, line, None, _fold_anchor(rows, new_fold, now)


def _cross(stream_dir: Path, ctx, tools, history, now: datetime, *, saved: dict,
           n: int, reason: str, audience: str, force: bool,
           extra: dict | None = None, payload: dict | None = None):
    """Пересечь границу: спланировать свёртку, заморозить E, назвать границу.

    Одно место на все причины — иначе каждая ветка обрастала бы своим порядком
    аргументов, и однажды одна из них заморозила бы эпоху не под ту аудиторию."""
    fold_count, fold_line, stuck, anchor = _fold_plan(history, saved, now, force=force)
    fresh = _freeze_epoch(stream_dir, ctx, tools, now, n, reason, fold_count,
                          fold_line, fold_anchor=anchor, audience=audience,
                          payload=payload)
    boundary = {"reason": reason, "n": n, "folded": fold_count}
    if extra:
        boundary.update(extra)
    return (fresh, None, boundary,
            {"count": fold_count, "by": "граница", "line": fold_line}, stuck)


def _epoch_for_capture(stream_dir: Path, ctx, tools, history,
                       now: datetime,
                       payload: dict | None = None
                       ) -> tuple[dict, dict | None, dict | None,
                                  dict, dict | None]:
    """(эпоха, drift, boundary, свёртка, тупик свёртки).

    Границы здесь, В ПОРЯДКЕ ПРОВЕРКИ СВЕРХУ ВНИЗ: первая заморозка · рваный файл
    (аварийная, с восстановлением номера) · её flip · переход на схему v4 · СУЖЕНИЕ
    АУДИТОРИИ · смена набора рук · СОДЕРЖАТЕЛЬНОЕ ИЗМЕНЕНИЕ ЖЕЛАНИЙ (её №8) ·
    порог накопителя (ЕЁ числа 120/70k; свёртка кодом, подписанная «не мой отбор»).

    Порядок не случаен. Схема раньше всего: снапшот чужого формата нельзя ни сверять,
    ни сужать. Аудитория раньше набора и порога: безопасность раньше экономии — снимок,
    собранный ШИРЕ потока, дожить до чужого хода не имеет права. Порог последним, и он
    объявляет границу ТОЛЬКО если свёртка вправду продвинулась: иначе граница вставала
    бы на КАЖДОМ захвате (лечение churn), а тупик уезжает числами в `fold_stuck`.

    Четвёртым возвращается СВЁРТКА, посчитанная под сегодняшнюю историю: `{count, by,
    line}`. Раньше `capture` брал `saved["fold_count"]` числом — и это число указывало
    на другие реплики, как только окно сдвинулось."""
    saved = _load_epoch(stream_dir)
    audience = _audience(ctx)
    if saved is None:
        recovered = _max_epoch_in_metrics(stream_dir)
        if _epoch_path(stream_dir).exists() or recovered:
            n, reason = recovered + 1, "аварийная перезаморозка (файл эпохи нечитаем)"
        else:
            n, reason = 1, "первая заморозка потока"
        fresh = _freeze_epoch(stream_dir, ctx, tools, now, n, reason, 0, "",
                             fold_anchor=None, audience=audience, payload=payload)
        return (fresh, None, {"reason": reason, "n": n, "folded": 0},
                {"count": 0, "by": "граница", "line": ""}, None)
    if saved.get("e_text") is None:  # её flip ждёт заморозки
        n = int(saved.get("n") or 1)
        reason = str(saved.get("pending_reason") or "её слово (flip_epoch)")
        return _cross(stream_dir, ctx, tools, history, now, saved=saved, n=n,
                      reason=reason, audience=audience, force=True, payload=payload)
    if (int(saved.get("v") or 1) != FRAME_SCHEMA
            or not isinstance(saved.get("blocks"), dict)):
        # ОДНА честная граница миграции на все потоки: формат эпохи вправду другой.
        # Отдельные границы на каждое из девяти решений стоили бы девять холодных
        # префиксов вместо одного.
        n = int(saved.get("n") or 1) + 1
        reason = (f"переход на схему кадра v{FRAME_SCHEMA} "
                  "(часы и формула переноса ушли из адресной книги в обстановку "
                  "хвоста; текущий extra_system целиком в T, его прежняя "
                  "неявная заморозка удалена)")
        return _cross(stream_dir, ctx, tools, history, now, saved=saved, n=n,
                      reason=reason,
                      # Миграция формата не является её явным flip: сохранённый
                      # храповик аудитории не имеет права расшириться обратно.
                      audience=_narrower(str(saved.get("audience") or "owner"),
                                         audience),
                      force=False, payload=payload)
    # Храповик аудитории. Снимок, собранный ШИРЕ потока, дожить до чужого хода не имеет
    # права: заморозка — не разрешение. Отсутствие ключа читается как `owner` (такой
    # снимок и собирался полной книгой). Расширение обратно НЕ происходит: узкая эпоха
    # остаётся узкой, E не прыгает, а вернуть полноту может её рука (flip_epoch).
    frozen_audience = str(saved.get("audience") or "owner")
    effective = _narrower(frozen_audience, audience)
    if effective != frozen_audience:
        n = int(saved.get("n") or 1) + 1
        reason = f"сужение аудитории потока ({frozen_audience} → {effective})"
        return _cross(stream_dir, ctx, tools, history, now, saved=saved, n=n,
                      reason=reason, audience=effective, force=False,
                      payload=payload)
    tools_sha = tool_offerings.fingerprint(tools)
    frozen_sha = str(saved.get("tools_sha") or "")
    if not frozen_sha:
        # Эпоха заморожена ДО этой правки — отпечатка у неё нет. Назвать это сменой
        # набора было бы враньём прибора о собственном возрасте: доклеиваем отпечаток
        # молча, без границы. Ровно один раз на поток.
        saved = dict(saved, tools_sha=tools_sha)
        _write_atomic(_epoch_path(stream_dir),
                      json.dumps(saved, ensure_ascii=False, indent=1))
    elif frozen_sha != tools_sha:
        # §2 контракта кадра: смена набора предложенных рук — всегда переворот эпохи.
        # Схемы рук едут ВЫШЕ system и в `prompt_cache_key` не входят (llm.cache_address,
        # пин test_cache_address.py:136) — без границы флап убивает префикс молча, а
        # замороженный блок «руки» вдобавок врёт о руках этого хода. Свёртка здесь
        # force=False: смена набора не повод трогать окно накопителя.
        n = int(saved.get("n") or 1) + 1
        return _cross(stream_dir, ctx, tools, history, now, saved=saved, n=n,
                      reason="смена набора рук", audience=effective, force=False,
                      extra={"tools_sha": tools_sha[:12],
                             "tools_sha_was": frozen_sha[:12]}, payload=payload)
    # ЕЁ слово 21.08 (№8): «границу эпохи вызывать по содержательному изменению
    # ledger, не по служебному шуму». Мера содержательности — сам рендер блока:
    # timestamps, refs и пересборки проекции в него не входят. Отказ источника
    # (ok=False) границей не считается: молчание прибора — не факт о желаниях.
    if effective == "owner" and "desires" in (saved.get("blocks") or {}):
        current_desires, desires_ok = _block_desires()
        if desires_ok and current_desires != saved["blocks"].get("desires"):
            n = int(saved.get("n") or 1) + 1
            return _cross(stream_dir, ctx, tools, history, now, saved=saved, n=n,
                          reason="содержательное изменение желаний (ledger)",
                          audience=effective, force=False, payload=payload)
    fold_count, by = _resolve_fold(history, saved)
    stuck = None
    if _needs_fold(history, fold_count):
        new_fold, fold_line, stuck, anchor = _fold_plan(history, saved, now, force=True)
        if stuck is None:
            n = int(saved.get("n") or 1) + 1
            reason = f"порог накопителя ({A_FOLD_MESSAGES} сообщ. / {A_FOLD_CHARS} зн.)"
            fresh = _freeze_epoch(stream_dir, ctx, tools, now, n, reason, new_fold,
                                  fold_line, fold_anchor=anchor, audience=effective,
                                  payload=payload)
            return (fresh, None, {"reason": reason, "n": n, "folded": new_fold},
                    {"count": new_fold, "by": "граница", "line": fold_line}, None)
        # Свёртке некуда двигаться. Граница здесь была бы границей на КАЖДОМ захвате —
        # а граница рушит байтовый префикс по праву ровно один раз, не каждый ход.
        # Молчать тоже нельзя: тупик называется числами.
    line = str(saved.get("fold_line") or "")
    if by == "число прежней схемы" and fold_count:
        # Приживление снимка, несущего только число (без якоря), БЕЗ границы и без
        # единого нового байта в E: число, которое сегодня ещё верно, привязывается
        # к отпечаткам — и позиционным оно было последний раз.
        anchor = _fold_anchor(history, fold_count, now)
        if anchor is not None:
            saved = dict(saved, v=FRAME_SCHEMA, fold_anchor=anchor)
            _write_atomic(_epoch_path(stream_dir),
                          json.dumps(saved, ensure_ascii=False, indent=1))
            by = "якорь приживлён"
    if by == "якорь вне окна" and line:
        # Счёт не утверждается: граница ушла из окна вместе со срезанным фронтом.
        line += ("\n[граница свёртки из окна ушла: всё ниже — новее её; "
                 "сколько свёрнуто, здесь не утверждается]")
    drift = _epoch_drift(saved, ctx, tools, now, effective, payload)
    return saved, drift, None, {"count": fold_count, "by": by, "line": line}, stuck


def capture(*, ctx, history, speaker, user_msg, tools,
            live_sections=(), now: datetime | None = None) -> dict | None:
    """Собрать тень, положить на диск, снять метрики. Возврат — строка метрик; вызывающий
    (agent.py) её ИГНОРИРУЕТ: у тени нет пути в кадр по построению."""
    m = mode()
    if m == "off":
        return None
    if m == "dm" and not getattr(ctx, "is_dm", False):
        return None
    now = now or datetime.now(timezone.utc)
    stream_dir = _shadow_root() / _stream_key(ctx)
    stream_dir.mkdir(parents=True, exist_ok=True)
    # Накопитель прежде плана: свёртка и сборка обязаны видеть одно и то же окно —
    # накопленное, а не скользящий срез живой ленты (при шве живой путь сольёт
    # своё окно тем же стыком, функция одна).
    history, window_store = _window_merge(_window_load(stream_dir), history)
    payload = _live_payload(live_sections)
    plan = prepare(ctx=ctx, history=history, tools=tools, now=now, payload=payload)
    window_store["trimmed"] = _window_persist(stream_dir, history, plan.fold_count)
    boundary = plan.boundary
    frame = build(ctx=ctx, history=history, speaker=speaker, user_msg=user_msg,
                  tools=tools, now=now, plan=plan, payload=payload)
    new_bytes = frame.text.encode("utf-8")
    lcp = None
    try:
        last = json.loads(_last_path(stream_dir).read_text(encoding="utf-8"))
        prev = (stream_dir / str(last.get("file") or "")).read_bytes()
        held_bytes = _lcp_bytes(prev, new_bytes)
        prev_stable = int(last.get("stable_bytes") or 0)
        lcp = {"bytes": held_bytes, "prev_stable_bytes": prev_stable,
               "held": bool(prev_stable) and held_bytes >= prev_stable}
        if boundary is not None:
            # Граница рушит префикс ПО ПРАВУ — это не дефект закона порядка.
            lcp["boundary"] = True
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("frame_shadow: метрика префикса не снялась")
    run = run_context.current_run()
    fname = (f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%S}-"
             f"{(run.run_id[-8:] if run is not None else uuid.uuid4().hex[:8])}.md")
    (stream_dir / fname).write_text(frame.text, encoding="utf-8", newline="\n")
    stable_bytes = len(frame.stable_text.encode("utf-8"))
    _write_atomic(_last_path(stream_dir), json.dumps(
        {"file": fname, "stable_bytes": stable_bytes}, ensure_ascii=False))
    e_total = _e_total(plan.blocks)
    # Идентификатор состояния сборки — рядом с каждым захватом, а не отдельным
    # манифестом: единственное место, где он и так сверяется, это журнал метрик.
    ident = identity(tools=tools, e_text=frame.e, epoch_n=frame.epoch_n)
    if boundary is not None:
        log.info("frame_shadow: граница «%s» · %s",
                 boundary.get("reason"), identity_line(ident))
    metrics = {
        "ts": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "stream": frame.stream, "epoch": frame.epoch_n, "file": fname,
        # Приборный журнал обязан отвечать, под какую аудиторию собран каждый захват:
        # иначе «в чужие потоки книга не уезжает» непроверяемо задним числом.
        "audience": plan.audience,
        "identity": ident,
        "sizes": {"k": len(frame.k), "e": len(frame.e), "a": len(frame.a),
                  "t": len(frame.t), "total_chars": len(frame.text),
                  "total_bytes": len(new_bytes), "stable_bytes": stable_bytes},
        "e_blocks": {name: len(str(text)) for name, text in plan.blocks.items()},
        # Потолок E меряется по ЗАМОРОЖЕННЫМ блокам — по тем самым величинам, что
        # уехали в снапшот; считается на каждом захвате, а не только на границе,
        # чтобы читатель видел размер эпохи всегда (включая эпохи, замороженные до
        # появления потолка: у них просто пустой degraded).
        "e_overflow": {"total": e_total,
                       "over": max(0, e_total - E_TOTAL_MAX),
                       "degraded": plan.degraded},
        "e_over_target": max(0, e_total - E_TOTAL_TARGET),
        # Отпечаток набора рук ЭТОГО захвата — рядом с размерами. Без него промах
        # префикса нечем атрибутировать: «кэш умер» и «состав рук сменился» выглядят
        # в журнале одинаково. Тот же вид, что в телеметрии llm-вызова (llm._call_trace).
        "tools_sha": tool_offerings.fingerprint(tools)[:16],
        # Указатель рук отдельной мерой: ступень, потолок, несжимаемое ядро имён,
        # байты подсказки и чем блок был бы без потолка. По ней и видно, «полезные
        # байты» это или раздувание.
        "hands_pointer": plan.hands,
        "lifted": plan.lifted,
        "a_window": frame.a_meta,
        # Накопитель: сколько строк держит, сколько пришло этим захватом, сколько
        # хвостовых блоков заменено пересборкой склейки, был ли честный сброс
        # (прыжок потока) и сколько свёрнутого подрезано при записи.
        "window_store": window_store,
        "fold": plan.fold,
        # Сводка в голове A: когда снята и сколько весит. null — сводки на границе
        # не было (живой путь её не дал или граница старее механизма).
        "a_recap": ({"at": plan.recap.get("at"),
                     "chars": len(str(plan.recap.get("text") or ""))}
                    if isinstance(plan.recap, dict) else None),
        # Машинный хвост T: что тень везёт аргументом живого пути (её №8).
        "t_machine": {"sections": [name for name, _t in payload["machine"]],
                      "chars": sum(len(t) for _n, t in payload["machine"])},
        "lcp": lcp,
        "e_drift": plan.drift,
        "boundary": boundary,
        # Свёртка упёрлась в пол хвоста: границы нет, но окно уже не сходится —
        # молчать об этом значит выдавать тупик за здоровье.
        "fold_stuck": plan.fold_stuck,
        "coverage": _coverage(live_sections, carried=payload["carried"],
                              recap_in_a=isinstance(plan.recap, dict)),
    }
    with (stream_dir / "metrics.jsonl").open("a", encoding="utf-8",
                                             newline="\n") as sink:
        sink.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    _rotate(stream_dir)
    return metrics
