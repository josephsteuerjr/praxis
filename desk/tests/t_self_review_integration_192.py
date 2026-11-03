"""Single-process lifecycle and authenticated consumer proofs; no model calls."""
import ast
from contextvars import ContextVar
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
sys.path.insert(0, str(DESK / "localharness"))

from localharness import self_review, self_review_integration as integration
from deskd import agentcfg, readers


class Forge:
    SELF_REVIEW_API = 1

    def __init__(self, root):
        self.root, self.spawned, self.stopped = root, [], []

    def self_review_task(self, task_id, snapshot, subject, worker, parallel):
        self.current_subject = subject

    def _unit_dir(self, task_id, kind, unit_id):
        return self.root / task_id / kind / unit_id

    def _unit_state(self, directory):
        return {"status": "running"}

    def agent(self, task_id, action, **kwargs):
        if action == "stop":
            self.stopped.append(kwargs["agent_id"])
            return "stop requested"
        directory = self._unit_dir(task_id, "agents", kwargs["agent_id"])
        directory.mkdir(parents=True)
        self.spawned.append((task_id, kwargs))
        return "started"


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tree, self.code, self.app = (self.root / name for name in ("home", "code", "program"))
        self.tree.mkdir(); self.code.mkdir(); (self.app / "app").mkdir(parents=True)
        (self.code / "agent.py").write_text("value = 1\n")
        (self.app / "app" / "runner.py").write_text("value = 2\n")
        self.config = self.root / "helene.json"
        self.config.write_text(json.dumps({"self_review": {"depth": "quick", "after_self_edit": True}}))
        self.agent = types.SimpleNamespace(SELF_EDIT_REVIEW_API=1, TOOL_IMPL={}, BASE_TOOLS=[],
                                           _TURN_CHANNEL=ContextVar("owner-channel", default=None))
        self.forge = Forge(self.root / "forge")
        self.runtime = self.make_runtime()

    def make_runtime(self):
        return integration.Integration(self.tree, self.code, self.app, self.forge, self.config, self.agent).install()

    def tick(self, paused=False):
        self.runtime.next_tick = 0
        self.runtime.tick(paused=paused)

    def test_owner_command_drives_forge_and_restart_does_not_respawn(self):
        asked = integration.command(self.tree, {"action": "request", "intent": "check edits"})
        self.assertEqual(integration.status(self.tree)["commands"][0]["state"], "queued")
        self.tick()
        state = integration.status(self.tree)
        self.assertEqual(state["commands"][0]["state"], "done")
        self.assertEqual(len(self.forge.spawned), 1)
        self.assertIn(asked["command_id"], state["reviews"][0]["operations"])
        self.runtime = self.make_runtime()
        self.tick()
        self.assertEqual(len(self.forge.spawned), 1)
        self.assertEqual(integration.status(self.tree)["reviews"][0]["execution"], "running")

    def test_paused_runtime_persists_requests_without_admitting_workers(self):
        integration.command(self.tree, {"action": "request"})
        self.tick(paused=True)
        self.assertFalse(self.forge.spawned)
        self.assertEqual(self.runtime.service.status()[0]["state"], "pending")
        self.tick()
        self.assertEqual(len(self.forge.spawned), 1)

    def test_contested_update_required_even_when_optional_off_and_reconnect_is_idempotent(self):
        receipt = {"id": "upgrade", "state": "done", "to_version": "1.9.2", "agent_code": {"merged": ["agent.py"]}}
        record = self.runtime.observe_update(receipt)
        self.assertTrue(record["required"])
        self.assertEqual(record["trigger"], "contested_overlay")
        self.runtime = self.make_runtime()
        self.assertIsNone(self.runtime.observe_update(receipt))
        self.assertEqual(len(self.runtime.service.status()), 1)

    def test_skipped_codecarry_inputs_are_not_silently_optional(self):
        record = self.runtime.observe_update({"id": "skip", "state": "done", "agent_code": {"skipped": ["app/strange"]}})
        self.assertTrue(record["required"])

    def test_latest_receipt_is_observed_even_when_notice_already_marked_reported(self):
        from deskd import control
        self_review._write(self.tree / "memory/.control" / control.UPDATE_RECEIPT,
                           {"id": "old", "state": "done", "agent_code": {"no_base": "missing"}})
        self.tick(paused=True)
        self.assertTrue(self.runtime.service.status()[0]["required"])

    def test_real_codecarry_metadata_freezes_exact_tree_and_app_conflict_sides(self):
        materials = self.tree / "workspace/update-1.9.2"
        conflicts = []
        for kind, name, resolved in (("tree", "agent.py", self.code), ("app", "runner.py", self.app / "app")):
            target = materials / kind; target.mkdir(parents=True)
            for side, value in (("base", 0), ("mine", 1), ("theirs", 2)):
                (target / (name + "." + side)).write_text(f"value = {value}\n")
            conflicts.append({"path": kind + "/" + name, "why": "both changed"})
        record = self.runtime.observe_update({"id": "actual-shape", "state": "done", "agent_code": {
            "folder": "workspace/update-1.9.2", "conflicts": conflicts}})
        subject = json.loads((Path(record["snapshot"]) / "subject.json").read_text())
        paths = {row["path"] for row in subject["manifest"]}
        for index, name in enumerate(("agent.py", "runner.py")):
            for side in ("base", "mine", "release", "resolved"):
                self.assertIn(f"{side}-{index}/{name}", paths)
        self.assertFalse(any(gap["path"] == "overlay" for gap in subject["gaps"]), subject["gaps"])

    def test_conflict_materials_cannot_read_outside_workspace(self):
        record = self.runtime.observe_update({"id": "bad-folder", "state": "done", "agent_code": {
            "folder": "../elsewhere", "conflicts": [{"path": "tree/agent.py"}]}})
        self.assertTrue(record["required"])
        self.assertTrue(any(gap["path"] == "overlay" for gap in record["gaps"]))

    def test_private_conflict_is_durable_required_gap_not_intake_failure(self):
        for index, name in enumerate(('AGENTS.md', 'memory/private.py')):
            with self.subTest(name=name):
                receipt = {'id': f'private-{index}', 'state': 'done', 'agent_code': {
                    'folder': 'workspace/update-private', 'conflicts': [{'path': 'tree/' + name}]}}
                row = self.runtime.observe_update(receipt)
                self.assertTrue(row['required'])
                self.assertTrue(any(gap['path'] == 'overlay' for gap in row['gaps']))
                self.assertEqual(self.runtime.service.status(row['id'])['id'], row['id'])
                subject = self_review.validate_snapshot(row['snapshot'], row['subject_sha256'])
                self.assertFalse(any(name in item['path'] for item in subject['manifest']))
                self.assertIsNone(self.runtime.observe_update(receipt))

    def test_disabled_optional_update_does_not_freeze_code_or_repeat(self):
        receipt = {"id": "optional", "kind": "package", "state": "installed"}
        self.assertEqual(self.runtime.observe_update(receipt)["state"], "disabled")
        self.assertEqual(self.runtime.service.status(), [])
        self.assertIsNone(self.runtime.observe_update(receipt))

    def test_old_preserved_forge_reports_unavailable_without_startup_failure(self):
        self.forge.SELF_REVIEW_API = 0
        integration.command(self.tree, {"action": "request"})
        self.tick()
        state = integration.status(self.tree)
        self.assertFalse(state["capability"]["supported"])
        self.assertIn("preserved", state["capability"]["reason"])
        self.assertEqual(state["reviews"][0]["execution"], "unavailable")
        self.assertEqual(state["reviews"][0]["verdict"], "insufficient")

    def test_self_edit_callback_freezes_selected_source_and_skips_memory(self):
        record = self.agent.SELF_EDIT_REVIEW_HOOK("commit", "self-edit", ["agent.py"])
        self.assertEqual(record["trigger"], "after_self_edit")
        self.assertIsNone(self.agent.SELF_EDIT_REVIEW_HOOK("other", "memory", ["memory/journal/today.md"]))
        self.assertEqual(len(self.runtime.service.status()), 1)

    def test_agent_hand_requires_current_owner_context(self):
        hand = self.agent.TOOL_IMPL["self_review"]
        self.assertIn("owner dialogue", hand(action="request"))
        self.agent._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        self.assertEqual(json.loads(hand(action="request"))["state"], "queued")

    def test_config_reload_applies_without_restart_and_invalid_block_does_not_spawn(self):
        self.config.write_text(json.dumps({"self_review": {"after_self_edit": False}}))
        self.tick(paused=True)
        self.assertEqual(self.agent.SELF_EDIT_REVIEW_HOOK("sha", "edit", ["agent.py"])["state"], "disabled")

    def test_invalid_command_never_queues_or_calls_forge(self):
        for body in ({"action": "bad"}, {"action": "request", "depth": "ultra"},
                     {"action": "cancel", "id": "../other"}, {"action": "request", "retry": "true"},
                     {"action": "disposition", "id": "review-" + "a"*16, "decision": "accepted"}):
            with self.assertRaises(ValueError): integration.command(self.tree, body)
        self.assertFalse(self.forge.spawned)

    def test_actual_self_edit_function_uses_callback_not_legacy_paid_queue(self):
        source = ast.parse((DESK.parent / "helene/core/agent.py").read_text(encoding="utf8"))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "_autocommit_self_edit")
        callback, legacy = [], []
        namespace = {"selfgit": types.SimpleNamespace(changed_paths=lambda: ["agent.py"], snapshot=lambda msg: "sha"),
                     "tool_journal": lambda *args, **kwargs: None, "BASE": self.tree,
                     "SELF_EDIT_REVIEW_HOOK": lambda *args: callback.append(args),
                     "immune": types.SimpleNamespace(enqueue=lambda *args: legacy.append(args)), "log": logging.getLogger()}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "agent.py", "exec"), namespace)
        namespace["_autocommit_self_edit"]()
        self.assertEqual(callback, [("sha", "self-edit: agent.py", ["agent.py"])])
        self.assertEqual(legacy, [])

    def test_unified_hook_replaces_legacy_preshell_evaluator_but_uninstalled_behavior_remains(self):
        import os
        source = ast.parse((DESK.parent / "helene/core/agent.py").read_text(encoding="utf8"))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "_core_edit_check_on")
        namespace = {"os": os, "SELF_EDIT_REVIEW_HOOK": lambda *args: None}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "agent.py", "exec"), namespace)
        with patch.dict(os.environ, {"PRAXIS_CORE_EDIT_CHECK": "1"}):
            self.assertFalse(namespace["_core_edit_check_on"]())
            namespace["SELF_EDIT_REVIEW_HOOK"] = None
            self.assertTrue(namespace["_core_edit_check_on"]())

    def test_cancel_and_explicit_disposition_survive_channel_reconnect(self):
        integration.command(self.tree, {"action": "request"})
        self.tick()
        record = self.runtime.service.status()[0]
        integration.command(self.tree, {"action": "cancel", "id": record["id"]})
        self.tick()
        # This fake never confirms exit. Maintenance retries the SAME unit,
        # rather than treating a stop request as successful cancellation.
        self.assertTrue(self.forge.stopped)
        self.assertEqual(set(self.forge.stopped), {record['critics'][0]['unit_id']})
        self.assertEqual(self.runtime.service.status(record['id'])['execution'], 'unknown')
        self.assertEqual(len(self.forge.spawned), 1)
        integration.command(self.tree, {"action": "disposition", "id": record["id"],
                            "decision": "continue-with-reason", "reason": "review stopped; not a clear verdict",
                            "subject_sha256": record["subject_sha256"]})
        self.tick()
        state = integration.status(self.tree)
        self.assertEqual(state["reviews"][0]["dispositions"][0]["decision"], "continue-with-reason")
        self.assertEqual(state["reviews"][0]["verdict"], "insufficient")

    def test_accepted_cannot_substitute_for_pending_review(self):
        integration.command(self.tree, {"action": "request"})
        self.tick()
        record = self.runtime.service.status()[0]
        asked = integration.command(self.tree, {"action": "disposition", "id": record["id"],
                                    "decision": "accepted", "reason": "premature",
                                    "subject_sha256": record["subject_sha256"]})
        self.tick()
        command = next(row for row in integration.status(self.tree)["commands"] if row["command_id"] == asked["command_id"])
        self.assertEqual(command["state"], "error")
        self.assertEqual(self.runtime.service.status()[0]["dispositions"], [])


