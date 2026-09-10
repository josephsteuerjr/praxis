"""Focused contract for the MTProto runner's observer-only event-loop probe."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault(
    "TELEGRAM_SESSION", str(Path(tempfile.gettempdir()) / "praxis_test_loop_liveness"))

import mtproto_runner as runner  # noqa: E402


class _ControlledLoop:
    """Accept callbacks from the probe thread; the test chooses when to execute them."""

    def __init__(self) -> None:
        self._callbacks: list[object] = []
        self._condition = threading.Condition()

    def call_soon_threadsafe(self, callback) -> None:
        with self._condition:
            self._callbacks.append(callback)
            self._condition.notify_all()

    def wait_for_callbacks(self, count: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self._callbacks) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def run_next(self) -> None:
        with self._condition:
            callback = self._callbacks.pop(0)
        callback()


class _ImmediateLoop:
    def call_soon_threadsafe(self, callback) -> None:
        callback()


class _ClosedLoop:
    def call_soon_threadsafe(self, callback) -> None:
        raise RuntimeError("loop closed")


class LoopLivenessProbeTests(unittest.TestCase):
    @staticmethod
    def _wait_until(predicate, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return bool(predicate())

    def test_one_warning_and_one_recovery_per_overdue_episode(self):
        loop = _ControlledLoop()
        with self.assertLogs(runner.log, level="INFO") as captured:
            probe = runner._LoopLivenessProbe(
                loop, interval=0.02, threshold=0.035).start()
            self.addCleanup(probe.stop)
            self.assertTrue(loop.wait_for_callbacks(1), "probe did not schedule its callback")
            self.assertTrue(self._wait_until(
                lambda: sum("telegram_loop_probe_overdue" in line
                            for line in captured.output) == 1))

            # Repeated checks of the same pending callback stay in one episode.
            time.sleep(0.08)
            self.assertEqual(sum("telegram_loop_probe_overdue" in line
                                 for line in captured.output), 1)
            loop.run_next()
            self.assertTrue(self._wait_until(
                lambda: sum("telegram_loop_probe_recovered" in line
                            for line in captured.output) == 1))

            # Once recovered, a later delayed callback is a new episode.
            self.assertTrue(loop.wait_for_callbacks(1))
            self.assertTrue(self._wait_until(
                lambda: sum("telegram_loop_probe_overdue" in line
                            for line in captured.output) == 2))
            loop.run_next()
            self.assertTrue(self._wait_until(
                lambda: sum("telegram_loop_probe_recovered" in line
                            for line in captured.output) == 2))
            probe.stop()

        warnings = [line for line in captured.output
                    if "telegram_loop_probe_overdue" in line]
        recoveries = [line for line in captured.output
                      if "telegram_loop_probe_recovered" in line]
        self.assertEqual(len(warnings), 2)
        self.assertEqual(len(recoveries), 2)
        self.assertTrue(all("callback_lag_sec=" in line for line in warnings))
        self.assertTrue(all("episode_duration_sec=" in line for line in recoveries))

    def test_timely_callbacks_are_silent_and_stop_joins_daemon(self):
        probe = runner._LoopLivenessProbe(
            _ImmediateLoop(), interval=0.01, threshold=0.05)
        with mock.patch.object(runner.log, "warning") as warning, \
             mock.patch.object(runner.log, "info") as info:
            probe.start()
            self.assertTrue(probe.thread.daemon)
            time.sleep(0.04)
            probe.stop()
        self.assertFalse(probe.thread.is_alive())
        warning.assert_not_called()
        info.assert_not_called()

    def test_closed_loop_stops_observer_without_alert(self):
        probe = runner._LoopLivenessProbe(
            _ClosedLoop(), interval=0.01, threshold=0.02)
        with mock.patch.object(runner.log, "warning") as warning, \
             mock.patch.object(runner.log, "info") as info:
            probe.start()
            probe.thread.join(timeout=0.5)
        self.assertFalse(probe.thread.is_alive())
        warning.assert_not_called()
        info.assert_not_called()

    def test_bad_timing_values_fall_back_to_safe_defaults(self):
        with mock.patch.dict(os.environ, {
            "BAD_PROBE_VALUE": "not-a-number",
            "ZERO_PROBE_VALUE": "0",
            "GOOD_PROBE_VALUE": "1.25",
        }):
            self.assertEqual(runner._positive_env_float("BAD_PROBE_VALUE", 5.0), 5.0)
            self.assertEqual(runner._positive_env_float("ZERO_PROBE_VALUE", 5.0), 5.0)
            self.assertEqual(runner._positive_env_float("GOOD_PROBE_VALUE", 5.0), 1.25)


if __name__ == "__main__":
    unittest.main()
