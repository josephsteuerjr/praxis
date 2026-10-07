# -*- coding: utf-8 -*-
"""Живой стенд Linux-тела: X11 (EWMH, XTest, GetImage) + AT-SPI2, со свидетелями со стороны.

Запуск — внутри образа `helene-linux-desk` (installer/linux/Dockerfile.desk); всё
окружение поднимает installer/linux/desk-stand.sh:

    HELENE_BODY_BIN=/target/debug/praxis-body python3 tests/t_body_linux.py

Что здесь. Тело зовётся напрямую (`praxis-body invoke-local`, та же диспетчеризация, что у
моста), а правду о сделанном говорит НЕ оно, а независимые свидетели:

  desktop.status          platform=linux, x11, оконный менеджер, XTest, шина AT-SPI
  desktop.window.list     окно zenity с тем же hwnd, что у `xdotool search`, и тем же pid
  desktop.window.activate `xdotool getactivewindow` после — ровно запрошенное окно
  desktop.input.perform   кириллица в поле zenity и Enter → zenity печатает ТО, что набрано;
                          мышь → `xdotool getmouselocation`; одинокий `ctrl down` отпущен
  desktop.screen.capture  PNG 1280×800; угол корня — цвет, поставленный `xsetroot`
  desktop.clipboard.read  текст, положенный `xclip`, читается тем же
  desktop.window.read     дерево окна zenity: поле ввода (edit) и кнопка OK с patterns
  desktop.element.find    кнопка находится отбором role/name
  desktop.element.act     set_value в поле и invoke на OK → zenity печатает положенное
  os.process.list         pid zenity есть в списке

Нет Linux, нет DISPLAY, нет бинаря тела или свидетелей — стенд пропускается словами, а
не зелёным: причина стоит в skip.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

LINUX = sys.platform.startswith("linux")
BODY = os.environ.get("HELENE_BODY_BIN", "").strip()
WITNESSES = ("xdotool", "xprop", "zenity", "xsetroot", "xclip")
ROOT_COLOR = (0x33, 0x66, 0x99)


def _why_skipped() -> str:
    if not LINUX:
        return "не Linux — живой стенд X11/AT-SPI идёт в образе helene-linux-desk"
    if not os.environ.get("DISPLAY"):
        return "нет DISPLAY — X-сервер не поднят (installer/linux/desk-stand.sh)"
    if not BODY or not Path(BODY).is_file():
        return f"нет бинаря тела: HELENE_BODY_BIN={BODY!r}"
    missing = [tool for tool in WITNESSES if shutil.which(tool) is None]
    if missing:
        return "нет свидетелей: " + ", ".join(missing)
    return ""


SKIP = _why_skipped()


def run(*args: str, timeout: float = 15, check: bool = True) -> str:
    done = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    if check and done.returncode != 0:
        raise AssertionError(f"{args[0]} ответил {done.returncode}: {done.stderr.strip()}")
    return done.stdout.strip()


def png_pixels(path: Path) -> tuple[int, int, list[bytes]]:
    """Минимальный разбор PNG тела: 8 бит RGB, без чересстрочности. -> (ширина, высота, строки)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "не PNG"
    pos, chunks, head = 8, [], None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        pos += 12 + length
    width, height, depth, color, _, _, interlace = head
    assert (depth, color, interlace) == (8, 2, 0), f"ждали RGB 8 бит: {head}"
    raw = zlib.decompress(b"".join(chunks))
    stride, rows, prev = width * 3, [], bytearray(width * 3)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - 3] if x >= 3 else 0
            b = prev[x]
            c = prev[x - 3] if x >= 3 else 0
            if kind == 1:
                line[x] = (line[x] + a) & 0xFF
            elif kind == 2:
                line[x] = (line[x] + b) & 0xFF
            elif kind == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        rows.append(bytes(line))
        prev = line
    return width, height, rows


