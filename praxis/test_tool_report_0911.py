# -*- coding: utf-8 -*-
"""Журнал знает исход руки и что она запускала; модель получает прежний текст (09.11.2026).

Запуск: python praxis_test.py test_tool_report_0911 -v
"""
from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import forge  # noqa: E402
import tool_report  # noqa: E402
import workshop  # noqa: E402


class Slot:
    def __enter__(self):
        self.report = tool_report.new()
        self.token = tool_report.attach(self.report)
        return self.report

    def __exit__(self, *exc):
        tool_report.detach(self.token)


class TheReport(unittest.TestCase):
    def test_silence_is_unknown_not_success(self):
        self.assertEqual(tool_report.summary(tool_report.new())["outcome"], "unknown")

    def test_a_crash_is_failed(self):
        self.assertEqual(tool_report.summary(tool_report.new(), error=ValueError())["outcome"], "failed")

    def test_the_ceiling_is_in_doubt(self):
        self.assertEqual(tool_report.summary(tool_report.new(), expired=True)["outcome"], "in_doubt")

    def test_note_outside_a_call_is_silent(self):
        tool_report.note(outcome="ok", target="x")
        tool_report.execution({"status": "returned"})

    def test_an_invented_outcome_is_ignored(self):
        with Slot() as report:
            tool_report.note(outcome="maybe")
        self.assertEqual(tool_report.summary(report)["outcome"], "unknown")

    def test_metadata_carries_the_outcome(self):
        with Slot() as report:
            tool_report.note(outcome="refused", target="a.txt")
        meta = agent._tool_result_metadata(agent._tool_outbound_snapshot(), {"report": report})
        self.assertEqual((meta["outcome"], meta["target"]), ("refused", "a.txt"))
        self.assertIn("outbound", meta)

    def test_metadata_without_a_report_is_unchanged(self):
        meta = agent._tool_result_metadata(agent._tool_outbound_snapshot())
        self.assertEqual(sorted(meta), ["outbound", "schema"])


class TheAudit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_file_actions_are_recorded_without_content(self):
        target = Path(self.dir) / "note.txt"
        with Slot() as report:
            tool_report._audit("open", (str(target), "w", os.O_WRONLY | os.O_CREAT))
            tool_report._audit("os.rename", (str(target), str(target) + ".2", -1, -1))
        self.assertEqual(report["operations"][0], ["write", str(target)])
        self.assertEqual(report["operations"][1][0], "replace")

    def test_code_loading_is_not_an_action(self):
        with Slot() as report:
            tool_report._audit("open", ("/app/agent.py", "r", os.O_RDONLY))
            tool_report._audit("open", (tool_report.sys.prefix + "/lib/x.txt", "r", os.O_RDONLY))
        self.assertEqual(report["operations"], [])

    def test_her_own_journal_is_not_an_action(self):
        with Slot() as report:
            for path in ("/app/memory/runs/2026-11/r/events.jsonl", "/app/memory/.state/x.json", "/app/memory/.forge/t.json"):
                tool_report._audit("open", (path, "r", os.O_RDONLY))
        self.assertEqual(report["operations"], [])

    def test_the_list_is_bounded_and_counts_the_rest(self):
        with Slot() as report:
            for i in range(tool_report.MAX_OPERATIONS + 5):
                tool_report._audit("open", (f"{self.dir}/f{i}", "r", os.O_RDONLY))
        self.assertEqual(len(report["operations"]), tool_report.MAX_OPERATIONS)
        self.assertEqual(report["operations_cut"], 5)

    def test_outside_a_call_nothing_is_recorded(self):
        tool_report._audit("open", (f"{self.dir}/f", "r", os.O_RDONLY))

    def test_a_bad_event_never_raises(self):
        with Slot():
            tool_report._audit("open", ("only-one-field",))
            tool_report._audit("subprocess.Popen", (None,))


