# -*- coding: utf-8 -*-
"""Живой стенд тела на macOS: контракт тела (план 19.09, §2) через движок.

Запуск:  HELENE_BODY_DIR=<папка с helene-bridge и helene-body> python tests/t_body_macos.py
         (на раннере — рантаймом сборки: HELENE_BODY_DIR=$BUILD/Helene, шаг
          «Тело — живые стенды рантаймом сборки» в .github/workflows/macos.yml)

Что здесь. Поднимаются НАСТОЯЩИЕ мост и тело тем же путём, что у движка
(`body.launch` из `localharness/body.py`), ждём `connected`, затем через мост
(`body.call` — та же дорога, что у сторожа) спрашиваем то, что обещает контракт,
и сверяем форму ответа:

  desktop.status          ok, platform == "macos", tcc — два булевых, hints — список
  desktop.screen.capture  ok + PNG > 10 КБ + scale — ИЛИ ok:false + hints с «Запись экрана»
  desktop.window.list     ≥ 1 строка с hwnd, pid, rect (формы те же, что у Windows)
  os.process.list         среди процессов есть helene или python3
  desktop.clipboard.*     write → read туда-обратно (без разрешения — честный отказ)
  desktop.window.read     окно Helene по pid из списка: дерево с узлами при
                          «Универсальном доступе», иначе отказ с этими словами
  desktop.window.activate ok ТОЛЬКО когда переднее окно стало запрошенным;
                          иначе — отказ рамки со словами и с waited_ms
  desktop.input.perform   безвредная пачка shift вниз/вверх; одинокий `cmd down`
                          отпускается сам и называется в modifiers_auto_released

Стенд различает «нет разрешения» и «сломано». TCC (`tcc.screen_recording`,
`tcc.accessibility`) читается из desktop.status ДО проб, и ожидание ставится по
нему: без разрешения — честный отказ словами (печатается, стенд не падает); с
разрешением — настоящая проверка. Ложь ловится в обе стороны: «ok» без
разрешения — падение (это обои, а не снимок; §2: «отказ словами, не ложь»),
отказ при разрешении — тоже падение (сломано, а не запрещено).

На Windows и без бинарей стенд пропускается словами, а не зелёным: причина
печатается и стоит в skip каждого теста.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import body  # noqa: E402

DARWIN = sys.platform == "darwin"
#: Не 9480 (умолчание продукта: там мост установленной программы) и не 9490
#: (t_body.Live): свой мост на своём порту, pick_port обойдёт занятые.
PORT = 9500
CONNECT_SEC = 45
CALL_SEC = 20
READ_SEC = 40
#: Порог снимка: пустой PNG экрана без окон — единицы килобайт (дымовой шаг
#: workflow ловит то же число у `screencapture`).
PNG_MIN_BYTES = 10 * 1024

SCREEN_WORDS = ("Запись экрана", "Screen Recording")
AX_WORDS = ("Универсальный доступ", "Accessibility")


def _binaries() -> Path | None:
    """Папка с мостом и телом — только из HELENE_BODY_DIR: стенд живой, гадать
    по соседям нечего. Имена — какие объявляет движок на этой платформе, плюс
    имена крейтов на случай сырой сборки cargo."""
    said = (os.environ.get("HELENE_BODY_DIR") or "").strip()
    if not said:
        return None
    base = Path(said)
    declared = (str(getattr(body, "BRIDGE_EXE", "helene-bridge")),
                str(getattr(body, "BODY_EXE", "helene-body")))
    for pair in (declared, ("helene-bridge", "helene-body"), ("praxis-bridge", "praxis-body")):
        if all((base / name).is_file() for name in pair):
            return base
    return None


def _why_skipped() -> str:
    if not DARWIN:
        return f"живое тело macOS — только на macOS (здесь {sys.platform})"
    if _binaries() is None:
        return ("мост и тело не найдены: нужен HELENE_BODY_DIR с helene-bridge и helene-body "
                f"(HELENE_BODY_DIR={os.environ.get('HELENE_BODY_DIR') or 'не задан'})")
    return ""


SKIP = _why_skipped()


def _text(answer: dict) -> str:
    """Всё, чем тело могло объяснить отказ, одной строкой: hints, error, message, reason, note."""
    parts: list[str] = []
    for key in ("hints", "error", "message", "reason", "note", "code"):
        value = answer.get(key)
        if isinstance(value, (list, tuple)):
            parts += [str(v) for v in value]
        elif value:
            parts.append(str(value))
    return " | ".join(parts)


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def _hwnd_ok(value) -> bool:
    """`hwnd` — CGWindowID: число или строка `0x…` (HwndArg, как на Windows)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and value.lower().startswith("0x") and len(value) > 2


