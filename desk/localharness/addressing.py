# -*- coding: utf-8 -*-
"""Пробуждение по имени и приоритетная реплика (07.10, просьба Егора о Джарвисе).

Джарвис — агент-тестер Hélène в общем чате. Живой инцидент 07.10: в беседе он
отвечал только владельцу. Гейт адресации требовал имя ЦЕЛЫМ СЛОВОМ в форме
профиля («Jarvis»), а люди писали по-русски и в склонениях («Джарвиса»,
«Джарвис, глянь») — гейт молчал, сообщение падало в ambient; будил только
владелец, чей id и так проходил `is_allowed`. Отсюда две части:

1. `wake_on_name()` / `name_matches_loose()` — имя «не в чистом виде»: имя как
   НАЧАЛО СЛОВА в любой словоформе («джарвиса», «Джарвисом» — будят), регистр
   и ё→е нормализуются, латинское имя ищется и в кириллической транслитерации
   («Jarvis» → «джарвис…»). Рычаг `PRAXIS_WAKE_ON_NAME`: off (по умолчанию,
   байт-в-байт прежнее поведение) | strict (как раньше) | loose.
2. `priority_prefix()` / `split_priority()` / `priority_banner()` — приоритетный
   префикс в начале сообщения (`PRAXIS_PRIORITY_PREFIX`, у Hélène настраиваемый,
   off = выключить): реплика с ним — срочная, будит ход в группе без адресации
   и открывает следующий кадр агента секцией «ПРИОРИТЕТ — требуется действие».
   Префикс остаётся в тексте для людей; вырезается только для решения и баннера.

Парсер живёт здесь, а не в раннере: у модуля нет тяжёлых импортов, и тесты
адресации не тянут Telegram-клиентов. Сестра этого кода у Praxis — парсер в
`telegram_followups` (её хотфикс 07.10); расхождение намеренно: у неё префикс
фиксированный «!», у издания — настраиваемый.
"""
from __future__ import annotations

import os
import re
import unicodedata


def _fold(text: str) -> str:
    value = str(text or "").casefold().replace("ё", "е")
    # Диакритика («Hélène» → «helene») снимается и в имени, и в тексте:
    # люди пишут без акцентов, профиль носит их.
    return "".join(ch for ch in unicodedata.normalize("NFKD", value)
                   if not unicodedata.combining(ch))


# Латиница → кириллица для loose-формы имени: «Jarvis» должен ловить «джарвиса».
# Таблица однобуквенная, составных звуков два (j→дж, x→кс); имя — не словарь,
# точная транслитерация не нужна, нужна узнаваемость префикса.
_LAT_CYR = str.maketrans({
    "a": "а", "b": "б", "c": "ц", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
})
# ⚠ maketrans ключуется ОРДИНАЛАМИ: `ch in _LAT_CYR` для строки всегда False.
# Буквы-ключи — отдельным множеством (поймано тестом t_wake_addressing_0710).
_LAT_KEYS = frozenset(map(chr, _LAT_CYR))


def wake_on_name() -> str:
    """off (по умолчанию) | strict | loose. Неизвестное значение — off."""
    raw = str(os.environ.get("PRAXIS_WAKE_ON_NAME") or "").strip().casefold()
    return raw if raw in ("off", "strict", "loose") else "off"


def _needle_forms(name: str) -> set[str]:
    """Формы имени для loose-поиска: как есть + транслитерация латиницы.

    «h» перед гласной не даёт «х»: «Hélène» транслитерируется как «елена» —
    так её зовут по-русски, а не «хелена» (живое употребление важнее
    словарной транслитерации).
    """
    folded = _fold(name).strip()
    forms = {folded} if folded else set()
    if folded and all(ch in _LAT_KEYS or not ch.isalpha() for ch in folded):
        cyr = folded.translate(_LAT_CYR)
        forms.add(cyr)
        if cyr.startswith("х") and len(cyr) > 1 and cyr[1] in "аеиоуэюя":
            forms.add(cyr[1:])  # «хелена» → «елена» тоже будит
    return forms


def name_matches_loose(text: str, names) -> bool:
    """Имя как начало слова в любой форме. Короткие имена (<3) — только strict."""
    body = _fold(text)
    if not body:
        return False
    for name in names:
        if not str(name or "").strip() or str(name).startswith("@"):
            continue  # @username не склоняется — его ловит строгий матчинг
        for needle in _needle_forms(str(name)):
            if len(needle) < 3:
                continue  # «Ян» как префикс словил бы «январь» — не берём
            # Хвост: конец слова ИЛИ типовой падежный суффикс (гласная + до трёх
            # согласных/«й»: «-а», «-у», «-ом», «-ей»). «джарвисаппорт» не будит.
            if re.search(r"(?<![\w@])" + re.escape(needle)
                         + r"(?![а-яёa-z])|(?<![\w@])" + re.escape(needle)
                         + r"[аеиоуыэюя][б-щй]{0,3}(?![а-яёa-z])",
                         body, re.IGNORECASE):
                return True
    return False


def priority_prefix() -> str:
    """Приоритетный маркер: свой — PRAXIS_PRIORITY_PREFIX, «off/none/0» — выкл."""
    raw = str(os.environ.get("PRAXIS_PRIORITY_PREFIX") or "").strip()
    if raw.casefold() in ("off", "none", "0"):
        return ""
    return raw or "!"


def split_priority(text: str) -> tuple[bool, str]:
    """(приоритетно?, текст без префикса). Префикс — только в начале строки."""
    marker = priority_prefix()
    value = str(text or "")
    if not marker:
        return False, value
    stripped = value.lstrip()
    if stripped.startswith(marker) and len(stripped) > len(marker):
        rest = stripped[len(marker):].lstrip(" \u00a0")
        if rest:
            return True, rest
    return False, value


def priority_banner(who: str, text_no_prefix: str) -> str:
    """Баннер фокуса — первым элементом ориентира следующего хода."""
    gist = " ".join(str(text_no_prefix or "").split())[:200]
    return ("[ПРИОРИТЕТ — срочное действие] "
            f"Реплика от {str(who or '').strip() or 'отправителя'} помечена "
            "приоритетным префиксом и поднята в этот ход вне очереди: "
            f"«{gist}». Прочитай её как просьбу, требующую действия сейчас; "
            "не откладывай на окно и не своди к наблюдению.")