@unittest.skipIf(bool(SKIP), SKIP)
class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="helene-body-linux-"))
        cls.config = cls.tmp / "body.json"
        cls.config.write_text(json.dumps({
            "device_id": "linux-stand",
            "token": "linux-stand-token",
            "bridge_ws_url": "ws://127.0.0.1:9/unused",
            "artifact_base_url": "http://127.0.0.1:9/unused",
            "state_dir": str(cls.tmp / "state"),
        }), encoding="utf-8")
        run("xsetroot", "-solid", "#%02x%02x%02x" % ROOT_COLOR)

    def call(self, capability: str, args: dict | None = None, timeout: float = 40) -> dict:
        done = subprocess.run([BODY, "invoke-local", "--config", str(self.config), "--capability",
                               capability, "--args", json.dumps(args or {}, ensure_ascii=False)],
                              capture_output=True, text=True, timeout=timeout)
        try:
            frame = json.loads(done.stdout)
        except ValueError:
            raise AssertionError(f"{capability}: тело ответило не JSON (код {done.returncode}): "
                                 f"{done.stdout[:300]} | stderr: {done.stderr[-800:]}") from None
        return frame

    def ok(self, capability: str, args: dict | None = None, timeout: float = 40) -> dict:
        frame = self.call(capability, args, timeout)
        result = frame.get("result") or {}
        self.assertTrue(frame.get("ok") and result.get("ok", True),
                        f"{capability}: {json.dumps(frame, ensure_ascii=False)[:1500]}")
        return result

    def zenity(self, *args: str, title: str) -> tuple[subprocess.Popen, int]:
        """Поднять zenity и дождаться его окна. -> (процесс, id окна по xdotool)."""
        proc = subprocess.Popen(["zenity", *args, "--title", title], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                env={**os.environ, "GTK_A11Y": "atspi", "NO_AT_BRIDGE": "0"})
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(100):
            found = run("xdotool", "search", "--name", title, check=False)
            if found:
                return proc, int(found.splitlines()[0])
            time.sleep(0.1)
        raise AssertionError(f"окно zenity {title!r} не появилось")

    def row_of(self, hwnd: int) -> dict:
        listing = self.ok("desktop.window.list", {"all_layers": True})
        rows = [row for row in listing["items"] if int(row["hwnd"], 16) == hwnd]
        self.assertEqual(len(rows), 1, f"окна 0x{hwnd:X} нет в списке: {listing['items']}")
        return rows[0]

    def field_value(self, hwnd: int) -> str | None:
        """Текст поля ввода окна — через AT-SPI (второй свидетель набора)."""
        tree = self.ok("desktop.window.read", {"hwnd": f"0x{hwnd:X}", "shape": "flat"})
        edits = [item for item in tree.get("items", []) if item.get("role") == "edit"]
        return edits[0].get("value") if edits else None

    def activate(self, hwnd: int) -> dict:
        result = self.ok("desktop.window.activate", {"hwnd": f"0x{hwnd:X}", "timeout_ms": 3000})
        self.assertEqual(int(run("xdotool", "getactivewindow")), hwnd, "свидетель: активно другое окно")
        return result

    # ─── проверки ──────────────────────────────────────────────────────────────────

    def test_1_status_names_linux_x11_wm_xtest_and_atspi(self):
        status = self.ok("desktop.status")
        self.assertEqual(status["platform"], "linux")
        self.assertTrue(status["console"])
        self.assertEqual(status["session"]["kind"], "x11")
        self.assertTrue(status["window_manager"], status)
        self.assertTrue(status["xtest"])
        self.assertEqual(status["virtual_screen"]["width"], 1280)
        self.assertEqual(status["virtual_screen"]["height"], 800)
        self.assertTrue(status["atspi"]["bus"], status["atspi"])

    def test_2_window_list_matches_xdotool_and_xprop(self):
        proc, hwnd = self.zenity("--info", "--text", "список окон", title="Стенд список")
        row = self.row_of(hwnd)
        self.assertEqual(row["title"], "Стенд список")
        self.assertEqual(row["pid"], proc.pid, "pid строки ≠ pid процесса zenity")
        xprop = run("xprop", "-id", str(hwnd), "_NET_WM_PID")
        self.assertIn(str(proc.pid), xprop)
        for key in ("fingerprint", "rect", "process_path", "class", "window_type", "minimized"):
            self.assertIn(key, row)
        self.assertGreater(row["rect"]["width"], 50)

    def test_3_activate_is_proved_by_the_witness(self):
        _, first = self.zenity("--info", "--text", "первое", title="Стенд первое")
        _, second = self.zenity("--info", "--text", "второе", title="Стенд второе")
        self.activate(first)
        result = self.activate(second)
        self.assertEqual(result["method"], "ewmh")
        self.assertEqual(int(result["foreground_hwnd"], 16), second)

    def test_4_typing_cyrillic_reaches_the_program(self):
        proc, hwnd = self.zenity("--entry", "--text", "ввод", title="Стенд ввод")
        self.activate(hwnd)
        time.sleep(0.5)
        typed = "Привет, Linux! ёЁ 123"
        result = self.ok("desktop.input.perform", {
            "expected_foreground": f"0x{hwnd:X}",
            "events": [{"type": "text", "text": typed}, {"type": "key", "key": "enter"}],
        })
        self.assertEqual(result["platform"], "linux")
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(out.strip(), typed, "zenity получил не то, что набрано")

    def test_5_mouse_moves_and_a_lone_modifier_is_released(self):
        proc, hwnd = self.zenity("--entry", "--text", "модификатор", title="Стенд модификатор")
        self.activate(hwnd)
        time.sleep(0.5)
        result = self.ok("desktop.input.perform", {"events": [
            {"type": "mouse", "x": 321, "y": 234},
            {"type": "key", "key": "ctrl", "action": "down"},
        ]})
        self.assertIn("ctrl", result["modifiers_auto_released"])
        # Отладочный слух (STAND_XEV=1): что приходит в окно фокуса zenity.
        xev = None
        if os.environ.get("STAND_XEV"):
            focus = (self.call("desktop.status").get("result") or {}).get("input_focus")
            xev = subprocess.Popen(["xev", "-id", str(focus), "-event", "keyboard"],
                                   stdout=subprocess.PIPE, text=True)
            self.addCleanup(lambda: xev.poll() is None and xev.kill())
            time.sleep(0.3)
        self.assertIn("x:321 y:234", run("xdotool", "getmouselocation"))
        # Свидетель отпускания: останься Ctrl зажат, «abc» ушло бы как Ctrl+A, Ctrl+B,
        # Ctrl+C, и zenity напечатал бы не «abc».
        self.ok("desktop.input.perform", {"expected_foreground": f"0x{hwnd:X}", "events": [
            {"type": "text", "text": "abc"},
        ]})
        typed = self.field_value(hwnd)
        if typed != "abc" and xev is not None:
            time.sleep(0.3)
            xev.kill()
            heard = " ".join(line.strip() for line in xev.stdout.read().splitlines()
                             if "keysym" in line or "Key" in line)[:3000]
            self.fail(f"в поле {typed!r} вместо 'abc'; xev: {heard}")
        self.ok("desktop.input.perform", {"expected_foreground": f"0x{hwnd:X}", "events": [
            {"type": "key", "key": "enter"},
        ]})
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            shot = Path(os.environ.get("STAND_EVIDENCE", str(self.tmp))) / "modifier-timeout.png"
            run("import", "-window", "root", str(shot), check=False)
            active = run("xdotool", "getactivewindow", "getwindowname", check=False)
            tree = self.call("desktop.window.read", {"hwnd": f"0x{hwnd:X}", "shape": "flat"}).get("result") or {}
            fields = [(i.get("role"), i.get("name"), i.get("value"), i.get("state"))
                      for i in tree.get("items", []) if i.get("role") in ("edit", "button")]
            status = self.call("desktop.status").get("result") or {}
            heard = ""
            if xev is not None:
                xev.kill()
                heard = " ".join(line.strip() for line in xev.stdout.read().splitlines()
                                 if "keysym" in line or "KeyPress" in line or "KeyRelease" in line)[:3000]
            raise AssertionError(f"zenity не закрылся (в поле после набора было {typed!r}): "
                                 f"активно окно {active!r} (0x{hwnd:X}), "
                                 f"фокус ввода {status.get('input_focus')}, зажато "
                                 f"{status.get('keys_down')}, поля {fields}, снимок {shot}; "
                                 f"xev: {heard}") from None
        self.assertEqual(out.strip(), "abc")

    def test_7_clipboard_read_sees_what_xclip_put(self):
        text = "буфер обмена: Hélène ✓"
        feeder = subprocess.Popen(["xclip", "-selection", "clipboard", "-i", "-loops", "1"],
                                  stdin=subprocess.PIPE, text=True)
        feeder.stdin.write(text)
        feeder.stdin.close()
        time.sleep(0.3)
        result = self.ok("desktop.clipboard.read")
        self.assertEqual(result["text"], text)
        feeder.wait(timeout=5)

    def test_8_window_read_shows_the_entry_and_the_ok_button(self):
        _, hwnd = self.zenity("--entry", "--text", "чтение", title="Стенд чтение")
        tree = self.ok("desktop.window.read", {"hwnd": f"0x{hwnd:X}", "shape": "flat"}, timeout=60)
        self.assertEqual(tree["backend"], "atspi")
        roles = {(item["role"], item.get("name")) for item in tree["items"]}
        edits = [item for item in tree["items"] if item["role"] == "edit"]
        self.assertTrue(edits, f"нет поля ввода: {sorted(roles, key=str)[:40]}")
        self.assertIn("set_value", edits[0].get("patterns", []))
        buttons = [item for item in tree["items"] if item["role"] == "button" and item.get("name") in ("OK", "ОК", "_OK")]
        self.assertTrue(buttons, f"нет кнопки OK: {sorted(roles, key=str)[:40]}")
        self.assertIn("invoke", buttons[0].get("patterns", []))

    def test_9_element_act_fills_the_field_and_presses_ok(self):
        proc, hwnd = self.zenity("--entry", "--text", "действие", title="Стенд действие")
        found = self.ok("desktop.element.find", {"hwnd": f"0x{hwnd:X}", "select": {"role": "edit"}})
        self.assertGreaterEqual(found["matched"], 1)
        self.ok("desktop.element.act", {"hwnd": f"0x{hwnd:X}", "select": {"role": "edit"},
                                        "do": "set_value", "text": "Из AT-SPI: ёжик"})
        self.ok("desktop.element.act", {"hwnd": f"0x{hwnd:X}", "select": {"role": "button", "name_contains": "OK"},
                                        "do": "invoke"})
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(out.strip(), "Из AT-SPI: ёжик")

    def test_10_process_list_sees_zenity(self):
        proc, _ = self.zenity("--info", "--text", "процессы", title="Стенд процессы")
        listing = self.ok("os.process.list", {"name_contains": "zenity", "limit": 50})
        self.assertIn(proc.pid, [row["pid"] for row in listing["items"]])
        self.assertEqual(listing["platform"], "linux")


