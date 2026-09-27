# -*- coding: utf-8 -*-
"""Стенд «свёртка памяти чата по кнопке» (27.09).

Запуск:  python tests/t_fold_button_2709.py

Егор 27.09: «интересно, как происходит компактирование в мейн чате Элен. Неплохо бы
сделать его по нажатию». Свёртка — её (`memory_life.fold_now`, та же, что у её руки
memory_compact(fold)); кнопка только просит. Здесь стерегутся обещания цепочки:

* канал кладёт просьбу и говорит правду о памяти места (сколько горячего, её пороги,
  идёт ли свёртка) — и не путает места;
* раннер берёт просьбу один раз, сворачивает ЕЁ функцией и отвечает распиской; вторая
  просьба, пока идёт первая, не берётся; сбой свёртки — словами, а не молчанием.

Модели нет: вместо memory_life — подделка с тем же `fold_now`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
from deskd import control  # noqa: E402
import runner  # noqa: E402


def life_state(tree: Path, room: str, hot: int) -> None:
    path = tree / "memory" / ".state" / "life" / f"{room}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "chat_id": room, "hot": [{"id": i} for i in range(hot)]}),
                    encoding="utf-8")


class Channel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)

    def test_пороги_её_личка_и_комната_разные(self):
        self.assertEqual(control.fold_bounds("window"), (150, 250, 300))
        self.assertEqual(control.fold_bounds("-1001240718803"), (250, 400, 500))

    def test_состояние_говорит_сколько_горячего(self):
        life_state(self.tree, "window", 212)
        st = control.fold_state(self.tree, "window")
        self.assertEqual(st["hot"], 212)
        self.assertEqual((st["keep"], st["offer_at"], st["hard_at"]), (150, 250, 300))
        self.assertIsNone(st["pending"])
        self.assertIsNone(st["receipt"])

    def test_нет_состояния_нет_числа(self):
        self.assertIsNone(control.fold_state(self.tree, "window")["hot"])

    def test_просьба_ложится_и_видна_своему_месту(self):
        said = control.fold_request(self.tree, "window")
        self.assertTrue(said["ok"])
        self.assertTrue((self.tree / "memory" / ".control" / control.FOLD_REQUEST).is_file())
        self.assertEqual(control.fold_state(self.tree, "window")["pending"]["room"], "window")
        self.assertIsNone(control.fold_state(self.tree, "chat-2")["pending"], "чужое место не свёртывается")

    def test_странное_имя_не_принимается(self):
        self.assertFalse(control.fold_request(self.tree, "../../etc")["ok"])
        self.assertFalse((self.tree / "memory" / ".control" / control.FOLD_REQUEST).exists())


class FakeLife:
    def __init__(self, result=None, error=None, hold=None):
        self.calls: list[str] = []
        self.result = result if result is not None else {"ok": True, "folded": 62, "hot": 212}
        self.error = error
        self.hold = hold

    def fold_now(self, place):
        self.calls.append(str(place))
        if self.hold is not None:
            self.hold.wait(5)
        if self.error:
            raise self.error
        return self.result


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        saved = runner._life
        self.addCleanup(lambda: setattr(runner, "_life", saved))
        runner._FOLD_BUSY[0] = False
        self.addCleanup(lambda: runner._FOLD_BUSY.__setitem__(0, False))

    def receipt(self) -> dict:
        path = self.tree / "memory" / ".state" / control.FOLD_RECEIPT
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def settle(self, want: str, seconds: float = 5.0) -> dict:
        end = time.time() + seconds
        while time.time() < end:
            got = self.receipt()
            if got.get("state") == want and not runner._FOLD_BUSY[0]:
                return got
            time.sleep(0.05)
        self.fail(f"расписка не дошла до «{want}»: {self.receipt()}")

    def test_раннер_сворачивает_её_функцией_и_отвечает(self):
        life = FakeLife()
        runner._life = life
        control.fold_request(self.tree, "window")
        runner._fold_tick(self.tree)
        got = self.settle("done")
        self.assertEqual(life.calls, ["window"])
        self.assertEqual(got["folded"], 62)
        self.assertIn("свёрнуто", got["note"])
        ctl = self.tree / "memory" / ".control"
        self.assertFalse((ctl / control.FOLD_REQUEST).exists())
        self.assertFalse((ctl / control.FOLD_PROCESSING).exists(), "взятая просьба убрана")

    def test_нечего_сворачивать_сказано_словами(self):
        runner._life = FakeLife(result={"ok": True, "folded": 0, "plan": {"reason": "within_window"}})
        control.fold_request(self.tree, "window")
        runner._fold_tick(self.tree)
        got = self.settle("nothing")
        self.assertIn("нечего", got["note"])

    def test_окно_сдвинулось_просит_ещё_раз(self):
        runner._life = FakeLife(result={"ok": False, "reason": "state_changed", "folded": 0})
        control.fold_request(self.tree, "window")
        runner._fold_tick(self.tree)
        self.assertIn("ещё раз", self.settle("retry")["note"])

    def test_сбой_свёртки_словами(self):
        runner._life = FakeLife(error=RuntimeError("модель не ответила"))
        control.fold_request(self.tree, "window")
        runner._fold_tick(self.tree)
        self.assertIn("модель не ответила", self.settle("failed")["note"])

    def test_вторая_просьба_ждёт_первую(self):
        hold = threading.Event()
        life = FakeLife(hold=hold)
        runner._life = life
        control.fold_request(self.tree, "window")
        runner._fold_tick(self.tree)
        time.sleep(0.1)
        control.fold_request(self.tree, "chat-2")
        runner._fold_tick(self.tree)          # занято — не берёт
        self.assertTrue((self.tree / "memory" / ".control" / control.FOLD_REQUEST).is_file())
        self.assertEqual(control.fold_state(self.tree, "window")["receipt"]["state"], "running")
        hold.set()
        self.settle("done")
        runner._fold_tick(self.tree)          # теперь берёт вторую
        end = time.time() + 5
        while time.time() < end and life.calls != ["window", "chat-2"]:
            time.sleep(0.05)
        self.assertEqual(life.calls, ["window", "chat-2"])
        self.settle("done")

    def test_без_памяти_дерева_ничего_не_берёт(self):
        runner._life = None
        control.fold_request(self.tree, "window")
        runner._fold_tick(self.tree)
        self.assertTrue((self.tree / "memory" / ".control" / control.FOLD_REQUEST).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