class Settings(unittest.TestCase):
    def test_local_full_document_writer_validates_before_replacing(self):
        from localharness import config_cli
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "helene.json"
            config.write_text('{"retained":true}')
            with self.assertRaises(ValueError):
                config_cli.save(config, '{"self_review":{"models":["https://other/route"]}}', None)
            self.assertEqual(config.read_text(), '{"retained":true}')
            result = config_cli.save(config, '{"self_review":{"depth":"quick"}}', None)
            self.assertTrue(result["ok"])
            self.assertFalse(json.loads(config.read_text())["self_review"]["after_self_edit"])

    def test_existing_cas_writer_validates_all_settings_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "helene.json"
            config.write_text(json.dumps({"model": {"model": "current"}}))
            with patch.object(readers, "config_path", return_value=config):
                before = config.read_bytes()
                result = agentcfg.save({"self_review": {"depth": "wrong"}, "model": {"model": "bad"}})
                self.assertFalse(result["ok"])
                self.assertEqual(config.read_bytes(), before)
                seen = agentcfg.state()["mtime_ns"]
                self.assertTrue(agentcfg.save({"self_review": {"depth": "deep"}}, seen)["ok"])
                self.assertEqual(json.loads(config.read_text())["self_review"]["depth"], "deep")
                self.assertEqual(agentcfg.save({"self_review": {}}, seen)["code"], "conflict")


