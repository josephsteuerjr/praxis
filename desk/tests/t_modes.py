# -*- coding: utf-8 -*-
"""Стенд режима (задачи R1/R2): ограда и служба — два независимых ответа.

Запуск:  python tests/t_modes.py

Главный сценарий здесь — тот самый P0: **служба стоит, `sandbox.enabled` = true,
режим в файле не записан**. До 04.09 миграция выводила из этого режим `service`,
а `service` означал «ограды нет» — песочница снималась МОЛЧА, при том что
владелец её выбирал. Проверяем не «функция возвращает строку», а именно этот
сценарий целиком: ограда осталась, служба видна отдельно.

Живьём здесь не ставится и не снимается ни одна служба: `service_installed`
спрашивают через параметр `installed=` — ровно тот шов, через который в продукт
приходит ответ SCM. Файлы пишутся во ВРЕМЕННУЮ папку и сносятся вместе с тестом.
Стенд лежит вне `localharness/`, потому что сборка поставки забирает оттуда ВСЕ
*.py (`installer/build_dist.py`), а тесты владельцу в папку программы не нужны.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import modes  # noqa: E402


class Migration(unittest.TestCase):
    """`infer` и `resolve` на конфигах, которые уже лежат у людей на диске."""

    def test_service_does_not_take_the_fence_away(self):
        # ⚠⚠ P0 целиком: служба установлена, ограда включена, режима в файле нет.
        cfg = {"sandbox": {"enabled": True, "network": True},
               "installed": {"service": True}}
        picture = modes.resolve(cfg, installed=True)
        self.assertEqual(picture["name"], "sandbox")
        self.assertTrue(picture["sandbox"], "служба сняла ограду — это и был P0")
        # Служба при этом видна — просто отдельным ответом, а не именем режима.
        self.assertIs(picture["service_installed"], True)
        self.assertNotIn("service", modes.MODES)
        # И раскладка по ручкам ограду тоже не снимает.
        modes.apply(cfg, installed=True)
        self.assertEqual(cfg["agent_mode"], "sandbox")
        self.assertIs(cfg["sandbox"]["enabled"], True)

    def test_service_without_fence_stays_interactive(self):
        cfg = {"sandbox": {"enabled": False}, "installed": {"service": True}}
        picture = modes.resolve(cfg, installed=True)
        self.assertEqual(picture["name"], "interactive")
        self.assertFalse(picture["sandbox"])
        self.assertIs(picture["service_installed"], True)

    def test_fence_is_inferred_the_same_with_and_without_service(self):
        # Ровно то свойство, ради которого измерения разведены: ответ про ограду
        # не зависит от службы НИ В ОДНОМ из четырёх сочетаний.
        for enabled in (True, False):
            for installed in (True, False, None):
                cfg = {"sandbox": {"enabled": enabled}}
                with self.subTest(enabled=enabled, installed=installed):
                    self.assertEqual(
                        modes.resolve(cfg, installed=installed)["sandbox"], enabled)

    def test_infer_takes_no_service_argument(self):
        # Параметр `installed=` у `infer` был дверью, через которую служба
        # входила в ответ про ограду. Двери больше нет — и это проверяется, а не
        # подразумевается.
        with self.assertRaises(TypeError):
            modes.infer({}, installed=True)          # type: ignore[call-arg]

    def test_old_config_without_any_mode(self):
        # Блока sandbox нет вовсе: ограда в `fence.install` включена по
        # умолчанию, значит такой конфиг СЕГОДНЯ живёт песочницей.
        picture = modes.resolve({"port": 8094}, installed=False)
        self.assertEqual(picture["name"], "sandbox")
        self.assertFalse(picture["explicit"])
        self.assertTrue(picture["needs_write"])
        self.assertIn("миграция", picture["source"])

    def test_empty_sandbox_block_falls_to_default(self):
        picture = modes.resolve({"sandbox": {}}, installed=False)
        self.assertEqual(picture["name"], modes.DEFAULT_MODE)


class LegacyServiceValue(unittest.TestCase):
    """`agent_mode: "service"` — то, что записала первая волна."""

    def test_service_value_is_not_a_fence(self):
        cfg = {"agent_mode": "service", "sandbox": {"enabled": True},
               "installed": {"service": True}}
        picture = modes.resolve(cfg, installed=True)
        self.assertTrue(picture["legacy_service"])
        # Ограду выводим отдельно — и она остаётся.
        self.assertEqual(picture["name"], "sandbox")
        self.assertTrue(picture["sandbox"])
        self.assertFalse(picture["explicit"])
        self.assertTrue(picture["needs_write"])
        self.assertTrue(any("режимом не является" in n
                            for n in picture["notes"]))

    def test_service_value_with_fence_off(self):
        cfg = {"agent_mode": "service", "sandbox": {"enabled": False}}
        picture = modes.resolve(cfg, installed=True)
        self.assertEqual(picture["name"], "interactive")
        self.assertTrue(picture["legacy_service"])

    def test_apply_repairs_the_key(self):
        cfg = {"agent_mode": "service", "sandbox": {"enabled": True}}
        modes.apply(cfg, installed=True)
        self.assertEqual(cfg["agent_mode"], "sandbox")
        self.assertNotEqual(cfg["agent_mode"], "service")

    def test_stated_hides_service_but_legacy_service_shows_it(self):
        cfg = {"agent_mode": "service"}
        self.assertEqual(modes.stated(cfg), ("", ""))
        self.assertEqual(modes.legacy_service(cfg), (True, "agent_mode"))

    def test_service_in_hijacked_mode_key(self):
        # Самый неудачный из старых файлов: и режим не режим, и ключ чужой.
        cfg = {"mode": "service", "sandbox": {"enabled": True}}
        picture = modes.resolve(cfg, installed=True)
        self.assertEqual(picture["name"], "sandbox")
        self.assertTrue(picture["legacy_service"])
        modes.apply(cfg, installed=True)
        self.assertEqual(cfg["mode"], "local")       # ключ отдан обратно
        self.assertEqual(cfg["agent_mode"], "sandbox")


class Stated(unittest.TestCase):
    """Явно записанная ограда — три формы, как их правят руками."""

    def test_string_form(self):
        self.assertEqual(modes.stated({"agent_mode": "interactive"}),
                         ("interactive", "agent_mode"))

    def test_object_form(self):
        cfg = {"agent_mode": {"name": "sandbox", "session0": True}}
        self.assertEqual(modes.stated(cfg), ("sandbox", "agent_mode.name"))
        self.assertTrue(modes.session0(cfg))

    def test_hijacked_mode_key_is_read_and_repaired(self):
        cfg = {"mode": "sandbox"}
        self.assertEqual(modes.stated(cfg), ("sandbox", "mode"))
        picture = modes.resolve(cfg, installed=False)
        self.assertTrue(picture["needs_write"])
        self.assertTrue(any("занят под местожительство" in n
                            for n in picture["notes"]))
        modes.apply(cfg, installed=False)
        self.assertEqual(cfg["mode"], "local")
        self.assertEqual(cfg["agent_mode"], "sandbox")

    def test_fence_wins_over_a_disagreeing_knob(self):
        cfg = {"agent_mode": "sandbox", "sandbox": {"enabled": False}}
        picture = modes.resolve(cfg, installed=False)
        self.assertTrue(picture["sandbox"])
        self.assertTrue(picture["needs_write"])
        self.assertTrue(any("побеждает режим" in n for n in picture["notes"]))


class TwoDoorsOfTheService(unittest.TestCase):
    """`session0` и `firewall` — две РАЗНЫЕ галочки, а не одна на два смысла."""

    def test_firewall_is_on_by_default(self):
        # Из-за одного общего ключа кнопка QR под службой была мертва по
        # умолчанию: узкое действие продукта требовало отдать агенту всю машину.
        cfg = {"agent_mode": "interactive", "installed": {"service": True}}
        picture = modes.resolve(cfg, installed=True)
        self.assertTrue(picture["firewall"])
        self.assertFalse(picture["session0"], "нулевая сессия обязана быть выкл.")

    def test_session0_off_does_not_turn_the_firewall_off(self):
        cfg = {"agent_mode": "sandbox", "service": {"session0": False}}
        picture = modes.resolve(cfg, installed=True)
        self.assertFalse(picture["session0"])
        self.assertTrue(picture["firewall"])

    def test_firewall_off_does_not_touch_session0(self):
        cfg = {"agent_mode": "sandbox",
               "service": {"session0": True, "firewall": False}}
        picture = modes.resolve(cfg, installed=True)
        self.assertTrue(picture["session0"])
        self.assertFalse(picture["firewall"])
        self.assertEqual(picture["session0_warning"], modes.SESSION0_WARNING)

    def test_both_are_words_without_the_service(self):
        cfg = {"agent_mode": "sandbox", "service": {"session0": True}}
        picture = modes.resolve(cfg, installed=False)
        self.assertFalse(picture["session0"])
        self.assertTrue(picture["session0_set"])
        self.assertFalse(picture["firewall"])
        self.assertTrue(any("служба не установлена" in n for n in picture["notes"]))

    def test_plain_config_without_service_is_quiet(self):
        # Умолчание `firewall = true` не должно давать строку тревоги каждому,
        # кто службу не ставил: это не расхождение, а обычная жизнь.
        picture = modes.resolve({"agent_mode": "interactive"}, installed=False)
        self.assertEqual(picture["notes"], [])

    def test_firewall_off_under_a_service_is_named(self):
        cfg = {"agent_mode": "interactive", "service": {"firewall": False}}
        notes = " ".join(modes.resolve(cfg, installed=True)["notes"])
        self.assertIn("«Телефон»", notes)

    def test_session0_lives_in_any_fence(self):
        # Ограда и права системы независимы: песочница со службой и нулевой
        # сессией — законное сочетание, а не «одно вместо другого».
        for name in modes.MODES:
            cfg = {"agent_mode": name, "service": {"session0": True}}
            with self.subTest(mode=name):
                self.assertTrue(modes.resolve(cfg, installed=True)["session0"])

    def test_apply_moves_session0_to_the_canon_and_keeps_choice(self):
        cfg = {"agent_mode": {"name": "sandbox", "session0": True},
               "service": {"firewall": False, "broker": False}}
        modes.apply(cfg, installed=True)
        self.assertIs(cfg["service"]["session0"], True)   # переехала в канон
        self.assertIs(cfg["service"]["firewall"], False)  # выбор не затёрт
        self.assertIs(cfg["service"]["broker"], False)    # чужая ручка цела

    def test_apply_writes_the_firewall_default_once(self):
        cfg = {"sandbox": {"enabled": True}}
        modes.apply(cfg, installed=True)
        self.assertIs(cfg["service"]["firewall"], modes.FIREWALL_DEFAULT)


class Truth(unittest.TestCase):
    """Тексты, которые владелец читает как обещание продукта."""

    def test_service_text_does_not_promise_life_without_login(self):
        # Было: «агент живёт, пока включён компьютер». Неправда: без нулевой
        # сессии харнесс поднимается В СЕССИИ ВЛАДЕЛЬЦА (svc::supervise_session).
        text = modes.SERVICE_TEXT
        self.assertNotIn("пока включён компьютер", text)
        self.assertIn("вошёл в систему", text)

    def test_service_text_names_admins_as_writers(self):
        # Было: «папка закрыта на запись всем, кроме системы». Неправда:
        # администраторам запись выдаётся намеренно (harden_args: *S-1-5-32-544:F).
        self.assertIn("администратор", modes.SERVICE_TEXT)
        self.assertNotIn("всем, кроме системы", modes.SERVICE_TEXT)

    def test_service_text_says_the_fence_is_separate(self):
        self.assertIn("Ограду это не меняет", modes.SERVICE_TEXT)

    def test_notes_point_at_settings_not_at_system_screen(self):
        # Кнопки службы живут в Настройках, карточка «Режим». Указание на экран
        # «Система» вело владельца не туда.
        cfg = {"agent_mode": "sandbox", "service": {"session0": True}}
        notes = " ".join(modes.resolve(cfg, installed=False)["notes"])
        self.assertIn("Настройках", notes)
        self.assertNotIn("«Система»", notes)

    def test_catalogue_has_two_fences_and_no_admin(self):
        cards = modes.catalogue()
        self.assertEqual([c["name"] for c in cards], list(modes.MODES))
        self.assertEqual([c["name"] for c in cards], ["sandbox", "interactive"])
        for card in cards:
            self.assertFalse(card["needs_admin"], "ограда админа не требует")
            self.assertTrue(card["title"] and card["text"])

    def test_service_option_carries_both_toggles(self):
        option = modes.service_option()
        self.assertTrue(option["needs_admin"])
        keys = [t["key"] for t in option["toggles"]]
        self.assertEqual(keys, ["service.session0", "service.firewall"])
        by_key = {t["key"]: t for t in option["toggles"]}
        self.assertIs(by_key["service.session0"]["default"], False)
        self.assertIs(by_key["service.firewall"]["default"], True)
        self.assertEqual(by_key["service.session0"]["warning"],
                         modes.SESSION0_WARNING)


class Describe(unittest.TestCase):
    """Срез для окна: два ответа рядом, ни одного склеенного."""

    def test_shape_has_fence_and_service_apart(self):
        cfg = {"sandbox": {"enabled": True}, "service": {"session0": True}}
        out = modes.describe(modes.resolve(cfg, installed=True))
        for key in ("name", "title", "text", "sandbox", "service_installed",
                    "service_title", "service_text", "session0", "session0_set",
                    "session0_warning", "firewall", "firewall_set",
                    "legacy_service", "notes", "source", "explicit"):
            self.assertIn(key, out)
        self.assertEqual(out["name"], "sandbox")
        self.assertIs(out["service_installed"], True)
        self.assertTrue(out["session0"])

    def test_describe_survives_an_empty_picture(self):
        out = modes.describe({})
        self.assertEqual(out["name"], modes.DEFAULT_MODE)
        self.assertTrue(out["text"])


class Platform(unittest.TestCase):
    """Порт без службы и тела (macOS): секций нет в картине, а не «нет» словами.

    Флаги `HAS_SERVICE`/`HAS_COMPUTER` подменяются, чтобы разобрать обе
    картины на любой машине; на Windows картина обязана остаться прежней.
    """

    def setUp(self):
        self.saved = (modes.HAS_SERVICE, modes.HAS_COMPUTER)

    def tearDown(self):
        modes.HAS_SERVICE, modes.HAS_COMPUTER = self.saved

    def test_flags_follow_the_platform(self):
        import importlib
        fresh = importlib.reload(modes)
        self.assertEqual(fresh.HAS_SERVICE, os.name == "nt")
        self.assertEqual(fresh.HAS_COMPUTER, os.name == "nt")

    def test_without_a_service_the_section_is_empty_but_the_fence_is_whole(self):
        modes.HAS_SERVICE = False
        # Конфиг, приехавший с Windows: и след установщика, и обе галочки.
        cfg = {"agent_mode": "sandbox", "installed": {"service": True},
               "service": {"session0": True, "firewall": False}}
        picture = modes.resolve(cfg)
        self.assertEqual(picture["name"], "sandbox")
        self.assertTrue(picture["sandbox"], "ограда от службы не зависит")
        self.assertFalse(picture["service_here"])
        self.assertIsNone(picture["service_installed"], "след установщика ничего не значит")
        self.assertEqual(picture["service_title"], "")
        self.assertEqual(picture["service_text"], "")
        self.assertFalse(picture["session0"], "нулевую сессию некому дать")
        self.assertFalse(picture["firewall"])
        self.assertTrue(picture["session0_set"], "что записано в файле — факт файла")
        self.assertEqual(picture["notes"], [], "тревог о службе быть не должно")
        self.assertEqual(picture["session0_warning"], "")
        described = modes.describe(picture)
        self.assertFalse(described["service_here"])
        self.assertEqual(described["service_title"], "")
        self.assertEqual(described["service_text"], "")
        self.assertIsNone(modes.service_installed(cfg))

    def test_explicit_scm_answer_is_still_the_seam(self):
        # Стенды и Windows приносят ответ SCM явно — ему верим как есть.
        modes.HAS_SERVICE = False
        picture = modes.resolve({"agent_mode": "sandbox", "service": {"session0": True}},
                                installed=True)
        self.assertTrue(picture["service_here"])
        self.assertTrue(picture["session0"])
        self.assertEqual(picture["service_title"], modes.SERVICE_TITLE)

    def test_windows_picture_is_untouched(self):
        modes.HAS_SERVICE = True
        cfg = {"agent_mode": "sandbox", "service": {"session0": True}}
        picture = modes.resolve(cfg, installed=False)
        self.assertTrue(picture["service_here"])
        self.assertEqual(picture["service_title"], modes.SERVICE_TITLE)
        self.assertTrue(any("служба не установлена" in n for n in picture["notes"]))
        self.assertEqual(modes.describe(picture)["service_text"], modes.SERVICE_TEXT)

    def test_pipe_drops_both_sections_where_there_is_no_platform_for_them(self):
        sys.path.insert(0, str(HERE.parent))
        import deskd.readers as readers          # noqa: PLC0415
        modes.HAS_SERVICE = False
        modes.HAS_COMPUTER = False
        with tempfile.TemporaryDirectory(prefix="helene-modes-") as tmp:
            cfg_path = Path(tmp) / "helene.json"
            cfg_path.write_text(json.dumps({"agent_mode": "sandbox", "tree": "data",
                                            "computer": {"enabled": True}}),
                                encoding="utf-8")
            with patch.dict(os.environ, {"HELENE_CONFIG": str(cfg_path),
                                         "HELENE_TREE": str(Path(tmp) / "data")}):
                picture = readers.mode_state()
        self.assertEqual(picture["name"], "sandbox")
        self.assertEqual(picture["choices"], modes.catalogue(), "ограды есть на любой платформе")
        self.assertIsNone(picture["service"])
        self.assertIsNone(picture["computer"])
        self.assertIsNone(picture["computer_option"])
        self.assertEqual(picture["computer_live"], {})
        self.assertFalse(picture["service_here"])
        # И ни одного слова про платформу на экран.
        text = json.dumps(picture, ensure_ascii=False).lower()
        self.assertNotIn("macos", text)
        self.assertNotIn("платформ", text)

    def test_pipe_keeps_both_sections_on_windows(self):
        sys.path.insert(0, str(HERE.parent))
        import deskd.readers as readers          # noqa: PLC0415
        modes.HAS_SERVICE = True
        modes.HAS_COMPUTER = True
        with tempfile.TemporaryDirectory(prefix="helene-modes-") as tmp:
            cfg_path = Path(tmp) / "helene.json"
            cfg_path.write_text(json.dumps({"agent_mode": "sandbox", "tree": "data"}),
                                encoding="utf-8")
            with patch.dict(os.environ, {"HELENE_CONFIG": str(cfg_path),
                                         "HELENE_TREE": str(Path(tmp) / "data")}):
                picture = readers.mode_state()
        self.assertEqual(picture["service"], modes.service_option())
        self.assertEqual(picture["computer_option"], modes.computer_option())
        self.assertEqual(picture["computer"]["enabled"], False)

    def test_journal_says_one_line_without_a_service(self):
        modes.HAS_SERVICE = False
        picture = modes.resolve({"agent_mode": "interactive"})
        with self.assertLogs("helene.modes", level="INFO") as caught:
            modes.journal(picture, where="helene.json")
        said = "\n".join(caught.output)
        self.assertIn("на этой платформе её нет", said)
        self.assertNotIn("нулевая сессия", said)


def _plugin_dict(src: str, name: str, keys: list[str]) -> dict[str, str]:
    """Разбор словаря так, как его читает сборка установщика.

    Зеркало `dictValues` + `joinLiterals` из `setup/ui/vite.config.ts`: словарь
    верхнего уровня от `{` до `}` в первой колонке, запись — от `"ключ":` до
    следующего ключа, значение — все строковые литералы куска подряд. Если
    Python и это зеркало прочитают разное, плагин соберёт установщик не с тем
    текстом, что отдаёт канал.
    """
    import re
    at = re.search(rf"^{name}\s*(?::[^=\n]*)?=\s*\{{", src, re.M)
    if not at:
        raise AssertionError(f"modes.py: не нашёл словарь {name}")
    open_ = src.index("{", at.start())
    close = src.index("\n}", open_)
    body = src[open_ + 1:close]
    out: dict[str, str] = {}
    for key in keys:
        head = f'"{key}":'
        start = body.index(head)
        end = len(body)
        for other in keys:
            if other == key:
                continue
            pos = body.find(f'"{other}":')
            if start < pos < end:
                end = pos
        chunk = body[start + len(head):end]
        parts = []
        for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', chunk):
            try:
                parts.append(json.loads(f'"{literal}"'))
            except ValueError:
                parts.append(literal)
        if not parts:
            raise AssertionError(f"modes.py: нет текста в {name}[{key!r}]")
        out[key] = "".join(parts)
    return out


class MacTexts(unittest.TestCase):
    """Тексты оград для macOS: та же форма, что у TEXTS, без обещаний Windows.

    Общие тексты обещают опцию «Управление компьютером» и окно прав Windows —
    в порте их нет. Словарь-двойник обязан совпадать по ключам, читаться
    сборкой установщика по той же форме и уезжать в картину на darwin.
    """

    FORBIDDEN = ("windows", "appcontainer", "управление компьютером", "uac", "macos")

    def test_keys_and_shape_match_texts(self):
        self.assertEqual(set(modes.TEXTS_MACOS), set(modes.TEXTS))
        self.assertEqual(set(modes.TEXTS_MACOS), set(modes.MODES))
        for name in modes.MODES:
            self.assertIsInstance(modes.TEXTS_MACOS[name], str)
            self.assertTrue(modes.TEXTS_MACOS[name].strip(), name)
            self.assertNotEqual(modes.TEXTS_MACOS[name], modes.TEXTS[name],
                                "двойник без разницы — не двойник")

    def test_no_windows_promises_and_no_platform_words(self):
        for name, text in modes.TEXTS_MACOS.items():
            low = text.lower()
            for word in self.FORBIDDEN:
                self.assertNotIn(word, low, f"{name}: «{word}» в тексте для macOS")
            self.assertNotIn("этого нет", low, name)
        # Ограда названа по механизму, а не по чужому.
        self.assertIn("seatbelt", modes.TEXTS_MACOS["sandbox"])
        self.assertIn("Forge", modes.TEXTS_MACOS["sandbox"])
        self.assertIn("монтировать", modes.TEXTS_MACOS["interactive"])

    def test_installer_plugin_reads_the_same_strings(self):
        src = (HERE.parent / "localharness" / "modes.py").read_text(encoding="utf-8")
        self.assertEqual(_plugin_dict(src, "TEXTS_MACOS", list(modes.MODES)), modes.TEXTS_MACOS)
        # Зеркало верно и на общем словаре, который плагин читает с самого начала.
        self.assertEqual(_plugin_dict(src, "TEXTS", list(modes.MODES)), modes.TEXTS)
        self.assertEqual(_plugin_dict(src, "TITLES", list(modes.MODES)), modes.TITLES)

    def test_darwin_picture_takes_the_mac_texts(self):
        with patch.object(sys, "platform", "darwin"):
            self.assertIs(modes.texts(), modes.TEXTS_MACOS)
            picture = modes.resolve({"agent_mode": "sandbox"}, installed=None)
            self.assertEqual(picture["text"], modes.TEXTS_MACOS["sandbox"])
            self.assertEqual(modes.describe(picture)["text"], modes.TEXTS_MACOS["sandbox"])
            self.assertEqual(modes.describe({})["text"], modes.TEXTS_MACOS[modes.DEFAULT_MODE])
            self.assertEqual([c["text"] for c in modes.catalogue()],
                             [modes.TEXTS_MACOS[n] for n in modes.MODES])
            self.assertEqual([c["title"] for c in modes.catalogue()],
                             [modes.TITLES[n] for n in modes.MODES])

    def test_windows_picture_is_untouched(self):
        with patch.object(sys, "platform", "win32"):
            self.assertIs(modes.texts(), modes.TEXTS)
            picture = modes.resolve({"agent_mode": "interactive"}, installed=False)
            self.assertEqual(picture["text"], modes.TEXTS["interactive"])
            self.assertEqual([c["text"] for c in modes.catalogue()],
                             [modes.TEXTS[n] for n in modes.MODES])


class PipeShape(unittest.TestCase):
    """Аварийный ответ трубы обязан быть той же формы, что удачный.

    Окно читает поля `/api/mode` без проверок: недостающий ключ в аварийной
    ветке — это не «пустой экран», а сломанный экран. Копий формы две
    (`modes.describe` и `readers._MODE_UNKNOWN`), и разойтись им нельзя.
    """

    def setUp(self):
        sys.path.insert(0, str(HERE.parent))
        import deskd.readers as readers          # noqa: PLC0415
        self.readers = readers

    def test_unknown_covers_every_field_of_describe(self):
        full = set(modes.describe(modes.resolve({}, installed=None)))
        full |= {"choices", "service", "config"}
        missing = full - set(self.readers._MODE_UNKNOWN)
        self.assertEqual(missing, set(),
                         f"в аварийном ответе трубы нет полей: {sorted(missing)}")

    def test_unknown_names_no_mode(self):
        out = self.readers.mode_unknown("модуль режима не нашёлся")
        self.assertEqual(out["name"], "")
        self.assertIn("модуль режима не нашёлся", out["notes"])


class OnDisk(unittest.TestCase):
    """`ensure_written`: файл владельца, а не словарь в памяти."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-modes-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "helene.json"

    def write(self, cfg: dict, *, encoding: str = "utf-8") -> None:
        self.path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                             encoding=encoding)

    def read(self) -> dict:
        return json.loads(modes.read_config_text(self.path))

    def test_service_install_keeps_the_fence_on_disk(self):
        # Тот же P0, но через файл: именно так он и доезжал до владельца.
        self.write({"mode": "local", "sandbox": {"enabled": True},
                    "installed": {"service": True}})
        picture = modes.ensure_written(self.path, installed=True)
        self.assertTrue(picture["written"])
        cfg = self.read()
        self.assertEqual(cfg["agent_mode"], "sandbox")
        self.assertIs(cfg["sandbox"]["enabled"], True)
        self.assertEqual(cfg["mode"], "local")       # местожительство не тронуто
        self.assertIs(cfg["service"]["session0"], False)
        self.assertIs(cfg["service"]["firewall"], True)

    def test_legacy_service_value_is_repaired_on_disk(self):
        self.write({"agent_mode": "service", "sandbox": {"enabled": True},
                    "service": {"session0": True}})
        modes.ensure_written(self.path, installed=True)
        cfg = self.read()
        self.assertEqual(cfg["agent_mode"], "sandbox")
        self.assertIs(cfg["sandbox"]["enabled"], True)
        self.assertIs(cfg["service"]["session0"], True)  # выбор владельца цел

    def test_already_settled_config_is_not_rewritten(self):
        self.write({"agent_mode": "interactive", "sandbox": {"enabled": False},
                    "service": {"session0": False, "firewall": True}})
        before = self.path.read_bytes()
        picture = modes.ensure_written(self.path, installed=False)
        self.assertFalse(picture["written"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_bom_config_is_read(self):
        # Блокнот и PowerShell 5.1 пишут UTF-8 с меткой; строгий utf-8 молча
        # возвращал пустоту, и режим выводился на пустом конфиге.
        self.write({"agent_mode": "sandbox", "sandbox": {"enabled": True}},
                   encoding="utf-8-sig")
        picture = modes.ensure_written(self.path, installed=False)
        self.assertEqual(picture["name"], "sandbox")
        self.assertIsNone(picture.get("error"))

    def test_broken_config_does_not_raise(self):
        self.path.write_text("{ это не json", encoding="utf-8")
        picture = modes.ensure_written(self.path, installed=False)
        self.assertFalse(picture["written"])
        self.assertIn("не прочитан", picture["error"])
        self.assertIn(picture["name"], modes.MODES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
