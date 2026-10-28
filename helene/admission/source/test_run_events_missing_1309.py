"""Прогон без events.jsonl (13.09): пустой поток событий, а не отказ всего читателя.

Ретенция 12.09 сняла события у 65 июльских прогонов; сверка исходящих
(`outstanding_tools`) и рука `list_active_runs` падали на них при каждом старте
(`RunError: cannot read event stream … No such file or directory`).

Запуск: python praxis_test.py test_run_events_missing_1309 -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PRAXIS_TEST", "1")

from run_context import RunContext  # noqa: E402
from run_manager import RunManager  # noqa: E402


class MissingEvents(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.manager = RunManager(self.base)
        context = RunContext.create(
            run_id="run-missing-events", kind="chat", goal="events gone",
            principal_id="telegram:100", scope="owner", origin_chat_id="100",
            origin_message_ids=[1], delivery_chat_id="100")
        self.run = self.manager.create(context, "# Context\n")
        self.manager.transition(self.run.run_id, "running", expected="pending")
        self.manager.transition(self.run.run_id, "done", expected="running")
        events = self.manager._find(self.run.run_id) / "events.jsonl"
        self.assertTrue(events.exists())
        events.unlink()

    def test_outstanding_tools_is_empty_not_an_error(self):
        self.assertEqual(self.manager.outstanding_tools(self.run.run_id), {})

    def test_status_and_listing_still_work_from_the_manifest(self):
        status = self.manager.status(self.run.run_id)
        self.assertEqual(status["status"], "done")
        rows = self.manager.list_runs()
        self.assertIn(self.run.run_id, [row["run_id"] for row in rows])

    def test_event_iterators_yield_nothing(self):
        run_dir = self.manager._find(self.run.run_id)
        self.assertEqual(list(self.manager._iter_events(run_dir)), [])
        self.assertEqual(list(self.manager._iter_events_reverse(run_dir)), [])


if __name__ == "__main__":
    unittest.main()
