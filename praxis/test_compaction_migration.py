"""PR10 behavior migration: no live files, all fixtures in temporary directories."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import compaction_migration as migration
import rooms

SHA = "a" * 40


class CompactionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.profile = self.base / "memory/rooms/-100123.md"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("# Fixture\n\nmode: quiet\nmode_set_by: praxis\n"
                                "context_hot: 200\n\n## Notes\nKeep my prose.\n")

    def apply(self):
        return migration.apply(self.base, sha=SHA, reason="Deliberate global helper removal, PR10")

    def rows(self):
        return [json.loads(line) for line in migration._ledger(self.base).read_text().splitlines()]

    def test_explicit_profiles_and_provenance_no_read_fallback(self):
        self.assertNotIn("compaction_policy", rooms.parse_profile(self.profile.read_text())["header"])
        result = self.apply()
        parsed = rooms.parse_profile(self.profile.read_text())
        self.assertEqual(parsed["header"]["compaction_policy"], "authored-only")
        self.assertEqual(parsed["header"]["compaction_set_by"], "deploy:" + SHA)
        self.assertEqual(parsed["mode"], "quiet")
        self.assertEqual(parsed["body"], "## Notes\nKeep my prose.")
        self.assertEqual(parsed["context_hot"], 200)
        global_policy = self.base / "memory/policies/compaction.md"
        self.assertIn("compaction_policy: authored-only", global_policy.read_text())
        self.assertEqual(result["actor"], "deploy:" + SHA)
        self.assertIn("Rollback:", result["explanation"])
        self.assertIn("32000", result["explanation"])
        self.assertFalse((self.base / "soul").exists())
        self.assertFalse((self.base / "memory/.state").exists())

    def test_idempotency_and_preserve_subsequent_authorship(self):
        self.apply()
        self.profile.write_text(self.profile.read_text() + "\nMy later note.\n")
        before = {p: p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        self.apply()
        self.assertEqual(before, {p: p.read_bytes() for p in self.base.rglob("*") if p.is_file()})
        self.assertEqual([r["kind"] for r in self.rows()], ["planned", "applied"])

    def test_interrupted_migration_resumes_original_plan(self):
        with patch.object(migration, "_atomic_write", side_effect=OSError("interruption")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual([r["kind"] for r in self.rows()], ["planned"])
        importlib.reload(migration)
        migration.apply(self.base, sha="b" * 40, reason="retry must not replace attribution")
        self.assertEqual(self.rows()[-1]["actor"], "deploy:" + SHA)
        self.assertEqual([r["kind"] for r in self.rows()], ["planned", "applied"])

    def test_partial_profile_write_resumes(self):
        real_write = migration._atomic_write
        count = 0

        def interrupted(path, data):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("crash after global profile")
            return real_write(path, data)

        with patch.object(migration, "_atomic_write", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.apply()
        self.apply()
        self.assertIn("compaction_policy: authored-only", self.profile.read_text())
        self.assertEqual([r["kind"] for r in self.rows()], ["planned", "applied"])

    def test_visible_once_survives_restart(self):
        self.assertEqual(migration.context_once(self.base), "")
        self.apply()
        text = migration.context_once(self.base)
        self.assertIn("helper compaction removed globally", text)
        self.assertIn("Rollback:", text)
        importlib.reload(migration)
        self.assertEqual(migration.context_once(self.base), "")
        self.apply()
        self.assertEqual(migration.context_once(self.base), "")
        self.assertEqual([r["kind"] for r in self.rows()],
                         ["planned", "applied", "context_shown"])

    def test_topic_profiles_use_canonical_routes_and_preserve_content(self):
        topic = self.profile.with_name("-1004301095307__topic__4654.md")
        raw = self.profile.read_text()
        topic.write_text(raw)
        invalid = ["-1001__topic__0.md", "-1001__topic__-2.md",
                   "-1001__topic__03.md", "-1001__topic__oops.md", "README.md"]
        for name in invalid:
            self.profile.with_name(name).write_text(raw)
        self.apply()
        parsed = rooms.parse_profile(topic.read_text())
        self.assertEqual(parsed["title"], "# Fixture")
        self.assertEqual(parsed["body"], "## Notes\nKeep my prose.")
        self.assertEqual(parsed["header"]["mode_set_by"], "praxis")
        self.assertEqual(parsed["header"]["compaction_policy"], "authored-only")
        self.assertIn(topic.relative_to(self.base).as_posix(), self.rows()[0]["profiles"])
        for name in invalid:
            self.assertEqual(self.profile.with_name(name).read_text(), raw)

    def test_other_migration_kinds_do_not_suppress_or_supply_receipts(self):
        for kind in ("planned", "applied", "context_shown"):
            migration._append(self.base, {"migration": "future-other", "kind": kind})
        self.assertEqual(migration.context_once(self.base), "")
        self.apply()
        self.assertIn("helper compaction removed globally", migration.context_once(self.base))
        self.assertEqual([r["kind"] for r in migration._rows(self.base)],
                         ["planned", "applied", "context_shown"])

    def test_short_writes_are_completed(self):
        real_write = os.write

        def short_write(fd, data):
            return real_write(fd, data[:max(1, len(data) // 3)])

        # Call the ledger helper directly: do not alter FileLock's own writes.
        with migration._LOCK, migration._lock(self.base):
            with patch.object(migration.os, "write", side_effect=short_write):
                migration._append(self.base, {"migration": "foreign", "kind": "applied"})
        self.assertEqual(self.rows(), [{"migration": "foreign", "kind": "applied"}])

    def test_torn_append_at_each_stage_resumes_and_preserves_history(self):
        for stage in ("planned", "applied", "context_shown"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                foreign = b'{"migration":"foreign","kind":"applied","note":"keep bytes"}\n'
                ledger = migration._ledger(base)
                ledger.parent.mkdir(parents=True)
                ledger.write_bytes(foreign)
                real_write = os.write
                torn = None

                def crash_write(fd, data):
                    nonlocal torn
                    data = bytes(data)
                    if b'"kind": "' + stage.encode() + b'"' in data:
                        torn = bytes(data[:len(data) // 2])
                        real_write(fd, torn)
                        raise OSError("crash after partial ledger append")
                    return real_write(fd, data)

                with patch.object(migration.os, "write", side_effect=crash_write):
                    with self.assertRaises(OSError):
                        migration.apply(base, sha=SHA, reason="original reason")
                        migration.context_once(base)
                before = ledger.read_bytes()
                committed = before[:-len(torn)]
                self.assertTrue(committed.startswith(foreign))
                importlib.reload(migration)
                result = migration.apply(base, sha="b" * 40, reason="retry")
                notice = migration.context_once(base)
                self.assertIn("helper compaction removed globally", notice)
                self.assertEqual(migration.context_once(base), "")
                self.assertTrue(ledger.read_bytes().startswith(committed))
                self.assertEqual([r["kind"] for r in migration._rows(base)],
                                 ["planned", "applied", "context_shown"])
                self.assertEqual(result["actor"], "deploy:" + ("b" * 40 if stage == "planned" else SHA))
                quarantines = list(ledger.parent.glob("compaction.jsonl.torn-*"))
                self.assertEqual(len(quarantines), 1)
                self.assertEqual(quarantines[0].read_bytes(), torn)

    def test_unterminated_valid_foreign_record_is_preserved(self):
        ledger = migration._ledger(self.base)
        ledger.parent.mkdir(parents=True)
        foreign = b'{"migration":"foreign","kind":"applied"}'
        ledger.write_bytes(foreign)
        self.apply()
        self.assertTrue(ledger.read_bytes().startswith(foreign + b"\n"))
        self.assertEqual(self.rows()[0], json.loads(foreign))

    def test_corrupt_complete_records_fail_closed_without_mutation(self):
        ledger = migration._ledger(self.base)
        ledger.parent.mkdir(parents=True)
        for bad in (b'{broken}\n', b'[]\n', b'\xff\n'):
            for suffix in (b'', b'{"migration":"foreign"}\n', b'{"torn":'):
                with self.subTest(bad=bad, suffix=suffix):
                    original = b'{"migration":"foreign"}\n' + bad + suffix
                    ledger.write_bytes(original)
                    for operation in (self.apply, lambda: migration.context_once(self.base)):
                        with self.assertRaises(ValueError):
                            operation()
                    self.assertEqual(ledger.read_bytes(), original)
                    self.assertEqual(list(ledger.parent.glob("*.torn-*")), [])
                    self.assertNotIn("compaction_policy", self.profile.read_text())

    def test_recovery_quarantine_failure_leaves_ledger_untouched(self):
        self.apply()
        ledger = migration._ledger(self.base)
        original = ledger.read_bytes() + b'{"incomplete":"\xe2\x86'
        ledger.write_bytes(original)
        with patch.object(migration, "_atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                migration.context_once(self.base)
        self.assertEqual(ledger.read_bytes(), original)
        self.assertIn("helper compaction removed globally", migration.context_once(self.base))
        quarantine = next(ledger.parent.glob("*.torn-*"))
        self.assertEqual(quarantine.read_bytes(), b'{"incomplete":"\xe2\x86')
        # A crash after quarantine persistence but before truncate is retryable.
        ledger.write_bytes(original)
        self.assertIn("helper compaction removed globally", migration.context_once(self.base))
        self.assertEqual(len(list(ledger.parent.glob("*.torn-*"))), 1)

    def test_write_interrupt_and_zero_progress(self):
        real_write = os.write
        with migration._LOCK, migration._lock(self.base):
            with patch.object(migration.os, "write", side_effect=[InterruptedError(), 0]):
                with self.assertRaises(OSError):
                    migration._append(self.base, {"migration": "foreign"})
            self.assertEqual(migration._ledger(self.base).read_bytes(), b"")
            calls = 0

            def interrupted(fd, data):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise InterruptedError()
                return real_write(fd, data)

            with patch.object(migration.os, "write", side_effect=interrupted):
                migration._append(self.base, {"migration": "foreign"})
        self.assertEqual(self.rows(), [{"migration": "foreign"}])

    def test_real_builder_once_restart_and_no_probe_consumption(self):
        import agent
        self.apply()
        with patch.object(agent, "BASE", self.base):
            probe = agent.build_state_evidence_block()
            self.assertNotIn("compaction_deployment_change", probe)
            self.assertEqual([r["kind"] for r in self.rows()], ["planned", "applied"])
            text = agent.build_state_evidence_block(include_deployment_notice=True)
            notices = [r for r in map(json.loads, text.splitlines())
                       if r["label"] == "compaction_deployment_change"]
            self.assertEqual(len(notices), 1)
            self.assertIn("deploy:" + SHA, notices[0]["content"])
            self.assertIn("Rollback:", notices[0]["content"])
            importlib.reload(migration)
            self.assertNotIn("compaction_deployment_change",
                             agent.build_state_evidence_block(include_deployment_notice=True))
        self.assertEqual(self.rows()[-1]["acknowledgement"], "context_constructed_not_delivered")

    def test_prompt_path_opts_into_real_builder(self):
        import agent
        self.apply()
        ctx = agent.ChannelContext(chat_id="-100123", room_id="-100123",
                                   is_dm=False, owner=True, known=True)
        with patch.object(agent, "BASE", self.base):
            _, _, evidence = agent._build_prompt_parts(speaker="fixture", query="hello", ctx=ctx,
                                                  include_deployment_notice=True)
        self.assertIn("compaction_deployment_change", evidence)
        self.assertIn("helper compaction removed globally", evidence)
        self.assertEqual(self.rows()[-1]["kind"], "context_shown")

    def test_real_builder_serialization_failure_does_not_consume_receipt(self):
        import agent
        self.apply()
        with patch.object(agent, "BASE", self.base):
            with patch.object(agent, "build_runner_counters", return_value=object()):
                with self.assertRaises(TypeError):
                    agent.build_state_evidence_block(include_deployment_notice=True)
            self.assertEqual([r["kind"] for r in self.rows()], ["planned", "applied"])
            self.assertIn("compaction_deployment_change",
                          agent.build_state_evidence_block(include_deployment_notice=True))

    def test_room_writer_preserves_explicit_policy(self):
        self.apply()
        with patch.object(rooms, "ROOMS_DIR", self.profile.parent):
            rooms.profile_update("-100123", context_hot=250)
        self.assertIn("compaction_policy: authored-only", self.profile.read_text())
        self.assertIn("compaction_set_by: deploy:" + SHA, self.profile.read_text())

    def test_invalid_deploy_metadata_does_not_write(self):
        for sha, reason in [("HEAD", "reason"), (SHA, ""), (SHA, "bad\nreason")]:
            with self.assertRaises(ValueError):
                migration.apply(self.base, sha=sha, reason=reason)
        self.assertFalse(migration._ledger(self.base).exists())


if __name__ == "__main__":
    unittest.main()
