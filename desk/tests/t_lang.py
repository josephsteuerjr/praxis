# -*- coding: utf-8 -*-
"""Каталоги языков: перевод не отстаёт от канона молча.

Запуск:  python tests/t_lang.py

⚠ ПОЧЕМУ ПРИБОР ОБЯЗАТЕЛЕН. Перевод живёт ровно до первой правки русской строки.
Кто-то поправит формулировку в `ru.json` — и три перевода начинают врать: показывают
старое обещание новым словом. Молчащий прибор здесь хуже отсутствующего, поэтому
у каждого переведённого ключа записан отпечаток русского оригинала (`stamps.json`),
и расхождение краснеет ИМЕНЕМ КЛЮЧА. Это тот же приём, что уже работает в дереве
агента для английских схем рук (`tool_text_en.py`).

Что проверяется:
  · канон (`ru.json`) — не пуст и без пустых строк;
  · у каждого языка нет ключей, которых нет в каноне (опечатка автора);
  · для каждого переведённого ключа отпечаток совпадает с текущим русским текстом;
  · перевод не равен русскому байт в байт (кроме имён собственных и цифр);
  · непереведённые ключи перечислены ЧИСЛОМ — это не ошибка, а известный долг.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANG = HERE.parent / "lang"
LANGS = ("en", "fr", "zh")


def _load(name: str) -> dict:
    path = LANG / f"{name}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def sha8(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


class Canon(unittest.TestCase):
    def test_the_canon_is_there_and_has_no_empty_strings(self):
        ru = _load("ru")
        self.assertTrue(ru, "канон пуст: переводить нечего, и прибор ничего не сторожит")
        empty = [k for k, v in ru.items() if not str(v).strip()]
        self.assertEqual(empty, [], "в каноне пустые строки")

    def test_keys_look_like_keys(self):
        ru = _load("ru")
        bad = [k for k in ru if not k or " " in k or k != k.lower()]
        self.assertEqual(bad, [], "ключи должны быть без пробелов и в нижнем регистре")


class Translations(unittest.TestCase):
    def test_no_stray_keys(self):
        ru = _load("ru")
        for code in LANGS:
            stray = sorted(set(_load(code)) - set(ru))
            self.assertEqual(stray, [], f"{code}.json: ключей нет в каноне — {stray[:5]}")

    def test_every_translated_key_carries_a_fresh_stamp(self):
        """Канон ушёл вперёд — прибор краснеет ИМЕНЕМ КЛЮЧА, а не общим «что-то не так»."""
        ru, stamps = _load("ru"), _load("stamps")
        stale = []
        for code in LANGS:
            for key in _load(code):
                want = sha8(ru.get(key, ""))
                got = (stamps.get(key) or {}).get(code)
                if got != want:
                    stale.append(f"{code}:{key}")
        self.assertEqual(stale, [], "перевод отстал от русского текста: " + ", ".join(stale[:8]))

    def test_a_translation_is_not_the_russian_string_again(self):
        ru = _load("ru")
        for code in LANGS:
            same = [k for k, v in _load(code).items()
                    if str(v).strip() == str(ru.get(k, "")).strip() and len(str(v)) > 12]
            self.assertEqual(same, [], f"{code}.json: строки остались русскими — {same[:5]}")

    def test_the_debt_is_counted_not_hidden(self):
        """Непереведённое — не ошибка, но оно обязано быть посчитанным."""
        ru = _load("ru")
        for code in LANGS:
            missing = sorted(set(ru) - set(_load(code)))
            # Пока перевод неполон, это печатается в отчёт стенда: долг виден числом.
            if missing:
                print(f"  {code}: непереведённых ключей {len(missing)} из {len(ru)}")


if __name__ == "__main__":
    unittest.main()
