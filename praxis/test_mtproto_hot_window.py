"""Legacy hot text is a bounded read view, never a storage/KEAT policy."""
from __future__ import annotations

import ast
from collections import defaultdict, deque
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", str(Path(tempfile.gettempdir()) / "praxis_test_hot_window"))

import mtproto_runner as runner


class HotWindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.buf = defaultdict(lambda: deque(maxlen=runner.BUF_MAXLEN))
        self.meta = {}
        self.fetch = mock.AsyncMock()
        for patch in (
            mock.patch.object(runner, "_buf", self.buf),
            mock.patch.object(runner, "_meta", self.meta),
            mock.patch.object(runner, "client", SimpleNamespace(get_messages=self.fetch)),
            mock.patch.object(runner.memory_life, "HOT_LO", 50),
            mock.patch.object(runner.memory_life, "HOT_HI", 100),
            mock.patch.object(runner.memory_life, "HOT_HARD_HI", 125),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    async def test_bounds_live_cold_parity_and_retention(self):
        messages = [SimpleNamespace(id=i, message=f"line {i}", sender=None,
                                    out=False, media=None, reply_to_msg_id=None)
                    for i in range(160)]
        # Use the real formatter for the cold path, and the identical stored lines locally.
        with mock.patch.object(runner, "_sender_name", return_value="Alice"):
            lines = runner._format_messages(messages)
            for want, expected in ((-100, 50), (0, 50), (49, 50), (50, 50),
                                   (80, 80), (100, 100), (101, 100), (125, 100),
                                   (126, 100), (10**100, 100)):
                with self.subTest(last_n=want), mock.patch.object(runner, "LAST_N", want):
                    self.assertEqual(runner._hot_window(), expected)
                    self.buf["dm:7"].extend(lines)
                    retained = list(self.buf["dm:7"])
                    self.fetch.reset_mock()
                    local = await runner._last_n_text("dm:7")
                    self.fetch.assert_not_awaited()
                    self.assertEqual(local, "\n".join(lines[-expected:]))
                    self.assertEqual(list(self.buf["dm:7"]), retained)
                    self.buf["dm:7"].clear()
                    self.meta["dm:7"] = {"entity": "peer"}
                    self.fetch.return_value = list(reversed(messages[-expected:]))
                    cold = await runner._last_n_text("dm:7")
                    self.fetch.assert_awaited_once_with("peer", limit=expected)
                    self.assertEqual(cold, local)
                    self.assertFalse(self.buf["dm:7"], "read must not populate storage")

    async def test_topic_local_buffer_and_cold_route(self):
        with mock.patch.object(runner, "LAST_N", 80):
            self.buf["-1007"].append("wrong root")
            self.buf["-1007:10"].append("wrong sibling")
            self.buf["-1007:20"].extend(f"topic {i}" for i in range(120))
            self.meta["-1007:20"] = {"entity": "group", "topic_id": "20"}
            result = await runner._last_n_text("-1007:20")
            self.assertEqual(result.splitlines(), [f"topic {i}" for i in range(40, 120)])
            self.fetch.assert_not_awaited()
            self.buf["-1007:20"].clear()
            self.fetch.return_value = []
            self.assertEqual(await runner._last_n_text("-1007:20"), "")
            self.fetch.assert_awaited_once_with("group", limit=80, reply_to=20)

    async def test_short_live_buffer_does_not_fetch_to_fill_floor(self):
        self.buf["dm:7"].append("latest")
        self.meta["dm:7"] = {"entity": "peer"}
        self.assertEqual(await runner._last_n_text("dm:7"), "latest")
        self.fetch.assert_not_awaited()

    async def test_missing_or_failed_cold_fetch_stays_empty(self):
        self.assertEqual(await runner._last_n_text("missing"), "")
        self.fetch.assert_not_awaited()
        self.meta["missing"] = {"entity": "peer"}
        self.fetch.side_effect = RuntimeError("offline")
        with self.assertLogs(runner.log, level="WARNING"):
            self.assertEqual(await runner._last_n_text("missing"), "")

    def test_last_n_env_keeps_existing_strict_import_parse(self):
        # Execute the actual assignment, not a copy, without reimporting a Telegram client.
        tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "LAST_N"
                                  for t in node.targets))
        code = compile(ast.Module(body=[assignment], type_ignores=[]), runner.__file__, "exec")
        for raw in ("", "bad", "80.0", "nan", "1,000"):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"PRAXIS_LAST_N": raw}):
                with self.assertRaises(ValueError):
                    exec(code, {"os": os})
        for raw, expected in ((" -1 ", -1), ("0", 0), ("80", 80), ("100000", 100000)):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"PRAXIS_LAST_N": raw}):
                namespace = {"os": os}
                exec(code, namespace)
                self.assertEqual(namespace["LAST_N"], expected)


if __name__ == "__main__":
    unittest.main()
