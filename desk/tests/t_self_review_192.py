"""Self-review consumer flow with the real Forge admission and fake provider only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desk"))
from localharness import self_review, self_review_worker


def load_forge():
    dependencies = ("forge_intelligence", "forge_learning", "forge_swarm", "body_client",
                    "computer_memory", "serverd_client", "selfdev", "process_liveness")
    replacements = {name: types.ModuleType(name) for name in dependencies}
    replacements["process_liveness"].is_process_alive = lambda pid: True
    spec = importlib.util.spec_from_file_location("self_review_test_forge", ROOT / "helene/core/forge.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, replacements):
        spec.loader.exec_module(module)
    return module


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.code = self.root / "selected-preserved"
        self.app = self.root / "package/app"
        self.code.mkdir()
        self.app.mkdir(parents=True)
        (self.code / "agent.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.app / "runner.py").write_text("APP = 2\n", encoding="utf-8")
        self.forge = load_forge()
        self.forge.TASKS_DIR = self.root / "home/memory/.forge/tasks"
        self.forge.BASE = self.root / "home"
        self.forge._event = mock.Mock()
        self.forge.emit_unit_event = mock.Mock()
        self.forge._proc_started_at = lambda pid: "test-birth"
        self.forge._pid_alive = lambda *args: True
        self.spawns = []

        def spawn(script, request, log):
            self.spawns.append((script, request, json.loads(request.read_text(encoding="utf-8"))))
            return 99999999

        self.forge._spawn_runner = mock.Mock(side_effect=spawn)
        self.service = self_review.Service(self.root / "home", self.code, self.app.parent, self.forge)

    def request(self, **kwargs):
        return self.service.request(**kwargs)

    def result(self, record, index=0, *, verdict="clear", status="done", complete=True, gaps=None):
        critic = record["critics"][index]
        directory = self.forge._unit_dir(record["task_id"], "agents", critic["unit_id"])
        findings = [{"path": "agent/agent.py", "scenario": "a reachable input", "detail": "concrete defect"}] if verdict == "findings" else []
        data = {"status": status, "complete": complete, "subject_sha256": record["subject_sha256"],
                "snapshot_verified": True, "model": "actual-model",
                "result": json.dumps({"subject_sha256": record["subject_sha256"], "verdict": verdict,
                                      "summary": "source-only result", "findings": findings, "gaps": gaps or []})}
        directory.mkdir(parents=True, exist_ok=True)
        self_review._write(directory / "result.json", data)
        return directory / "result.json"

    def test_four_triggers_and_contested_optional_off(self):
        self.assertEqual(self.request(trigger="after_update")["state"], "disabled")
        for key in ("merged", "conflicts", "no_base", "materials_error", "skipped_code"):
            row = self.request(trigger="after_update", operation_id=key, overlay={key: ["agent.py"]})
            self.assertTrue(row["required"])
            self.assertIn("contested_overlay", row["triggers"])
        self.assertEqual(self.request(trigger="after_self_edit")["trigger"], "after_self_edit")
        self.assertIn("on_request", self.request()["triggers"])
        self.service.settings({"after_update": True})
        self.assertEqual(self.request(trigger="after_update")["trigger"], "after_update")

    def test_preserved_source_only_projection_no_memory_or_credentials(self):
        (self.code / "memory").mkdir()
        (self.code / "memory/private.py").write_text("SECRET_MEMORY", encoding="utf-8")
        for name in (".env", "AGENTS.md", "helene.json", "llm.json"):
            (self.code / name).write_text("PRIVATE_NEVER_MODEL", encoding="utf-8")
        (self.code / "credential.py").write_text("PRIVATE_NEVER_MODEL", encoding="utf-8")
        (self.code / "embedded.py").write_text('api_key = "sk-12345678901234567890"', encoding="utf-8")
        row = self.request()
        subject = self_review.validate_snapshot(row["snapshot"], row["subject_sha256"])
        paths = {entry["path"] for entry in subject["manifest"]}
        self.assertIn("agent/agent.py", paths)
        self.assertNotIn("agent/embedded.py", paths)
        self.assertTrue(subject["gaps"])
        self.assertNotIn("PRIVATE_NEVER_MODEL", "".join(path.read_text(encoding="utf-8") for path in Path(row["snapshot"]).rglob("*") if path.is_file()))

    def test_idempotent_operation_and_identical_bytes_do_not_spawn_twice(self):
        first = self.request(operation_id="request-1")
        with mock.patch.object(self_review, "collect", side_effect=AssertionError("must not resnapshot")):
            self.assertEqual(self.request(operation_id="request-1")["id"], first["id"])
        self.assertEqual(self.request()["id"], first["id"])
        self.service.tick()
        self.service.tick()
        self.assertEqual(len(self.spawns), 1)
        recovered = self_review.Service(self.root / "home", self.code, self.app.parent, self.forge)
        recovered.tick()
        self.assertEqual(len(self.spawns), 1)

    def test_reserved_id_crash_before_spawn_resumes_once(self):
        row = self.request()
        self.forge.self_review_task(row["task_id"], row["snapshot"], row["subject_sha256"],
                                   str(Path(self_review_worker.__file__)))
        self.service.tick()
        self.assertEqual(self.spawns[0][2]["id"], row["critics"][0]["unit_id"])
        self.assertEqual(self.spawns[0][0], Path(self_review_worker.__file__).resolve())
        self.assertEqual(self.spawns[0][2]["self_review"]["subject_sha256"], row["subject_sha256"])

    def test_unsupported_preserved_forge_is_unavailable_never_generic_fallback(self):
        self.forge.SELF_REVIEW_API = 0
        row = self.request()
        self.service.tick()
        result = self.service.status(row["id"])
        self.assertEqual((result["execution"], result["verdict"]), ("unavailable", "insufficient"))
        self.assertEqual(self.spawns, [])

    def test_snapshot_tamper_extra_file_and_mutation_invalidate(self):
        row = self.request()
        snapshot = Path(row["snapshot"])
        (snapshot / "agent/agent.py").write_text("VALUE = 9", encoding="utf-8")
        self.service.tick()
        self.assertEqual(self.service.status(row["id"])["execution"], "error")
        self.assertFalse(self.spawns)
        with self.assertRaises(ValueError):
            self_review.validate_snapshot(snapshot, row["subject_sha256"])

    def test_unexpected_snapshot_file_refused(self):
        row = self.request()
        (Path(row["snapshot"]) / "unexpected.py").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            self_review.validate_snapshot(row["snapshot"], row["subject_sha256"])

    def test_credential_in_prompt_and_nonobject_overlay_refused(self):
        with self.assertRaises(ValueError):
            self.request(intent="credential sk-12345678901234567890")
        with self.assertRaises(ValueError):
            self.request(overlay=["conflict"])

    def test_missing_overlay_sides_is_attention_not_clear(self):
        row = self.request(overlay={"conflicts": ["agent.py"]})
        self.service.tick()
        self.result(row)
        self.service.tick()
        self.assertEqual(self.service.status(row["id"])["verdict"], "insufficient")

    def test_actual_overlay_sidefiles_are_exact_code_only_inputs(self):
        materials = self.root / "carry-materials"
        materials.mkdir()
        for suffix, content in (("base", "BASE = 1"), ("mine", "MINE = 2"), ("theirs", "RELEASE = 3")):
            (materials / ("agent.py." + suffix)).write_text(content, encoding="utf-8")
        (materials / "helene.json.mine").write_text("PRIVATE_CONFIG", encoding="utf-8")
        sources = {"base": {"root": materials, "files": {"agent.py": "agent.py.base"}},
                   "mine": {"root": materials, "files": {"agent.py": "agent.py.mine"}},
                   "release": {"root": materials, "files": {"agent.py": "agent.py.theirs"}},
                   "resolved": {"root": self.code, "files": {"agent.py": "agent.py"}}}
        row = self.request(overlay={"conflicts": ["agent.py"]}, sources=sources)
        subject = self_review.validate_snapshot(row["snapshot"], row["subject_sha256"])
        self.assertEqual(subject["gaps"], [])
        self.assertEqual((Path(row["snapshot"]) / "mine/agent.py").read_text(encoding="utf-8"), "MINE = 2")
        self.assertFalse((Path(row["snapshot"]) / "mine/helene.json").exists())
        self.assertFalse(self.service.freshness(row["id"])["stale"])
        self.service.tick()
        self.result(row)
        self.service.tick()
        self.assertEqual(self.service.status(row["id"])["verdict"], "clear")
        (materials / "agent.py.mine").write_text("CHANGED = 8", encoding="utf-8")
        self.assertTrue(self.service.freshness(row["id"])["stale"])
        with self.assertRaises(ValueError):
            self.request(sources={"mine": {"root": materials, "files": {"agent.py": "../outside.py"}}})
        with self.assertRaises(ValueError):
            self.request(sources={"mine": {"root": materials, "files": {"helene.json": "helene.json.mine"}}})
        disguised = self.request(sources={"mine": {"root": materials, "files": {"agent.py": "helene.json.mine"}}})
        self.assertFalse((Path(disguised["snapshot"]) / "mine/agent.py").exists())

    def test_forge_admits_only_reviewers_and_only_installed_adapter(self):
        row = self.request()
        self.service.tick()
        self.assertIn("read-only", self.forge.agent(row["task_id"], "spawn", role="worker", brief="edit"))
        spoof = self.root / "localharness/self_review_worker.py"
        spoof.parent.mkdir()
        spoof.write_text("raise Exception('not trusted')", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.forge.self_review_task("code-aaaaaaaa", row["snapshot"], row["subject_sha256"], str(spoof))

    def test_indexed_conflict_side_groups_must_all_match(self):
        sources = {}
        for index in range(2):
            material = self.root / f"materials-{index}"
            material.mkdir()
            for side in ("base", "mine", "release", "resolved"):
                filename = "agent.py." + ("theirs" if side == "release" else side)
                (material / filename).write_text(f"SIDE = {side!r}\n", encoding="utf-8")
                sources[f"{side}-{index}"] = {"root": material, "files": {"agent.py": filename}}
        row = self.request(overlay={"conflicts": [{"path": "tree/agent.py"}, {"path": "app/agent.py"}]}, sources=sources)
        subject = self_review.validate_snapshot(row["snapshot"], row["subject_sha256"])
        self.assertEqual(subject["gaps"], [])
        sources.pop("resolved-1")
        incomplete = self.request(overlay={"conflicts": [{"path": "tree/agent.py"}, {"path": "app/agent.py"}]}, sources=sources)
        subject = self_review.validate_snapshot(incomplete["snapshot"], incomplete["subject_sha256"])
        self.assertTrue(any(gap["path"] == "overlay" for gap in subject["gaps"]))

    def test_byte_change_same_timestamp_stales_and_deletions_have_diff(self):
        first = self.request()
        self.service.tick()
        self.result(first)
        self.service.tick()
        old_stat = (self.code / "agent.py").stat()
        (self.code / "agent.py").write_text("VALUE = 9\n", encoding="utf-8")
        os.utime(self.code / "agent.py", ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertTrue(self.service.freshness(first["id"])["stale"])
        with self.assertRaises(ValueError):
            self.service.disposition(first["id"], "accepted", "reviewed", first["subject_sha256"])
        (self.code / "agent.py").unlink()
        (self.code / "new.py").write_text("NEW = True\n", encoding="utf-8")
        second = self.request()
        diff = (Path(second["snapshot"]) / "changes.diff").read_text(encoding="utf-8")
        self.assertIn("-VALUE = 1", diff)
        self.assertIn("+NEW = True", diff)

    def test_result_execution_verdict_and_disposition_are_separate(self):
        row = self.request()
        self.service.tick()
        result_path = self.result(row, verdict="findings")
        raw = result_path.read_bytes()
        self.service.tick()
        result = self.service.status(row["id"])
        self.assertEqual((result["execution"], result["verdict"]), ("done", "findings"))
        self.assertEqual(result["critics"][0]["result_sha256"], hashlib.sha256(raw).hexdigest())
        with self.assertRaises(ValueError):
            self.service.disposition(row["id"], "accepted", "ignore", row["subject_sha256"])
        result = self.service.disposition(row["id"], "continue-with-reason", "explicit author choice", row["subject_sha256"])
        self.assertEqual(result["verdict"], "findings")
        self.assertEqual(result_path.read_bytes(), raw)

    def test_changed_canonical_result_refuses_disposition(self):
        row = self.request()
        self.service.tick()
        path = self.result(row)
        self.service.tick()
        path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.service.disposition(row["id"], "accepted", "understood", row["subject_sha256"])

    def test_explicit_retry_is_not_automatic_or_duplicate_live_attempt(self):
        row = self.request()
        with self.assertRaises(ValueError):
            self.request(retry=True)
        self.service.tick()
        self.result(row, status="error", complete=False)
        self.service.tick()
        self.assertEqual(self.request()["id"], row["id"])
        retry = self.request(retry=True)
        self.assertNotEqual(retry["id"], row["id"])

    def test_empty_malformed_stalled_unavailable_never_clear(self):
        for status, complete, raw in (("done", True, ""), ("done", True, "not json"),
                                      ("stalled", False, "clear"), ("error", False, "")):
            with self.subTest(status=status, raw=raw):
                (self.code / "agent.py").write_text(repr((status, complete, raw)), encoding="utf-8")
                row = self.request()
                self.service.tick()
                path = self.result(row, status=status, complete=complete)
                value = self_review._read(path)
                value["result"] = raw
                self_review._write(path, value)
                self.service.tick()
                self.assertEqual(self.service.status(row["id"])["verdict"], "insufficient")

    def test_deep_disagreement_and_serial_forge_admission(self):
        row = self.request(depth="deep")
        self.service.tick()
        self.assertEqual(len(self.spawns), 1)
        self.result(row, verdict="clear")
        self.service.tick()
        self.assertEqual(len(self.spawns), 2)
        self.result(row, index=1, verdict="findings")
        self.service.tick()
        result = self.service.status(row["id"])
        self.assertEqual(result["verdict"], "findings")
        self.assertEqual([critic["verdict"] for critic in result["critics"]], ["clear", "findings"])

    def test_cancel_before_start_never_resurrects(self):
        row = self.request()
        self.service.cancel(row["id"])
        self.service.tick()
        self.assertFalse(self.spawns)
        self.assertEqual(self.service.status(row["id"])["execution"], "cancelled")

    def test_live_cancel_uncertainty_is_retained_without_resurrection(self):
        row = self.request()
        self.service.tick()
        with mock.patch.object(self.forge, "agent", return_value="unknown: stop not proven"), \
             mock.patch.object(self.forge, "_unit_state", return_value={"status": "unknown"}):
            cancelled = self.service.cancel(row["id"])
        self.assertTrue(cancelled["cancel_requested"])
        self.assertEqual(cancelled["execution"], "unknown")
        self.service.tick()
        self.assertEqual(len(self.spawns), 1)

    def test_actual_forge_stop_retains_uncertainty_after_late_done(self):
        row = self.request()
        self.service.tick()
        unit = row['critics'][0]['unit_id']
        directory = self.forge._unit_dir(row['task_id'], 'agents', unit)
        with mock.patch.object(self.forge, '_kill_tree', return_value='unknown: fixture access denied'):
            self.forge.agent(row['task_id'], 'stop', agent_id=unit)
        receipt = self_review._read(directory / 'stop.json')
        self.assertEqual(receipt['state'], 'unresolved')
        self.result(row)
        self.assertEqual(self.forge._unit_state(directory)['status'], 'unknown')
        polled = self.forge.agent(row['task_id'], 'poll', agent_id=unit)
        self.assertEqual(json.loads(polled.split('\n--- worker log ---')[0])['status'], 'unknown')
        # Repeating Stop against an already-written result still needs real
        # process exit evidence; the terminal result is never that evidence.
        with mock.patch.object(self.forge, '_kill_tree', return_value='unknown: fixture access denied'):
            self.forge.agent(row['task_id'], 'stop', agent_id=unit)
        self.assertEqual(self_review._read(directory / 'stop.json')['state'], 'unresolved')
        self.assertEqual(self_review._read(directory / 'result.json')['status'], 'done')
        self.service.tick()
        observed = self.service.status(row['id'])
        self.assertNotEqual(observed['execution'], 'done')
        self.assertEqual(observed['verdict'], 'insufficient')
        self.assertEqual(observed['critics'][0]['execution'], 'unknown')
        self.assertTrue(observed['critics'][0]['result_sha256'])
        with self.assertRaises(ValueError):
            self.service.disposition(row['id'], 'accepted', 'cannot infer exit', row['subject_sha256'])

    def test_readonly_forged_tools_paths_and_live_lessons_refused(self):
        row = self.request()
        reviewer = self_review_worker.SnapshotReviewer({"root": row["snapshot"], "subject_sha256": row["subject_sha256"]})
        for tool in ("run", "edit", "delegate", "process", "signal"):
            self.assertIn("Refused", reviewer.dispatch({}, tool, {}))
        for path in ("../private", "C:/private"):
            with self.assertRaises(ValueError):
                reviewer.dispatch({}, "inspect", {"action": "read", "path": path})
        self.assertIn("Refused", reviewer.dispatch({}, "inspect", {"action": "lessons"}))
        self.assertNotIn("PRIVATE", reviewer.system({"brief": "inspect"}))

    def test_unavailable_cannot_be_accepted_and_settings_preserve_unknown(self):
        self.assertEqual(self_review.settings({"self_review": {"future": 4}})["future"], 4)
        for config in ({"models": ["openai::model"]}, {"max_iters": 0}, {"after_update": "yes"}):
            with self.assertRaises(ValueError):
                self_review.settings(config)
        row = self.request()
        with self.assertRaises(ValueError):
            self.service.disposition(row["id"], "accepted", "too early", row["subject_sha256"])

    def test_actual_service_forge_worker_fake_provider_readonly_result(self):
        row = self.request()
        self.service.tick()
        script, request_path, request = self.spawns[0]
        self.assertEqual(script, Path(self_review_worker.__file__).resolve())
        run_context = types.ModuleType("run_context")
        import contextlib
        run_context.bind_run = lambda value: contextlib.nullcontext()
        llm = types.ModuleType("llm")
        llm.BrokenChannelError = type("BrokenChannelError", (RuntimeError,), {})
        llm.limits = lambda: types.SimpleNamespace(max_tool_iters=10)
        llm.snapshot = lambda: {"voice": {"framework": "fake-route", "model": "fake-model"}}
        seen = []

        def chat(role, **kwargs):
            seen.append(kwargs)
            self.assertEqual(kwargs["framework"], "fake-route")
            self.assertIs(kwargs["allow_fallback"], False)
            if len(seen) == 1:
                return types.SimpleNamespace(model="fake-provider-model", stop_reason="tool_use", text="",
                    continuation={'scope': 'fixture-route', 'format': 'responses',
                                  'payload': [{'type': 'reasoning', 'encrypted_content': 'opaque-fixture'}]}, blocks=[
                    {"type": "tool_use", "id": "inspect-1", "name": "inspect", "input": {"action": "read", "path": "agent/agent.py"}},
                    {"type": "tool_use", "id": "forged-2", "name": "edit", "input": {"path": "agent/agent.py", "content": "BAD"}}])
            self.assertEqual(kwargs['messages'][1]['provider_continuation']['payload'][0]['encrypted_content'], 'opaque-fixture')
            return types.SimpleNamespace(model="fake-provider-model", stop_reason="end_turn", blocks=[],
                text=json.dumps({"subject_sha256": row["subject_sha256"], "verdict": "clear", "summary": "source-only inspected",
                                 "findings": [], "gaps": []}))

        # Exercise the actual adapter's sidecar constructor, not a second
        # test implementation of protocol reconstruction.
        import ast
        import copy
        source = ast.parse((ROOT / 'helene/core/llm.py').read_text(encoding='utf-8'))
        helpers = [node for node in source.body if isinstance(node, ast.FunctionDef)
                   and node.name in {'assistant_message', '_wire_value'}]
        namespace = {'LLMResponse': object, 'copy': copy, 'json': json}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'llm.py', 'exec'), namespace)
        llm.assistant_message = namespace['assistant_message']
        llm.chat = chat
        spec = importlib.util.spec_from_file_location("self_review_test_worker", ROOT / "praxis/forge_worker.py")
        worker = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"forge": self.forge, "llm": llm, "run_context": run_context}):
            spec.loader.exec_module(worker)
            with mock.patch.object(self.forge, "inspect", side_effect=AssertionError("live inspect forbidden"), create=True):
                previous_cwd = Path.cwd()
                previous_path = sys.path[:]
                try:
                    with mock.patch.dict(sys.modules, {"forge_worker": worker}):
                        self.assertEqual(self_review_worker.run(request_path), 0)
                finally:
                    os.chdir(previous_cwd)
                    sys.path[:] = previous_path
        self.service.tick()
        result = self.service.status(row["id"])
        self.assertEqual((result["execution"], result["verdict"]), ("done", "clear"))
        self.assertEqual(result["critics"][0]["model_actual"], "fake-provider-model")
        self.assertIn("Refused", str(seen[1]["messages"]))
        self.assertEqual((self.code / "agent.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        accepted = self.service.disposition(row["id"], "accepted", "source-only result understood", row["subject_sha256"])
        self.assertEqual(accepted["verdict"], "clear")


if __name__ == "__main__":
    unittest.main(verbosity=2)
