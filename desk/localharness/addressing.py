# -*- coding: utf-8 -*-
"""Address an agent by its profile name, transliteration or priority prefix."""
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


# Латиница → кириллица для loose-формы имени: «the agent» должен ловить «агента».
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
            # согласных/«й»: «-а», «-у», «-ом», «-ей»). «атласаппорт» не будит.
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
