# -*- coding: utf-8 -*-
"""Local-only deploy migration contract (no installed soul, credentials or network).

Run: python tests/t_vision_policy_migration_1510.py from desk/.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))
import boot
import vision_policy as vp


class Desk:
    stream = "window"

    def __init__(self, tree):
        self.tree = tree
        self.activity = []

    def archive(self, text, *, outgoing, now, sender, source_id):
        archive = self.tree / "memory/groups/window.jsonl"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("a", encoding="utf8") as f:
            f.write(json.dumps({"text": text, "source_id": source_id}) + "\n")
        self.activity.append(source_id)

    def life(self, text, **kw):
        self.activity.append("life:" + kw["source_id"])


class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-vision-policy-")
        self.tree = Path(self.tmp.name)
        self.target = self.tree / "memory/llm.json"
        self.target.parent.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("PRAXIS_VISION_PREPASS", None)
        self.acl = mock.patch.object(boot, "_own_only")
        self.acl.start()  # avoid invoking platform ACL commands in a unit test

    def tearDown(self):
        self.acl.stop()
        self.env.stop()
        self.tmp.cleanup()

    def profile(self, voice=None):
        self.target.write_text(json.dumps({"roles": {"voice": {"model": "primary", **(voice or {})}},
                              "frameworks": {"anthropic": {"api_key": "SENTINEL-NEVER-IN-EVENT"}},
                              "owner_extra": {"keep": [1, 2, 3]}}), encoding="utf8")

    def value(self):
        return json.loads(self.target.read_text(encoding="utf8"))["roles"]["voice"]["vision_prepass"]

    def events(self):
        journal = self.tree / "memory/.state/vision-policy.events.jsonl"
        return [json.loads(line) for line in journal.read_text("utf8").splitlines()]

    def test_old_profile_migrates_then_one_visible_receipt_with_undo_and_source(self):
        self.profile()
        vp.preflight(self.tree)
        self.assertTrue(vp.migrate(self.tree))
        self.assertIs(self.value(), True)
        self.assertEqual(json.loads(self.target.read_text("utf8"))["owner_extra"], {"keep": [1, 2, 3]})
        event = self.events()[0]
        self.assertEqual(event["author"], "deploy:" + hashlib.sha256(Path(vp.__file__).read_bytes()).hexdigest())
        self.assertEqual((event["before"], event["after"]), ("absent", True))
        self.assertNotIn("SENTINEL", json.dumps(event) + (self.tree / "memory/.state/vision-policy-notice.json").read_text("utf8"))
        desk = Desk(self.tree)
        now = dt.datetime.now(dt.timezone.utc)
        vp.notice(self.tree, desk, now)
        vp.notice(self.tree, desk, now)
        self.assertEqual(len(desk.activity), 2)  # one archive + one life
        archive = self.tree / "memory/groups/window.jsonl"
        self.assertIn("vision_prepass=false", archive.read_text("utf8"))
        self.assertFalse(vp.migrate(self.tree))
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(archive.read_text("utf8").splitlines()), 1)

    def test_explicit_false_and_true_are_respected_even_with_env_override(self):
        for value in (False, True):
            with self.subTest(value=value):
                self.profile({"vision_prepass": value})
                original = self.target.read_bytes()
                os.environ["PRAXIS_VISION_PREPASS"] = "1" if not value else "0"
                vp.preflight(self.tree)
                self.assertFalse(vp.migrate(self.tree))
                self.assertEqual(original, self.target.read_bytes())
                self.assertFalse((self.tree / "memory/.state/vision-policy-change.json").exists())

    def test_boot_projection_cannot_erase_explicit_false(self):
        self.profile({"vision_prepass": False})
        vp.preflight(self.tree)
        boot.project_brain(self.tree, {"model": {"model": "replacement-test-model"}})
        self.assertFalse(vp.migrate(self.tree))
        self.assertIs(self.value(), False)
        self.assertFalse(vp._notice(self.tree).exists())

    def test_env_off_writes_explicit_false_in_old_profile(self):
        self.profile()
        os.environ["PRAXIS_VISION_PREPASS"] = "0"
        self.assertTrue(vp.migrate(self.tree))
        self.assertIs(self.value(), False)
        self.assertFalse(self.events()[0]["after"])
        self.assertFalse(vp.migrate(self.tree))

    def test_dotenv_override_is_resolved_before_policy_receipt(self):
        from dotenv import load_dotenv
        for process, file_value, expected in (("1", "off", False), ("0", "on", True)):
            with self.subTest(process=process, file_value=file_value):
                self.profile()
                for path in (vp._marker(self.tree), vp._notice(self.tree),
                             self.tree / "memory/.state/vision-policy.events.jsonl"):
                    path.unlink(missing_ok=True)
                source = self.tree / "agent-code"
                source.mkdir(exist_ok=True)
                (source / ".env").write_text(
                    f"PRAXIS_VISION_PREPASS={file_value}\n", encoding="utf8")
                os.environ["PRAXIS_VISION_PREPASS"] = process
                self.assertTrue(vp.migrate(self.tree, code_dir=source, read_dotenv=True))
                self.assertIs(self.value(), expected)
                self.assertIs(self.events()[0]["after"], expected)
                # The actual agent import uses python-dotenv's override=True;
                # this must agree with the migration's pre-import interpretation.
                load_dotenv(source / ".env", override=True)
                self.assertIs(vp._value(), expected)

    def test_disabled_dotenv_ignores_file_and_invalid_enabled_value_refuses(self):
        self.profile()
        source = self.tree / "agent-code"
        source.mkdir()
        (source / ".env").write_text("PRAXIS_VISION_PREPASS=off\n", encoding="utf8")
        os.environ["PRAXIS_VISION_PREPASS"] = "1"
        self.assertTrue(vp.migrate(self.tree, code_dir=source, read_dotenv=False))
        self.assertIs(self.value(), True)
        self.target.unlink()
        self.profile()
        (source / ".env").write_text("PRAXIS_VISION_PREPASS=unknown\n", encoding="utf8")
        before = self.target.read_bytes()
        with self.assertRaises(vp.MigrationError):
            vp._value(code_dir=source, read_dotenv=True)
        self.assertEqual(self.target.read_bytes(), before)

    def test_parent_dotenv_matches_bare_agent_import_search(self):
        self.profile()
        parent = self.tree / "agent-source"
        code = parent / "core"
        code.mkdir(parents=True)
        (parent / ".env").write_text("PRAXIS_VISION_PREPASS=off\n", encoding="utf8")
        os.environ["PRAXIS_VISION_PREPASS"] = "1"
        self.assertEqual(boot.chosen_dotenv(code), parent / ".env")
        self.assertTrue(vp.migrate(self.tree, code_dir=code, read_dotenv=True))
        self.assertIs(self.value(), False)
        self.assertIs(self.events()[0]["after"], False)
        # Import a real module FROM the code directory; bare load_dotenv in
        # agent.py starts its search there, not in the bootstrap module's home.
        probe = code / "agent_probe.py"
        probe.write_text("from dotenv import load_dotenv\nload_dotenv(override=True)\n", encoding="utf8")
        spec = importlib.util.spec_from_file_location("agent_probe", probe)
        imported = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(imported)
        self.assertEqual(os.environ["PRAXIS_VISION_PREPASS"], "off")
        self.assertIs(vp._value(), self.value())

    def test_new_profile_is_initialized_from_deploy_config_before_model_import(self):
        self.assertFalse(self.target.exists())
        # Runner holds the tree lock, projects its model config, then migrates;
        # this tests the actual source-authored initialization seam, not a fake file.
        vp.preflight(self.tree)
        boot.project_brain(self.tree, {"model": {"model": "test-model"}})
        self.assertTrue(self.target.exists())
        self.assertTrue(vp.migrate(self.tree))
        self.assertIs(self.value(), True)
        self.assertFalse(vp.migrate(self.tree))
        self.assertEqual(len(self.events()), 1)

    def test_partial_existing_profile_without_voice_can_project_and_migrate(self):
        # llm._normalize accepts missing roles; refusing it at preflight would
        # strand older installs even though project_brain can fill voice safely.
        self.target.write_text(json.dumps({"roles": {}, "owner_extra": {"keep": True}}), encoding="utf8")
        vp.preflight(self.tree)
        boot.project_brain(self.tree, {"model": {"model": "test-model"}})
        self.assertTrue(vp.migrate(self.tree))
        config = json.loads(self.target.read_text("utf8"))
        self.assertIs(config["roles"]["voice"]["vision_prepass"], True)
        self.assertEqual(config["owner_extra"], {"keep": True})

    def test_unreadable_and_invalid_profiles_are_never_overwritten(self):
        for raw in (b'{broken', b'[]', b'{"roles":{"voice":{"vision_prepass":"false"}}}',
                    b'\xef\xbb\xbf{"roles":{"voice":{}}}'):
            with self.subTest(raw=raw):
                self.target.write_bytes(raw)
                with self.assertRaises(vp.MigrationError):
                    vp.preflight(self.tree)
                self.assertEqual(self.target.read_bytes(), raw)
        self.target.unlink()
        with self.assertRaises(vp.MigrationError):
            vp.migrate(self.tree)
        self.assertFalse(self.target.exists())

    def test_prepared_transaction_recovers_before_or_after_atomic_config_replace(self):
        self.profile()
        before = self.target.read_bytes()
        original_write = vp._write
        def fail_target(path, content, **kw):
            if path == self.target:
                raise OSError("simulated disk error before atomic replace")
            return original_write(path, content, **kw)
        with mock.patch.object(vp, "_write", side_effect=fail_target):
            with self.assertRaises(vp.MigrationError):
                vp.migrate(self.tree)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertTrue(vp.migrate(self.tree))
        self.assertTrue(self.value())
        self.assertEqual(len(self.events()), 1)
        # New pending transaction: simulate a crash after target replace, before
        # notice/event, then recover on the next boot without a second event.
        marker = vp._marker(self.tree)
        event = self.events()[0]
        marker.write_text(json.dumps(event), encoding="utf8")
        self.assertFalse(vp.migrate(self.tree))
        self.assertEqual(len(self.events()), 1)
        self.assertFalse(marker.exists())

    def test_conflicting_change_after_interrupted_transaction_refuses_overwrite(self):
        self.profile()
        before = self.target.read_bytes()
        original_write = vp._write
        with mock.patch.object(vp, "_write", side_effect=lambda path, data, **kw: (
                (_ for _ in ()).throw(OSError("disk error")) if path == self.target
                else original_write(path, data, **kw))):
            with self.assertRaises(vp.MigrationError):
                vp.migrate(self.tree)
        self.assertEqual(self.target.read_bytes(), before)
        edited = json.loads(before)
        edited["roles"]["voice"]["vision_prepass"] = False
        self.target.write_text(json.dumps(edited), encoding="utf8")
        changed = self.target.read_bytes()
        with self.assertRaises(vp.MigrationError):
            vp.migrate(self.tree)
        self.assertEqual(self.target.read_bytes(), changed)
        self.assertTrue(vp._marker(self.tree).exists())

    def test_private_temp_is_protected_before_secret_bytes_are_written(self):
        self.profile()
        original = self.target.read_bytes()
        observed = []

        def protect_empty(name):
            observed.append(Path(name).read_bytes())
            self.assertEqual(observed[-1], b"", "secret bytes entered temp before ACL")

        with mock.patch.object(vp, "_protect_private_temp", side_effect=protect_empty):
            self.assertTrue(vp.migrate(self.tree))
        self.assertEqual(observed, [b""])
        self.assertNotEqual(self.target.read_bytes(), original)

    def test_acl_refusal_leaves_original_private_profile_untouched(self):
        self.profile()
        original = self.target.read_bytes()
        with mock.patch.object(vp, "_protect_private_temp", side_effect=vp.MigrationError("ACL refused")):
            with self.assertRaises(vp.MigrationError):
                vp.migrate(self.tree)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertTrue(vp._marker(self.tree).is_file(), "same transaction stays recoverable")
        self.assertFalse(list(self.target.parent.glob("llm.json.*")), "empty temp must be removed")

    def test_brain_projection_protects_empty_temp_before_first_secret_write(self):
        empty = []
        def protect(name):
            empty.append(Path(name).read_bytes())
            self.assertEqual(empty[-1], b"", "project_brain wrote credentials before ACL")
        with mock.patch.object(vp, "_protect_private_temp", side_effect=protect):
            boot.project_brain(self.tree, {"model": {"model": "primary", "key": "SENTINEL-SECRET"}})
        self.assertEqual(empty, [b""])
        self.assertTrue(self.target.is_file())
        self.assertIn("SENTINEL-SECRET", self.target.read_text("utf8"))

    def test_brain_projection_acl_refusal_never_publishes_config(self):
        with mock.patch.object(vp, "_protect_private_temp", side_effect=vp.MigrationError("ACL refused")):
            with self.assertRaises(vp.MigrationError):
                boot.project_brain(self.tree, {"model": {"model": "primary", "key": "SENTINEL-SECRET"}})
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob("llm.json.*")))

    @unittest.skipUnless(os.name == "nt", "native Windows ACL observation")
    def test_windows_replaced_private_file_has_no_inherited_acl(self):
        target = self.tree / "acl-probe.json"
        target.write_bytes(b"previous-dummy")
        vp._write(target, b"new-dummy", private=True)
        self.assertEqual(target.read_bytes(), b"new-dummy")
        boot.project_brain(self.tree, {"model": {"model": "primary", "key": "dummy-key"}})
        # Test the *replacement* descriptor, not icacls' earlier return code.
        ps = ('$ErrorActionPreference="Stop"; $a=Get-Acl -LiteralPath $env:VISION_ACL_PROBE; '
              '$aces=@($a.Access); '
              '@{protected=$a.AreAccessRulesProtected; '
              'inherited=@($aces|Where-Object {$_.IsInherited}).Count; '
              'count=$aces.Count}|ConvertTo-Json -Compress')
        for path in (target, self.target):
            with self.subTest(path=path.name):
                result = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                        env={**os.environ, "VISION_ACL_PROBE": str(path),
                                             # Windows PowerShell 5 must load its own
                                             # built-ins even when tests start in pwsh 7.
                                             "PSModulePath": str(Path(os.environ["SystemRoot"]) /
                                                 "System32/WindowsPowerShell/v1.0/Modules")},
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                facts = json.loads(result.stdout)
                self.assertTrue(facts["protected"])
                self.assertEqual(facts["inherited"], 0)
                self.assertEqual(facts["count"], 3)  # user, SYSTEM, Administrators

    def test_crash_after_archive_does_not_duplicate_old_notice(self):
        self.profile()
        vp.migrate(self.tree)
        desk = Desk(self.tree)
        pending = json.loads(vp._notice(self.tree).read_text("utf8"))
        sid = "vision-policy-" + pending["id"]
        desk.archive(pending["text"], outgoing=False, now=dt.datetime.now(dt.timezone.utc),
                     sender="Hélène", source_id=sid)
        vp.notice(self.tree, desk, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(len((self.tree / "memory/groups/window.jsonl").read_text("utf8").splitlines()), 1)
        self.assertTrue(json.loads(vp._notice(self.tree).read_text("utf8"))["done"])

    def test_older_pending_notice_does_not_deadlock_replayed_migration(self):
        self.profile()
        vp.migrate(self.tree)
        old = vp._notice(self.tree).read_bytes()
        old_id = json.loads(old)["id"]
        # Reproduce a second transaction landing before its startup notice.
        self.profile()
        with mock.patch.object(vp, "_finish", side_effect=vp.MigrationError("power loss")):
            with self.assertRaises(vp.MigrationError):
                vp.migrate(self.tree)
        profile = self.target.read_bytes()
        new_id = json.loads(vp._marker(self.tree).read_text("utf8"))["id"]
        self.assertNotEqual(old_id, new_id)
        self.assertFalse(vp.migrate(self.tree))
        self.assertEqual(self.target.read_bytes(), profile)
        self.assertFalse(vp._marker(self.tree).exists())
        queued = vp._notice(self.tree).with_name("vision-policy-notice-" + old_id + ".json")
        self.assertEqual(queued.read_bytes(), old)
        desk = Desk(self.tree)
        now = dt.datetime.now(dt.timezone.utc)
        vp.notice(self.tree, desk, now)
        vp.notice(self.tree, desk, now)
        self.assertEqual(desk.activity, ["vision-policy-" + old_id, "life:vision-policy-" + old_id,
                                         "vision-policy-" + new_id, "life:vision-policy-" + new_id])
        self.assertTrue(json.loads(queued.read_text("utf8"))["done"])
        self.assertTrue(json.loads(vp._notice(self.tree).read_text("utf8"))["done"])

    def test_queue_collision_preserves_both_receipts_and_restart_deduplicates_archive(self):
        self.profile()
        vp.migrate(self.tree)
        pending = vp._notice(self.tree).read_bytes()
        old_id = json.loads(pending)["id"]
        queued = vp._notice(self.tree).with_name("vision-policy-notice-" + old_id + ".json")
        queued.write_bytes(pending)
        self.profile()
        self.assertTrue(vp.migrate(self.tree))
        self.assertEqual(queued.read_bytes(), pending, "an existing queued receipt must never be replaced")
        copies = list(queued.parent.glob("vision-policy-notice-" + old_id + "*.json"))
        self.assertEqual(len(copies), 2)
        desk = Desk(self.tree)
        now = dt.datetime.now(dt.timezone.utc)
        with mock.patch.object(desk, "life", side_effect=OSError("crash after archive")):
            with self.assertRaises(OSError):
                vp.notice(self.tree, desk, now)
        vp.notice(self.tree, desk, now)
        activity = list(desk.activity)
        vp.notice(self.tree, desk, now)
        self.assertEqual(desk.activity, activity)
        rows = [json.loads(line) for line in (self.tree / "memory/groups/window.jsonl").read_text("utf8").splitlines()]
        self.assertEqual(sum(row["source_id"] == "vision-policy-" + old_id for row in rows), 1)
        self.assertEqual(desk.activity.count("life:vision-policy-" + old_id), 1)
        self.assertTrue(all(json.loads(path.read_text("utf8"))["done"] for path in copies))

    def test_queue_alone_is_delivered_and_corrupt_receipt_is_not_silently_discarded(self):
        self.profile()
        vp.migrate(self.tree)
        pending = json.loads(vp._notice(self.tree).read_text("utf8"))
        queued = vp._notice(self.tree).with_name("vision-policy-notice-" + pending["id"] + ".json")
        vp._notice(self.tree).rename(queued)
        desk = Desk(self.tree)
        vp.notice(self.tree, desk, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(desk.activity, ["vision-policy-" + pending["id"], "life:vision-policy-" + pending["id"]])
        queued.write_text(json.dumps({**pending, "done": "false"}), encoding="utf8")
        before = queued.read_bytes()
        with self.assertRaises(vp.MigrationError):
            vp.notice(self.tree, desk, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(queued.read_bytes(), before)

    def test_invalid_old_notice_cannot_be_turned_into_a_path(self):
        self.profile()
        vp.migrate(self.tree)
        event = self.events()[0]
        bad = {"id": "../invalid", "text": "keep this", "done": False}
        vp._notice(self.tree).write_text(json.dumps(bad), encoding="utf8")
        original = vp._notice(self.tree).read_bytes()
        with self.assertRaises(vp.MigrationError):
            vp._finish(self.tree, {**event, "id": "b" * 32})
        self.assertEqual(vp._notice(self.tree).read_bytes(), original)

    def test_crash_after_queue_rename_recovers_without_losing_either_notice(self):
        self.profile()
        vp.migrate(self.tree)
        old = vp._notice(self.tree).read_bytes()
        self.profile()
        write = vp._write
        def crash_before_new_notice(path, data, **kwargs):
            if path == vp._notice(self.tree):
                raise OSError("power loss after queue rename")
            return write(path, data, **kwargs)
        with mock.patch.object(vp, "_write", side_effect=crash_before_new_notice):
            with self.assertRaises(vp.MigrationError):
                vp.migrate(self.tree)
        self.assertFalse(vp._notice(self.tree).exists())
        self.assertTrue(vp._marker(self.tree).exists())
        queued = list(vp._notice(self.tree).parent.glob("vision-policy-notice-*.json"))
        self.assertEqual([path.read_bytes() for path in queued], [old])
        self.assertFalse(vp.migrate(self.tree))
        desk = Desk(self.tree)
        vp.notice(self.tree, desk, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(len(desk.activity), 4)
        self.assertTrue(json.loads(queued[0].read_text("utf8"))["done"])

    def test_conflicting_text_for_one_receipt_id_refuses_before_delivery(self):
        self.profile()
        vp.migrate(self.tree)
        pending = json.loads(vp._notice(self.tree).read_text("utf8"))
        queued = vp._notice(self.tree).with_name("vision-policy-notice-" + pending["id"] + ".json")
        queued.write_bytes(vp._document({**pending, "text": "conflicting text"}))
        before = {path: path.read_bytes() for path in (queued, vp._notice(self.tree))}
        desk = Desk(self.tree)
        with self.assertRaises(vp.MigrationError):
            vp.notice(self.tree, desk, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(desk.activity, [])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_runner_wires_lock_projection_migration_notice_before_owner_turn(self):
        runner = Path(__file__).resolve().parents[1] / "localharness/runner.py"
        module = ast.parse(runner.read_text(encoding="utf8"))
        main = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        names = [ast.unparse(call.func) for node in ast.walk(main)
                 if isinstance(node, ast.Call) for call in [node]]
        def first(name):
            return next(node.lineno for node in ast.walk(main)
                        if isinstance(node, ast.Call) and ast.unparse(node.func) == name)
        self.assertTrue(all(name in names for name in ("boot.claim_tree", "vision_policy.preflight",
                        "boot.project_brain", "vision_policy.migrate", "_load_tree", "vision_policy.notice")))
        self.assertLess(first("boot.claim_tree"), first("vision_policy.preflight"))
        self.assertLess(first("vision_policy.preflight"), first("boot.project_brain"))
        self.assertLess(first("boot.project_brain"), first("vision_policy.migrate"))
        self.assertLess(first("vision_policy.migrate"), first("_load_tree"))
        self.assertLess(first("_load_tree"), first("vision_policy.notice"))


if __name__ == "__main__":
    unittest.main()
