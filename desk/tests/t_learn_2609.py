# -*- coding: utf-8 -*-
"""Вкладка-знакомство «Что поручить» (1.0.1) — адверсарное ревью 26.09 (W4).

На заявления вкладки не было ни одного стенда. Здесь прибито:
  * контраст текста не ниже 4,5:1 у пар, которые ревью нашло ниже нормы: пропуски рамок,
    цифры шагов, горящий шаг схемы, подпись «сон», кнопка рамки при наведении — в обеих
    темах, по токенам прямо из `tokens.css`;
  * CSS знакомства не задаёт голых классов, которыми окно рисует шаги хода (`step` в
    `steps.ts`): прежний `.step` перекрашивал их по всему окну;
  * вкладка не обещает того, чего нет: «Контекст» — теневая сборка, а не «ровно то, что
    видела модель»; сон — по часам компьютера и ждёт тишины; протухший ключ ловится после
    первого вызова; строка про Windows на Mac своя.

Запуск:  python tests/t_learn_2609.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
KIT = DESK / "ui-kit"
TOKENS = (KIT / "tokens.css").read_text(encoding="utf-8")
APP_CSS = (KIT / "window" / "styles" / "app.css").read_text(encoding="utf-8")
LEARN = (KIT / "window" / "views" / "learn.ts").read_text(encoding="utf-8")
STEPS = (KIT / "steps.ts").read_text(encoding="utf-8")
# Видимый текст: без комментариев (там законно цитируется то, что было неправдой).
# Строки TS рвутся конкатенацией `" +` — склеиваем, чтобы фразу было видно целиком.
LEARN = re.sub(r'"\s*\+\s*\n\s*"', "", LEARN)
LEARN_TEXT = re.sub(r"(?m)^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", LEARN, flags=re.S))


def _block(css: str, head: str) -> str:
    at = css.index(head)
    start = css.index("{", at) + 1
    depth, i = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    return css[start:i - 1]


def _tokens(block: str) -> dict[str, tuple[int, int, int]]:
    out = {}
    for name, value in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block):
        out[name] = tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    return out


THEMES = {"светлая": _tokens(_block(TOKENS, ":root {")),
          "тёмная": _tokens(_block(TOKENS, ':root[data-theme="dark"] {'))}


def mix(a, b, pa):
    return tuple(pa * x + (1 - pa) * y for x, y in zip(a, b))


def lum(c):
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def rule(selector: str) -> str:
    return _block(APP_CSS, selector + " {")


class Contrast(unittest.TestCase):
    def pairs(self, t):
        lrn_ink = mix(t["accent"], t["ink"], 0.78)
        return {
            "пропуск рамки (плейсхолдер)": (t["ink-2"], t["surface"]),
            "пропуск в фокусе": (t["ink-2"], mix(t["accent"], t["surface"], 0.10)),
            "цифра шага": (t["accent-ink"], lrn_ink),
            "горящий шаг схемы": (t["accent-ink"], lrn_ink),
            "подпись «сон» на подложке": (lrn_ink, t["surface"]),
            "кнопка рамки при наведении": (lrn_ink, mix(t["accent"], t["surface"], 0.14)),
        }

    def test_fixed_pairs_hold_4_5_in_both_themes(self):
        for theme, t in THEMES.items():
            for name, (fg, bg) in self.pairs(t).items():
                with self.subTest(theme=theme, pair=name):
                    self.assertGreaterEqual(round(ratio(fg, bg), 2), 4.5)

    def test_css_uses_the_measured_tokens(self):
        self.assertIn("color: var(--ink-2)", rule(".blank::placeholder"))
        self.assertIn("background: var(--lrn-ink)", rule(".lrn-step-num"))
        self.assertIn("background: var(--lrn-ink)", rule(".flow-step.is-lit .flow-num"))
        sleep = rule(".lrn-day-sleep span")
        self.assertIn("color: var(--lrn-ink)", sleep)
        self.assertIn("background: var(--surface)", sleep)
        self.assertIn("color-mix(in srgb, var(--accent) 14%, var(--surface))",
                      rule(".task-take:hover,\n.flow-go:hover"))


class NoCollisionWithRunSteps(unittest.TestCase):
    def test_learn_css_defines_no_bare_run_step_classes(self):
        start = APP_CSS.index("раздел «Что поручить»")
        learn_css = APP_CSS[start:]
        groups = [re.sub(r"\$\{[^}]*\}", " ", g) for g in re.findall(r'class="([^"]+)"', STEPS)]
        run_classes = {c for group in groups for c in group.split() if re.fullmatch(r"[\w-]+", c)}
        self.assertIn("step", run_classes, "условие: шаги хода носят класс step")
        for cls in sorted(run_classes):
            with self.subTest(cls=cls):
                self.assertIsNone(
                    re.search(rf"(?m)^\.{re.escape(cls)}(?![\w-])", learn_css),
                    f"знакомство задаёт голый .{cls} — перекрасит шаги хода")


class TabSaysOnlyWhatIsTrue(unittest.TestCase):
    def test_context_is_named_a_shadow_not_the_model_input(self):
        for lie in ("ровно то, что видела модель", "вызов за вызовом", "Кадр любого хода",
                    "разговор целиком"):
            self.assertNotIn(lie, LEARN_TEXT)
        self.assertIn("теневую сборку", LEARN)
        self.assertIn("в модель не уходит", LEARN)

    def test_sleep_is_local_clock_quiet_and_mute(self):
        self.assertNotIn("когда оно снова открыто", LEARN_TEXT)
        self.assertNotIn("пока окно открыто", LEARN_TEXT)
        self.assertIn("по часам этого компьютера", LEARN)
        self.assertIn("агент не отвечает", LEARN)
        self.assertIn("function sleepBand", LEARN)
        self.assertNotIn("left:${(4 / 24) * 100}%", LEARN, "полоса сна — по ручкам, не 4–6")

    def test_stale_key_and_platform_lines(self):
        self.assertNotIn("шапка не ловит", LEARN_TEXT)
        self.assertIn("после первого неудачного вызова", LEARN)
        self.assertIn("isMacPlatform(S.platform)", LEARN)
        self.assertIn("Запись экрана", LEARN)

    def test_remote_window_is_not_invited_to_type_into_itself(self):
        self.assertIn("function windowIsRead", LEARN)
        self.assertIn("Скопировать рамку", LEARN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