@unittest.skipIf(bool(SKIP), SKIP)
class ThroughTheEngine(unittest.TestCase):
    """Мост и тело — тем же путём, что у движка (`body.launch` харнесса): снимок публикуется
    мосту как артефакт, `invoke-local` без моста этого не умеет. Заодно — Linux-ветки
    `localharness/body.py`: тело на Linux есть, проба `desktop.status` пишет платформу."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "localharness"))
        import body as harness  # noqa: PLC0415
        cls.body = harness
        cls.tmp = Path(tempfile.mkdtemp(prefix="helene-engine-linux-"))
        cls.root = cls.tmp / "helene"
        cls.tree = cls.root / "data"
        (cls.tree / "memory" / ".state").mkdir(parents=True)
        cfg = {"agent_mode": "sandbox", "computer": {"enabled": True, "port": 9510}}
        (cls.root / "helene.json").write_text(json.dumps(cfg), encoding="utf-8")
        os.environ["HELENE_BODY_DIR"] = str(Path(BODY).parent)
        assert harness.HAS_BODY, "движок не знает тела на Linux: body.HAS_BODY = False"
        if harness.launch(cls.root, cls.tree, cfg) is None:
            raise AssertionError(f"тело не поднялось: {harness.STATE.get('reason')}")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and harness.STATE.get("connected") is not True:
            time.sleep(0.5)
        if harness.STATE.get("connected") is not True:
            harness.shutdown()
            raise AssertionError(f"тело не подключилось к мосту: {harness.STATE.get('reason')}")
        run("xsetroot", "-solid", "#%02x%02x%02x" % ROOT_COLOR)

    @classmethod
    def tearDownClass(cls):
        cls.body.shutdown()

    def call(self, capability: str, args: dict) -> dict:
        answer = self.body.call(capability, args, timeout=30)
        self.assertTrue(answer.get("ok"), f"{capability}: {json.dumps(answer, ensure_ascii=False)[:1500]}")
        return answer

    def test_status_through_the_engine_names_linux(self):
        status = self.call("desktop.status", {})
        self.assertEqual(status["platform"], "linux")
        self.body._BODY.probe_desktop()
        self.assertEqual(self.body.STATE.get("platform"), "linux")
        self.assertIsNone(self.body.STATE.get("tcc"), "TCC — понятие Mac; на Linux его нет")
        self.assertEqual(self.body.DEFAULT_DEVICE, "linux")

    def test_capture_through_the_bridge_sees_the_root_colour(self):
        result = self.call("desktop.screen.capture", {"target": "region", "x": 1200, "y": 700,
                                                       "width": 40, "height": 40, "name": "угол"})
        width, height, rows = png_pixels(Path(result["path"]))
        self.assertEqual((width, height), (40, 40))
        self.assertEqual(tuple(rows[20][60:63]), ROOT_COLOR, "в углу не цвет корня")
        whole = self.call("desktop.screen.capture", {"target": "desktop"})
        self.assertEqual((whole["width"], whole["height"]), (1280, 800))
        clipped = self.call("desktop.screen.capture", {"target": "region", "x": 1270, "y": 790,
                                                        "width": 100, "height": 100})
        self.assertEqual((clipped["width"], clipped["height"]), (10, 10))
        self.assertTrue(clipped["notes"], "обрезка края названа")


if __name__ == "__main__":
    unittest.main(verbosity=2)