class TheFileHands(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir / "workspace").mkdir()
        patcher = mock.patch.object(workshop, "BASE", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_reports_ok_and_missing_file_refused(self):
        (self.dir / "workspace/a.txt").write_text("x\n", encoding="utf-8")
        with Slot() as report:
            workshop.fs_read("workspace/a.txt")
        self.assertEqual(report["outcome"], "ok")
        with Slot() as report:
            workshop.fs_read("workspace/none.txt")
        self.assertEqual(report["outcome"], "refused")

    def test_a_refused_edit_is_refused_in_the_journal(self):
        with Slot() as report, mock.patch.object(workshop, "_serialized_write",
                                                 return_value={"status": "refused", "message": "Не правлю", "after_sha256": None}):
            workshop.fs_edit("workspace/a.txt", "o", "n")
        self.assertEqual(report["outcome"], "refused")

    def test_a_confirmed_edit_is_confirmed_in_the_journal(self):
        with Slot() as report, mock.patch.object(workshop, "_serialized_write",
                                                 return_value={"status": "confirmed", "message": "ok", "after_sha256": "ab"}):
            out = workshop.fs_edit("workspace/a.txt", "o", "n")
        self.assertEqual(report["outcome"], "confirmed")
        self.assertIn("[confirmed sha256=ab]", out)


class TheShell(unittest.TestCase):
    def call(self, command, **kwargs):
        with Slot() as report, mock.patch.object(agent, "_autocommit_self_edit"), \
                mock.patch.object(agent, "_pre_shell_dirt", return_value=None), \
                mock.patch.object(agent.selfgit, "safety_point"):
            return agent.tool_shell(command, **kwargs), report

    def test_exit_code_is_in_the_reply(self):
        out, report = self.call("echo hi")
        self.assertTrue(out.endswith("[exit 0]"), out)
        self.assertEqual(report["outcome"], "ok")

    def test_failure_is_failed(self):
        out, report = self.call("exit 3")
        self.assertIn("[exit 3]", out)
        self.assertEqual(report["outcome"], "failed")

    def test_cwd_is_used(self):
        folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, folder, True)
        out, _ = self.call("pwd", cwd=str(folder))
        self.assertIn(str(folder), out)

    def test_missing_cwd_is_refused_not_silently_home(self):
        out, report = self.call("pwd", cwd="/no/such/folder")
        self.assertIn("нет такой папки", out)
        self.assertEqual(report["outcome"], "refused")

    def test_timeout_is_honoured_and_bounded(self):
        out, report = self.call("sleep 5", timeout=1)
        self.assertIn("таймауту 1s", out)
        self.assertEqual(report["outcome"], "failed")

    def test_schema_offers_cwd_and_timeout(self):
        props = agent.catalog_tools_for(agent.ChannelContext(chat_id="42", room_id="42", is_dm=True, owner=True, known=True))
        shell = next(t for t in props if t.get("name") == "shell")
        self.assertGreaterEqual(set(shell["input_schema"]["properties"]), {"command", "cwd", "timeout"})


class TheNames(unittest.TestCase):
    def test_run_takes_command_and_there_is_no_old_name(self):
        self.assertEqual(list(inspect.signature(workshop.run).parameters)[0], "command")
        tools = agent.catalog_tools_for(agent.ChannelContext(chat_id="42", room_id="42", is_dm=True, owner=True, known=True))
        run = next(t for t in tools if t.get("name") == "shell")
        self.assertNotIn("cmd", run["input_schema"]["properties"])
        self.assertIn("cwd", run["input_schema"]["properties"])
        self.assertNotIn("project_run", agent.TOOL_IMPL)


class TheForge(unittest.TestCase):
    def test_edit_outcomes_are_read_from_the_reply(self):
        self.assertEqual(forge._edit_outcome("replace: a.py; sha256 ab → cd; 3 байт."), "confirmed")
        self.assertEqual(forge._edit_outcome("Исход правки неизвестен: замок"), "in_doubt")
        self.assertEqual(forge._edit_outcome("Нет файла a.py."), "refused")
        self.assertEqual(forge._edit_outcome("Patch применён.\nM a"), "ok")

    def _remote(self, reply):
        with Slot() as report:
            forge._note_remote_run("make", ".", reply)
        return report

    def test_a_remote_success_is_ok_and_names_the_command(self):
        report = self._remote('{"status": "done", "cwd": "/x", "remote_id": "op-1", "remote_result": {"exit": 0}}')
        self.assertEqual((report["outcome"], report["target"]), ("ok", "/x"))
        self.assertEqual(report["executions"][0]["command"], "make")

    def test_a_remote_nonzero_exit_is_failed(self):
        self.assertEqual(self._remote('{"status": "done", "remote_result": {"exit": 2}}')["outcome"], "failed")

    def test_a_remote_unknown_is_in_doubt(self):
        self.assertEqual(self._remote('{"status": "unknown"}')["outcome"], "in_doubt")

    def test_a_remote_refusal_text_is_refused(self):
        self.assertEqual(self._remote("Run refused: busy")["outcome"], "refused")

    @unittest.skipIf(os.name == "nt", "POSIX shell only")
    def test_forge_runs_bash(self):
        self.assertTrue(str(forge._FORGE_SHELL or "").endswith("bash"))


if __name__ == "__main__":
    unittest.main()