class Channel(unittest.IsolatedAsyncioTestCase):
    async def test_real_authenticated_http_and_tunnel_share_durable_commands(self):
        import deskapp
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            config = tree / "helene.json"; config.write_text("{}")
            app = web.Application(middlewares=[deskapp.auth_middleware])
            for route in deskapp.ROUTES:
                if route.path == "/api/self-review":
                    app.router.add_route(route.method, route.path, deskapp._http_handler(route))
            with patch.object(readers, "tree", return_value=tree), patch.object(readers, "config_path", return_value=config), \
                 patch.object(deskapp, "TOKEN", "owner-fixture"), patch.object(deskapp, "_host_ok", return_value=True), \
                 patch.object(deskapp, "_device_ok", side_effect=lambda value: value == "device-fixture"):
                async with TestClient(TestServer(app)) as client:
                    for token in ("", "device-fixture"):
                        response = await client.post("/api/self-review", json={"action": "request"},
                                                     headers={"Authorization": "Bearer " + token})
                        self.assertEqual(response.status, 403)
                        response = await client.get("/api/self-review", headers={"Authorization": "Bearer " + token})
                        self.assertEqual(response.status, 403)
                    response = await client.post("/api/self-review", json={"action": "request", "depth": "quick"},
                                                 headers={"Authorization": "Bearer owner-fixture"})
                    self.assertEqual(response.status, 200)
                    queued = await response.json()
                    response = await client.get("/api/self-review", headers={"Authorization": "Bearer owner-fixture"})
                    self.assertEqual((await response.json())["commands"][0]["command_id"], queued["command_id"])
                    tunnel = await deskapp._tunnel_dispatch("GET", "/api/self-review", None, role="owner")
                    self.assertEqual(tunnel["body"]["commands"][0]["command_id"], queued["command_id"])
                    self.assertEqual((await deskapp._tunnel_dispatch("POST", "/api/self-review", {"action": "request"}, role="device"))["status"], 403)
                    self.assertEqual((await deskapp._tunnel_dispatch("GET", "/api/self-review", None, role="device"))["status"], 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