@unittest.skipIf(bool(SKIP), SKIP)
class Contract(unittest.TestCase):
    """Один подъём на класс: мост и тело поднимаются секунды, а проб шесть."""

    tmp: tempfile.TemporaryDirectory
    root: Path
    tree: Path
    status: dict
    tcc: dict
    windows: list

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="helene-body-mac-", ignore_cleanup_errors=True)
        cls.root = Path(cls.tmp.name) / "Helene"
        cls.tree = cls.root / "data"
        (cls.tree / "memory" / ".state").mkdir(parents=True)
        cfg = {"agent_mode": "sandbox", "computer": {"enabled": True, "port": PORT}}
        (cls.root / "helene.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        os.environ["HELENE_BODY_DIR"] = str(_binaries())
        if not getattr(body, "HAS_BODY", False):
            raise AssertionError("движок не знает тела на macOS: body.HAS_BODY = False — "
                                 "порт движка (localharness/body.py) до этой ветки не доехал")
        started = body.launch(cls.root, cls.tree, cfg)
        if started is None:
            raise AssertionError(f"тело не поднялось: {body.STATE.get('reason')}")
        deadline = time.monotonic() + CONNECT_SEC
        while time.monotonic() < deadline and body.STATE.get("connected") is not True:
            time.sleep(0.5)
        if body.STATE.get("connected") is not True:
            tails = cls._tails()
            body.shutdown()
            raise AssertionError(f"тело не подключилось к мосту за {CONNECT_SEC} с: "
                                 f"{body.STATE.get('reason')}\n{tails}")
        status = body.call("desktop.status", {}, timeout=CALL_SEC)
        if not status.get("ok"):
            tails = cls._tails()
            body.shutdown()
            raise AssertionError(f"desktop.status не ответил ok: {status}\n{tails}")
        cls.status = status
        cls.tcc = status.get("tcc") if isinstance(status.get("tcc"), dict) else {}
        cls.windows = []
        print(f"\nтело: мост 127.0.0.1:{body.STATE.get('port')}, {body.STATE.get('reason')}")
        print(f"tcc: {json.dumps(cls.tcc, ensure_ascii=False)}; hints: "
              f"{json.dumps(status.get('hints'), ensure_ascii=False)}")

    @classmethod
    def tearDownClass(cls):
        body.shutdown()
        time.sleep(0.5)
        cls.tmp.cleanup()

    @classmethod
    def _tails(cls, lines: int = 40) -> str:
        out = []
        for name in ("bridge.log", "body.log"):
            path = cls.tree / "body" / name
            try:
                tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            except OSError:
                tail = ["(нет файла)"]
            out.append(f"=== {path}\n" + "\n".join(tail))
        return "\n".join(out)

    def call(self, capability: str, args: dict, timeout: float = CALL_SEC) -> dict:
        answer = body.call(capability, args, timeout=timeout)
        self.assertIsInstance(answer, dict, capability)
        return answer

    # ---- desktop.status --------------------------------------------------- #

    def test_1_status_names_platform_tcc_and_hints(self):
        s = self.status
        self.assertEqual(s.get("platform"), "macos", s)
        self.assertIsInstance(s.get("tcc"), dict, "в desktop.status нет tcc")
        for key in ("screen_recording", "accessibility"):
            self.assertIsInstance(s["tcc"].get(key), bool, f"tcc.{key} — не булево: {s['tcc']}")
        hints = s.get("hints")
        self.assertIsInstance(hints, list, "hints — не список")
        self.assertTrue(all(isinstance(h, str) for h in hints), hints)
        # Подсказки — ровно по отсутствующим разрешениям, словами про них.
        if not s["tcc"]["screen_recording"]:
            self.assertTrue(any(_mentions(h, SCREEN_WORDS) for h in hints),
                            f"нет «Записи экрана», а подсказки о ней нет: {hints}")
        if not s["tcc"]["accessibility"]:
            self.assertTrue(any(_mentions(h, AX_WORDS) for h in hints),
                            f"нет «Универсального доступа», а подсказки о нём нет: {hints}")
        if s["tcc"]["screen_recording"] and s["tcc"]["accessibility"]:
            self.assertEqual(hints, [], "оба разрешения есть, а подсказки остались")
        self.assertIn("session_id", s, "session_id обязан быть (null на macOS)")
        self.assertIsNone(s.get("session_id"), s.get("session_id"))
        self.assertIsInstance(s.get("interactive"), bool, s.get("interactive"))
        scale = s.get("scale")
        self.assertIsInstance(scale, (int, float), f"scale — не число: {scale!r}")
        self.assertGreaterEqual(float(scale), 1.0, scale)
        # Сторож движка увидел то же тело: снимок для окна написан и говорит «подключено».
        # Снимок пишет сторож раз в несколько секунд ПОСЛЕ своей пробы — сразу после
        # подъёма там ещё «подключается» (так упал выпускной прогон 0.8.1 при живом теле).
        # Ждём его слова до 20 с; «не дождались» — тогда и падаем, с последним снимком.
        snap_path = self.tree / "memory" / ".state" / "body.json"
        snap: dict = {}
        for _ in range(40):
            try:
                snap = json.loads(snap_path.read_text("utf-8"))
            except (OSError, ValueError):
                snap = {}
            if snap.get("connected") is True:
                break
            time.sleep(0.5)
        self.assertTrue(snap.get("connected"), f"сторож так и не записал «подключено» за 20 с: {snap}")
        self.assertEqual((body.STATE.get("identity") or {}).get("kind"), "interactive",
                         "тело без графической сессии: %s" % body.STATE.get("identity"))
        # Разрешения в снимок кладёт сторож своей пробой desktop.status — не чаще
        # неё; сразу после подключения там может быть ещё null. Сверяем, когда есть.
        if isinstance(snap.get("tcc"), dict):
            self.assertEqual(snap["tcc"], s["tcc"], "снимок сторожа расходится с desktop.status по tcc")
            self.assertIsInstance(snap.get("hints"), list, snap.get("hints"))
        else:
            print("  снимок body.json пока без tcc (сторож ещё не спрашивал desktop.status) — "
                  f"tcc={snap.get('tcc')!r}")

    # ---- desktop.screen.capture ------------------------------------------- #

    def test_2_screen_capture_is_honest(self):
        r = self.call("desktop.screen.capture", {})
        if self.tcc.get("screen_recording"):
            self.assertTrue(r.get("ok"), f"«Запись экрана» есть, а снимок не снялся: {r}")
            path = Path(str(r.get("path") or ""))
            self.assertTrue(path.is_file(), f"в ответе нет файла снимка: {r}")
            raw = path.read_bytes()
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n", "снимок — не PNG")
            self.assertGreater(len(raw), PNG_MIN_BYTES,
                               f"снимок подозрительно мал ({len(raw)} байт) — экран без окон?")
            for key in ("scale", "pixel_width", "pixel_height", "width", "height"):
                self.assertIsInstance(r.get(key), (int, float), f"в ответе снимка нет {key}: {r}")
            self.assertGreaterEqual(r["pixel_width"], r["width"], "пикселей меньше, чем пунктов")
            print(f"  снимок: {len(raw)} байт, {r['width']}×{r['height']} пт, "
                  f"{r['pixel_width']}×{r['pixel_height']} px, scale {r['scale']}")
        else:
            # Без разрешения снимок — обои без чужих окон; контракт велит отказать.
            self.assertFalse(r.get("ok"), f"«Записи экрана» нет, а снимок «ok» — это обои, не снимок: {r}")
            said = _text(r)
            self.assertTrue(_mentions(said, SCREEN_WORDS),
                            f"отказ без слов про «Запись экрана»: {r}")
            print(f"  [TCC] нет «Записи экрана» — снимок отказан словами: {said[:200]}")

    # ---- desktop.window.list ---------------------------------------------- #

    def test_3_window_list_has_rows_in_the_windows_shape(self):
        r = self.call("desktop.window.list", {"limit": 100})
        self.assertTrue(r.get("ok"), r)
        rows = r.get("items")
        self.assertIsInstance(rows, list, f"нет items в ответе списка окон: {r}")
        self.assertGreaterEqual(len(rows), 1, "ни одного окна — на раннере открыт хотя бы Finder")
        for row in rows:
            self.assertTrue(_hwnd_ok(row.get("hwnd")), f"hwnd не CGWindowID: {row}")
            self.assertIsInstance(row.get("pid"), int, f"pid — не число: {row}")
            rect = row.get("rect")
            self.assertIsInstance(rect, dict, f"rect — не словарь: {row}")
            for key in ("left", "top", "right", "bottom", "width", "height"):
                self.assertIn(key, rect, f"в rect нет {key}: {row}")
            self.assertIn("visible", row, row)
            self.assertIn("z_order", row, row)
        type(self).windows = rows
        titled = sum(1 for row in rows if row.get("title"))
        print(f"  окон: {len(rows)}, с заголовком: {titled}"
              + ("" if self.tcc.get("screen_recording") else " (без «Записи экрана» заголовки чужих окон — null)"))
        if not self.tcc.get("screen_recording") and titled == 0:
            self.assertTrue(any(row.get("note") for row in rows),
                            "заголовков нет и ни одна строка не говорит почему (note)")

    # ---- os.process.list -------------------------------------------------- #

    def test_4_process_list_sees_us(self):
        # Первая страница без фильтра — это pid по возрастанию, и на раннере 500
        # строк кончаются раньше наших процессов (четвёртый круг CI). Ищем по
        # имени, как делает дерево: `name_contains` смотрит и в имя, и в путь.
        r = self.call("os.process.list", {"limit": 50})
        self.assertTrue(r.get("ok"), r)
        rows = r.get("items")
        self.assertIsInstance(rows, list, r)
        self.assertTrue(rows, r)
        for row in rows:
            self.assertIsInstance(row.get("pid"), int, row)
        ours = []
        for needle in ("helene", "python"):
            f = self.call("os.process.list", {"limit": 50, "name_contains": needle})
            self.assertTrue(f.get("ok"), f)
            for row in f.get("items") or []:
                name = str(row.get("name") or "").lower()
                path = str(row.get("path") or "").lower()
                self.assertTrue(needle in name or needle in path, f"{needle!r} мимо фильтра: {row}")
                ours.append(name or path)
        self.assertTrue(ours, f"по фильтру не нашлись ни helene, ни python: всего процессов {r.get('total')}")
        print(f"  процессов: {r.get('total')}, свои по фильтру: {sorted(set(ours))[:6]}")

    # ---- clipboard -------------------------------------------------------- #

    def test_5_clipboard_round_trip_or_honest_refusal(self):
        text = f"helene-body-macos {secrets.token_hex(4)}"
        w = self.call("desktop.clipboard.write", {"text": text})
        r = self.call("desktop.clipboard.read", {})
        if w.get("ok") and r.get("ok"):
            self.assertTrue(r.get("available", True), r)
            self.assertEqual(r.get("text"), text, f"в буфере не то, что писали: {r}")
            print("  буфер обмена: туда-обратно сходится")
        else:
            said = _text(w) + " " + _text(r)
            self.assertTrue(_mentions(said, SCREEN_WORDS + AX_WORDS + ("разрешен", "permission")),
                            f"буфер обмена отказал без слов о разрешении: write={w} read={r}")
            print(f"  [TCC] буфер обмена отказан словами: {said[:200]}")

    # ---- desktop.window.read ---------------------------------------------- #

    def test_6_window_read_of_helene_window_or_refusal(self):
        rows = type(self).windows or (self.call("desktop.window.list", {"limit": 100}).get("items") or [])
        if not rows:
            self.skipTest("окон нет — читать нечего")

        def looks_like_helene(row: dict) -> bool:
            # `class` — идентификатор пакета, `owner` — имя владельца: разные поля с
            # 19.09, и своё окно может назваться любым из них.
            blob = " ".join(str(row.get(k) or "")
                            for k in ("class", "owner", "process_path", "title")).lower()
            return "helene" in blob

        own = [row for row in rows if looks_like_helene(row)]
        target = own[0] if own else rows[0]
        who = f"pid {target.get('pid')}, class {target.get('class')!r}, title {target.get('title')!r}"
        if own:
            print(f"  окно Helene найдено по pid из списка: {who}")
        else:
            print(f"  окна Helene на экране нет — читаю первое окно из списка: {who}")
        r = self.call("desktop.window.read", {"hwnd": target["hwnd"], "max_nodes": 300}, timeout=READ_SEC)
        if self.tcc.get("accessibility"):
            self.assertTrue(r.get("ok"), f"«Универсальный доступ» есть, а окно не прочиталось: {r}")
            self.assertIsInstance(r.get("window"), dict, r)
            self.assertGreaterEqual(int(r.get("nodes_read") or 0), 1, f"дерево без узлов: {r}")
            root = r.get("root")
            self.assertIsInstance(root, dict, f"shape=tree, а root — не узел: {r}")
            self.assertIn("role", root, root)
            print(f"  дерево окна: узлов прочитано {r.get('nodes_read')}, корень {root.get('role')!r} "
                  f"{(root.get('name') or '')[:40]!r}, truncated={r.get('truncated')}")
        else:
            self.assertFalse(r.get("ok"), f"«Универсального доступа» нет, а дерево «ok»: {r}")
            said = _text(r)
            self.assertTrue(_mentions(said, AX_WORDS),
                            f"отказ без слов про «Универсальный доступ»: {r}")
            print(f"  [TCC] нет «Универсального доступа» — дерево отказано словами: {said[:200]}")

    # ---- desktop.window.activate ------------------------------------------ #

    def test_7_activate_is_honest(self):
        """Поднять окно — и не соврать, если не поднялось.

        ⚠ ЗАЧЕМ ИМЕННО ТАК. `body.call` расплющивает ответ рамкой транспорта:
        `{**рамка, **результат, "ok": рамка.ok}`. Результат `{"ok": false}`
        доехал бы до модели как `"ok": true` — ложь в сторону успеха, и модель
        пошла бы печатать в чужое окно. Поэтому «не подняли» обязано быть
        ОШИБКОЙ рамки, и тогда снаружи это видно как `ok:false` + `error`.

        Ложь ловится в обе стороны: `ok:true` при `foreground_hwnd`, который не
        равен запрошенному, — падение; отказ при живом «Универсальном доступе»
        — тоже падение (сломано, а не запрещено).
        """
        rows = type(self).windows or (self.call("desktop.window.list", {"limit": 100}).get("items") or [])
        if not rows:
            self.skipTest("окон нет — поднимать нечего")

        def looks_like_helene(row: dict) -> bool:
            blob = " ".join(str(row.get(k) or "") for k in ("class", "process_path", "title")).lower()
            return "helene" in blob

        own = [row for row in rows if looks_like_helene(row)]
        # Своё окно, иначе Finder, иначе первое: чужой стол дёргать нечем.
        finder = [row for row in rows if "finder" in str(row.get("class") or "").lower()
                  or "finder" in str(row.get("owner") or "").lower()]
        target = (own or finder or rows)[0]
        hwnd = target["hwnd"]
        who = (f"pid {target.get('pid')}, class {target.get('class')!r}, "
               f"owner {target.get('owner')!r}, title {target.get('title')!r}")
        r = self.call("desktop.window.activate", {"hwnd": hwnd, "timeout_ms": 3000})
        said = _text(r)
        if r.get("ok"):
            # Успех — только по ФАКТУ: переднее окно стало запрошенным.
            self.assertEqual(r.get("requested_hwnd"), hwnd, r)
            self.assertEqual(
                str(r.get("foreground_hwnd") or "").lower(), str(hwnd).lower(),
                f"ok:true, а переднее окно другое — это ложь в сторону успеха: {r}")
            self.assertIn("waited_ms", r, r)
            print(f"  окно поднято по-настоящему ({who}): method={r.get('method')}, "
                  f"activated={r.get('activated')}, raised={r.get('raised')}, "
                  f"waited_ms={r.get('waited_ms')}")
        else:
            # Отказ обязан назвать, что удалось и почему не вышло.
            self.assertTrue(said, f"отказ без единого слова: {r}")
            self.assertTrue(
                _mentions(said, AX_WORDS + ("не поднялось", "was not activated", "did not come")),
                f"отказ без слов про «Универсальный доступ» и без «не поднялось»: {r}")
            for word in ("foreground_hwnd", "waited_ms"):
                self.assertIn(word, said, f"в отказе нет {word}: {r}")
            if self.tcc.get("accessibility"):
                # Разрешение есть — окно всё же могло не выйти вперёд (полноэкранный
                # сосед, Mission Control). Это законно, но СЛОВАМИ, а не «ok».
                print(f"  окно не поднялось при живом «Универсальном доступе» ({who}) — "
                      f"отказ словами: {said[:220]}")
            else:
                self.assertTrue(_mentions(said, AX_WORDS),
                                f"нет «Универсального доступа», а отказ молчит о нём: {r}")
                print(f"  [TCC] нет «Универсального доступа» — поднятие отказано словами: {said[:200]}")

    # ---- desktop.input.perform -------------------------------------------- #

    def test_8_input_is_honest(self):
        """Ввод: безвредная пачка и одинокий зажатый модификатор.

        Первая пачка — shift вниз и сразу вверх: на экране от неё ничего не
        происходит, а путь событий проверяется весь. Вторая — один `cmd down`
        без пары: состояние модификаторов живёт РОВНО ОДИН ВЫЗОВ (глобального
        состояния HID тело не держит), поэтому тело обязано отпустить его само
        и сказать об этом словами — иначе следующий щелчок человека станет
        ⌘-щелчком, а модель будет думать, что ⌘ всё ещё зажат.
        """
        shift = self.call("desktop.input.perform", {"events": [
            {"type": "key", "key": "shift", "action": "down"},
            {"type": "key", "key": "shift", "action": "up"},
        ]})
        if self.tcc.get("accessibility"):
            self.assertTrue(shift.get("ok"), f"«Универсальный доступ» есть, а ввод не прошёл: {shift}")
            self.assertGreaterEqual(int(shift.get("input_batches") or 0), 1, shift)
            self.assertEqual(shift.get("buttons_held_at_exit"), [], shift)
            self.assertEqual(shift.get("modifiers_auto_released"), [],
                             f"пачка закрыла shift сама — отпускать было нечего: {shift}")
            limits = shift.get("limits") or {}
            self.assertEqual(limits.get("modifiers_scope"), "call", limits)
            print(f"  ввод: пачек {shift.get('input_batches')}, записей {shift.get('input_records')}, "
                  f"modifiers_scope={limits.get('modifiers_scope')}")
        else:
            self.assertFalse(shift.get("ok"), f"«Универсального доступа» нет, а ввод «ok»: {shift}")
            said = _text(shift)
            self.assertTrue(_mentions(said, AX_WORDS), f"отказ ввода без слов про разрешение: {shift}")
            print(f"  [TCC] нет «Универсального доступа» — ввод отказан словами: {said[:200]}")

        lone = self.call("desktop.input.perform", {"events": [
            {"type": "key", "key": "cmd", "action": "down"},
        ]})
        if self.tcc.get("accessibility"):
            self.assertTrue(lone.get("ok"), f"одинокий cmd down не прошёл: {lone}")
            self.assertEqual(lone.get("modifiers_auto_released"), ["cmd"],
                             f"⌘ остался зажат после вызова: {lone}")
            self.assertEqual(lone.get("modifiers_held_at_exit"), [], lone)
            notes = lone.get("notes")
            self.assertIsInstance(notes, list, lone)
            self.assertTrue(any("modifier" in str(n).lower() for n in notes),
                            f"⌘ отпустили молча — модель так и будет думать, что он зажат: {lone}")
            print(f"  одинокий cmd down: отпущен сам ({lone.get('modifiers_auto_released')}), "
                  f"заметка есть")
        else:
            self.assertFalse(lone.get("ok"), f"«Универсального доступа» нет, а ввод «ok»: {lone}")
            self.assertTrue(_mentions(_text(lone), AX_WORDS), lone)


if __name__ == "__main__":
    if SKIP:
        print(f"пропуск  t_body_macos: {SKIP}", flush=True)
    unittest.main(verbosity=2)
