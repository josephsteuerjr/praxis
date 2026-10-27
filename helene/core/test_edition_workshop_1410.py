"""Hélène edition workshop contract; safe offline tests, no native process spawn."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import forge


class EditionWorkshop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        old = forge.TASKS_DIR
        forge.TASKS_DIR = self.root / "tasks"
        self.addCleanup(setattr, forge, "TASKS_DIR", old)
        self.task_id = "code-12345678"
        self.directory = forge.TASKS_DIR / self.task_id
        self.directory.mkdir(parents=True)
        (self.directory / "task.json").write_text(json.dumps({
            "id": self.task_id, "goal": "edition workshop", "root": str(self.root),
            "status": "active", "max_active_workers": 2, "run_context": {},
        }), encoding="utf-8")

    def unit(self, ident, parent="", result=None):
        directory = self.directory / "agents" / ident
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({
            "id": ident, "task_id": self.task_id, "parent_unit_id": parent,
            "role": "worker", "brief": "work", "created": "now",
            "status": "running", "supervisor_pid": 71, "supervisor_started_at": "born",
        }), encoding="utf-8")
        if result is not None:
            (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return directory

    def test_model_schema_can_reach_dispatch_and_receipt(self):
        named = {t["name"]: t for t in agent.WORKSHOP_TOOLS + agent.FORGE_TOOLS}
        self.assertIn("expected_sha256", named["fs_write"]["input_schema"]["properties"])
        self.assertIn("max_active_workers", named["coding_session"]["input_schema"]["properties"])
        self.assertIn("disposition", named["coding_agent"]["input_schema"]["properties"])
        self.assertIn("expected_sha256", named["coding_inspect"]["input_schema"]["properties"])
        offered = {t["name"]: t for t in agent.catalog_tools_for(agent.ChannelContext(owner=True))}
        self.assertTrue(set(named) <= set(offered), "edition catalog must offer the same tools the dispatcher implements")
        with mock.patch.object(agent.stewardship, "check", return_value=None), \
             mock.patch.object(agent, "_pre_shell_dirt", return_value=set()), \
             mock.patch.object(agent.workshop, "fs_write_result", return_value={
                 "status": "confirmed", "message": "saved", "after_sha256": "f" * 64}) as write, \
             mock.patch.object(agent, "_autocommit_self_edit") as commit:
            receipt = agent.tool_fs_write("workspace/example", "hello", expected_sha256="a" * 64)
        write.assert_called_once()
        self.assertEqual(write.call_args.kwargs["expected_sha256"], "a" * 64)
        commit.assert_called_once()
        self.assertEqual(json.loads(receipt.split("\n", 1)[1])["after_sha256"], "f" * 64)
        with mock.patch.object(agent.forge, "agent", return_value="receipt") as call:
            self.assertEqual(agent.tool_coding_agent(self.task_id, "disposition",
                              agent_id="agent-aaaaaaaa", result_sha256="a" * 64,
                              disposition="integrated"), "receipt")
        self.assertEqual(call.call_args.kwargs["disposition"], "integrated")

    def test_native_v2_input_events_have_model_fields(self):
        catalog = {t["name"]: t for t in agent.catalog_tools_for(agent.ChannelContext(owner=True))}
        fields = catalog["computer"]["input_schema"]["properties"]["events"]["items"]["properties"]
        self.assertTrue({"mouse_down", "mouse_up", "drag"} <= set(fields["type"]["enum"]))
        self.assertTrue({"to_x", "to_y", "steps", "hold_ms", "step_delay_ms", "settle_ms"}
                        <= set(fields))

    def test_worker_parent_slots_and_exact_disposition(self):
        parent = self.unit("agent-aaaaaaaa")
        with mock.patch.object(forge, "_event"), \
             mock.patch.object(forge, "_proc_started_at", return_value="born"), \
             mock.patch.object(forge, "_pid_alive", return_value=True), \
             mock.patch.object(forge, "_spawn_runner", return_value=72):
            response = forge.agent(self.task_id, "spawn", agent_id="agent-bbbbbbbb", brief="child", role="scout",
                                   _parent_request_path=str(parent / "request.json"))
            self.assertIn("agent-bbbbbbbb", response)
            request = json.loads((self.directory / "agents/agent-bbbbbbbb/request.json").read_text())
            self.assertEqual(request["parent_unit_id"], "agent-aaaaaaaa")
            self.assertIn("2/2", forge.agent(self.task_id, "spawn", brief="third"))
        child = self.directory / "agents/agent-bbbbbbbb"
        (child / "result.json").write_text(json.dumps({"status": "done", "result": "keep"}), encoding="utf-8")
        original = (child / "result.json").read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        with mock.patch.object(forge, "_event"):
            answer = forge.agent(self.task_id, "disposition", agent_id="agent-bbbbbbbb",
                                 result_sha256=digest, disposition="integrated",
                                 _parent_request_path=str(parent / "request.json"))
        self.assertIn(digest, answer)
        self.assertEqual(original, (child / "result.json").read_bytes())
        self.assertIn("integrated", forge.agent(self.task_id, "poll", agent_id="agent-bbbbbbbb",
                               _parent_request_path=str(parent / "request.json")))

    def test_process_stop_unknown_blocks_finish_then_preserves_natural_result(self):
        directory = self.directory / "processes" / "proc-aaaaaaaa"
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({
            "id": directory.name, "task_id": self.task_id, "command": "sleep 30",
            "supervisor_pid": 71, "supervisor_started_at": "born",
        }), encoding="utf-8")
        (directory / "state.json").write_text(json.dumps({"child_pid": 72,
            "child_started_at": "born"}), encoding="utf-8")
        with mock.patch.object(forge, "_pid_alive", return_value=False), \
             mock.patch.object(forge, "_kill_tree", side_effect=[
                 "не остановлен: child unknown", "остановлен", "остановлен"]), \
             mock.patch.object(forge, "_event"):
            first = forge.process(self.task_id, "stop", process_id=directory.name)
            self.assertIn("НЕ записан", first)
            self.assertEqual(forge._unit_state(directory)["status"], "unknown")
            self.assertIn("blocked", forge._finish_survey(self.task_id))
            prior = {"status": "done", "code": 0, "result": "retained"}
            (directory / "result.json").write_text(json.dumps(prior), encoding="utf-8")
            forge.process(self.task_id, "stop", process_id=directory.name)
        self.assertEqual(json.loads((directory / "result.json").read_text()), prior)
        self.assertEqual(json.loads((directory / "stop.json").read_text())["state"], "settled")

    def test_late_process_stop_keeps_existing_result_without_unknown_marker(self):
        directory = self.directory / "processes" / "proc-late000"
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({
            "id": directory.name, "task_id": self.task_id, "command": "finished",
            "supervisor_pid": 71, "supervisor_started_at": "old-birth",
        }), encoding="utf-8")
        (directory / "result.json").write_text(json.dumps({"status": "done", "code": 0}), encoding="utf-8")
        with mock.patch.object(forge, "_kill_tree") as kill:
            self.assertIn("Stop не послан", forge.process(self.task_id, "stop", process_id=directory.name))
        kill.assert_not_called()
        self.assertFalse((directory / "stop.json").exists())
        self.assertEqual(forge._unit_state(directory)["status"], "done")

    def test_verify_stop_marks_unknown_without_killing_supervisor_group(self):
        directory = self.directory / "verifications" / "verify-pending"
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({
            "id": directory.name, "task_id": self.task_id, "supervisor_pid": 71,
            "supervisor_started_at": "born", "status": "running",
        }), encoding="utf-8")
        with mock.patch.object(forge, "_kill_tree") as kill, \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge.time, "monotonic", side_effect=[0.0, 9.0]):
            response = forge.verify(self.task_id, "stop", verification_id=directory.name)
        self.assertIn("НЕ записан", response)
        kill.assert_not_called()
        self.assertEqual(forge._unit_state(directory)["status"], "unknown")

    def test_large_read_pages_are_exact_bounded_and_revision_bound(self):
        path = self.root / "sample.py"
        content = "a" * 9000 + "\nnext\n"
        path.write_bytes(content.encode("utf-8"))
        first = json.loads(forge.inspect(self.task_id, "read", path="sample.py"))
        self.assertLessEqual(len(json.dumps(first, ensure_ascii=False)), 8000)
        self.assertEqual(first["start"], 1)
        rest = json.loads(forge.inspect(self.task_id, "read", path="sample.py",
                                        start=first["next_start"], start_char=first["next_char"],
                                        expected_sha256=first["sha256"]))
        self.assertEqual(first["text"] + rest["text"], content)
        path.write_bytes((content + "changed").encode("utf-8"))
        self.assertIn("Конфликт версии", forge.inspect(self.task_id, "read", path="sample.py",
                     start=first["next_start"], start_char=first["next_char"],
                     expected_sha256=first["sha256"]))

    def test_edition_edit_accepts_full_read_digest_and_rejects_patch_false_guard(self):
        path = self.root / "sample.py"
        path.write_text("old\n", encoding="utf-8")
        full = hashlib.sha256(path.read_bytes()).hexdigest()
        page = json.loads(forge.inspect(self.task_id, "read", path="sample.py",
                                        expected_sha256=full))
        with mock.patch.object(forge, "_event"):
            answer = forge._edit_unlocked(self.task_id, "replace", path="sample.py",
                                          old="old", new="new", expected_sha256=page["sha256"])
        self.assertIn("sha256", answer)
        self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
        refusal = forge._edit_unlocked(self.task_id, "patch", patch="not a diff",
                                       expected_sha256=full)
        self.assertIn("Patch не применён", refusal)

    def test_edition_dirty_tree_never_reuses_green_head(self):
        with mock.patch.object(forge, "_git_root", return_value=self.root), \
             mock.patch.object(forge, "_git_text", return_value=" M changed.py"):
            self.assertEqual(forge._task_tree_sha(self.root), "")

    def test_macos_group_census_rejects_unknown_and_detects_live(self):
        def output(text, code=0):
            return type("Result", (), {"stdout": text, "returncode": code})()
        with mock.patch.object(forge.sys, "platform", "darwin"), \
             mock.patch.object(forge.subprocess, "run", return_value=output("42 Z\n42 S\n")):
            self.assertTrue(forge._group_live_members(42))
        with mock.patch.object(forge.sys, "platform", "darwin"), \
             mock.patch.object(forge.subprocess, "run", return_value=output("42 Z\n")):
            self.assertFalse(forge._group_live_members(42))
        with mock.patch.object(forge.sys, "platform", "darwin"), \
             mock.patch.object(forge.subprocess, "run", return_value=output("garbled\n")):
            self.assertIsNone(forge._group_live_members(42))

    def test_package_composer_selects_edition_over_production(self):
        """The real installer selector, with a tiny source-bound manifest fixture."""
        module_path = next((root / 'desk/installer/candidate_tree.py'
                            for root in Path(__file__).resolve().parents
                            if (root / 'desk/installer/candidate_tree.py').is_file()), None)
        if module_path is None:
            self.skipTest('Packaging integration requires the full source checkout with desk/installer')
        spec = importlib.util.spec_from_file_location("edition_candidate_tree", module_path)
        composer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(composer)
        package_root = self.root / "package_source"
        edition = package_root / "helene/core"
        production = package_root / "praxis"
        edition.mkdir(parents=True)
        production.mkdir()
        for name in ("agent.py", "forge.py"):
            edition_bytes = (Path(__file__).parent / name).read_bytes()
            (edition / name).write_bytes(edition_bytes)
            (production / name).write_bytes(b"not the edition override")
        inventory = {name: hashlib.sha256((edition / name).read_bytes()).hexdigest()
                     for name in ("agent.py", "forge.py")}
        (package_root / "HELENE-SOURCE.json").write_text(json.dumps({
            "tree_inventory_scope": "published_payload", "frozen_tree_files": inventory,
            "version": "test", "frozen_tree": "test",
        }), encoding="utf-8")
        destination = self.root / "composed"
        self.assertEqual(composer.stage(destination, root=package_root)["files"], 2)
        for name in inventory:
            self.assertEqual((destination / name).read_bytes(), (edition / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
