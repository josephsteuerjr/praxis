import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_retention as rr


NOW = dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc)
OLD = "2026-07-01T00:00:00Z"
RECENT = "2026-09-11T00:00:00Z"


class RetentionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def create_run(self, run_id, *, status="done", terminal_at=OLD, files=None):
        directory = self.base / "memory" / "runs" / "2026-07" / run_id
        directory.mkdir(parents=True)
        manifest = {"schema": "praxis.run.v1", "status": status,
                    "terminal_at": terminal_at, "event_seq": 1, "context": {"run_id": run_id}}
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (directory / "events.jsonl").write_text(
            json.dumps({"seq": 1, "kind": "run_created", "status": status}) + "\n")
        for name, body in (files or {}).items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return directory

    def inventory(self, **kwargs):
        return rr.inventory(self.base, min_age=dt.timedelta(days=30), now=NOW, **kwargs)


class TestInventory(RetentionFixture):
    def test_classifies_only_old_closed_terminal_bytes(self):
        old = self.create_run("run-old", files={
            "context.md": b"ctx",
            "results/0001.json": b"result", "artifacts/file.bin": b"xx",
            "RECAP.md": b"recap", ".run.lock": b"x",
        })
        self.create_run("run-running", status="running", terminal_at="", files={"results/x": b"large"})
        self.create_run("run-doubt", status="in_doubt", terminal_at=OLD)
        self.create_run("run-recent", terminal_at=RECENT)
        before = {p.relative_to(self.base).as_posix(): p.read_bytes()
                  for p in self.base.rglob("*") if p.is_file()}

        report = self.inventory()

        rows = {row["run_id"]: row for row in report["runs"]}
        self.assertTrue(report["dry_run"])
        self.assertTrue(rows["run-old"]["eligible"])
        self.assertEqual(rows["run-running"]["excluded_reason"], "non_terminal")
        self.assertEqual(rows["run-doubt"]["excluded_reason"], "in_doubt")
        self.assertEqual(rows["run-recent"]["excluded_reason"], "recent")
        expected = {
            "manifest": (old / "manifest.json").stat().st_size, "context": 3,
            "events": (old / "events.jsonl").stat().st_size, "results": 6, "artifacts": 2, "recap": 5, "other": 1,
        }
        self.assertEqual(rows["run-old"]["bytes_by_class"], expected)
        self.assertEqual(report["summary"]["reclaimable_bytes_by_class"], {})
        self.assertEqual(report["summary"]["reclaimable_bytes"], 0)
        after = {p.relative_to(self.base).as_posix(): p.read_bytes()
                 for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(after, before, "inventory must neither write nor delete")

    def test_run_directory_symlink_and_manifest_identity(self):
        outside = self.create_run("run-outside")
        (outside.parent / "run-link").symlink_to(outside, target_is_directory=True)
        wrong = self.create_run("run-wrong")
        raw = json.loads((wrong / "manifest.json").read_text())
        for patch in ({"schema": "future"}, {"context": {"run_id": "run-other"}}):
            (wrong / "manifest.json").write_text(json.dumps({**raw, **patch}))
            rows = {r["run_id"]: r for r in self.inventory()["runs"]}
            self.assertEqual(rows["run-wrong"]["excluded_reason"], "invalid_manifest")
            self.assertEqual(rows["run-link"]["excluded_reason"], "unsafe_symlink")

    def test_only_addressed_result_is_reclaimable_and_wal_tail_excludes(self):
        body = b"result"
        directory = self.create_run("run-old", files={
            "results/0001-tool.log": body, "results/unaddressed": b"keep",
            "archive-locators/address.json": b"keep", "RECAP.md": b"keep"})
        ref = {"schema": "praxis.result-ref.v1", "run_id": "run-old",
               "result_id": "result-0001", "path": "results/0001-tool.log",
               "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        events = directory / "events.jsonl"
        events.write_text(json.dumps({"seq": 1, "kind": "run_created", "status": "done", "result": ref}) + "\n")
        manifest = directory / "manifest.json"
        raw = json.loads(manifest.read_text()); raw["event_seq"] = 1
        manifest.write_text(json.dumps(raw))
        self.assertEqual(self.inventory()["summary"]["reclaimable_bytes_by_class"], {"results": 6})
        with events.open("a") as stream:
            stream.write(json.dumps({"seq": 2, "kind": "resume_stale_reopened"}) + "\n")
        self.assertEqual(self.inventory()["runs"][0]["excluded_reason"], "unsettled_events")

    def test_terminal_manifest_requires_matching_status_in_wal(self):
        cases = ([], [{"seq": 1, "kind": "tool_result"}],
                 [{"seq": 1, "kind": "status_changed", "to_status": "failed"}])
        for index, events in enumerate(cases):
            directory = self.create_run(f"run-unproven-{index}")
            (directory / "events.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in events))
            path = directory / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["event_seq"] = len(events)
            path.write_text(json.dumps(manifest))
        for row in self.inventory()["runs"]:
            self.assertFalse(row["eligible"])
            self.assertEqual(row["excluded_reason"], "inconsistent_terminal_state")
            self.assertEqual(row["reclaimable_bytes_by_class"], {})

    def test_evidence_read_budget(self):
        path = self.base / "bounded"
        path.write_bytes(b"12345")
        with self.assertRaisesRegex(rr.ContractError, "byte budget"):
            rr.read_evidence_bytes(path, max_bytes=4)

    @unittest.skipUnless(os.name == "posix", "requires POSIX FIFOs/no-follow")
    def test_authoritative_reads_reject_fifo_and_open_replacement_without_hanging(self):
        # Each adversarial reader runs behind a hard process timeout. Redirecting
        # the final open emulates replacement after every pathname pre-check,
        # without mutating any original evidence file.
        script = textwrap.dedent(r"""
            import datetime as dt
            import json, os, pathlib, tempfile
            from unittest import mock
            import run_retention as rr
            from run_manager import RunManager, RunError
            with tempfile.TemporaryDirectory() as temporary:
                base = pathlib.Path(temporary)
                run = base / 'memory/runs/2000-01/run-old'
                run.mkdir(parents=True)
                (run / 'manifest.json').write_text(json.dumps({
                    'schema': 'praxis.run.v1', 'context': {'run_id': 'run-old'},
                    'status': 'done', 'terminal_at': '2000-01-01T00:00:00Z',
                    'event_seq': 1}))
                (run / 'events.jsonl').write_text(json.dumps({
                    'seq': 1, 'kind': 'run_created', 'status': 'done'}) + '\n')
                fifo = base / 'fifo'
                os.mkfifo(fifo)
                link = base / 'link'
                link.symlink_to(run / 'manifest.json')
                directory = base / 'directory'
                directory.mkdir()
                real_open = os.open
                for name in ('manifest.json', 'events.jsonl'):
                    for replacement in (fifo, link, directory):
                        hits = []
                        def replaced_open(path, flags, *args, **kwargs):
                            if str(path) == name:
                                hits.append(name)
                                return real_open(replacement, flags)
                            return real_open(path, flags, *args, **kwargs)
                        with mock.patch.object(os, 'open', side_effect=replaced_open):
                            report = rr.inventory(base, min_age=dt.timedelta(days=1),
                                                  discover_open_evidence=False)
                            assert hits and not report['runs'][0]['eligible'], report
                            hits.clear()
                            manager = RunManager(base)
                            try:
                                manager.read_result('run-old', 'result-0001')
                            except RunError as exc:
                                assert hits and ('safely read' in str(exc) or
                                                 'cannot read event stream' in str(exc)), str(exc)
                            else:
                                raise AssertionError('unsafe authoritative read accepted')
                # Direct FIFO reads must also reject before trying to read bytes.
                try:
                    rr.read_evidence_bytes(fifo)
                except rr.ContractError:
                    pass
                else:
                    raise AssertionError('FIFO accepted')
        """)
        completed = subprocess.run([sys.executable, "-c", script],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(os.name == "posix", "requires POSIX FIFOs/no-follow")
    def test_discovery_replacements_fail_closed_without_hanging(self):
        # Actual replacement immediately before open, after discovery/prechecks.
        # A subprocess timeout makes a missing NONBLOCK a bounded test failure.
        script = textwrap.dedent(r"""
            import datetime as dt
            import json, os, pathlib, tempfile
            from unittest import mock
            import run_retention as rr
            for target_name in ('TASK.md', 'events.jsonl', 'work', 'tasks', 'task-one', 'desires'):
                for replacement in ('fifo', 'symlink', 'hardlink', 'directory'):
                    if target_name in ('work', 'tasks', 'task-one', 'desires') and replacement != 'symlink':
                        continue
                    with tempfile.TemporaryDirectory() as temporary:
                        base = pathlib.Path(temporary)
                        task = base / 'memory/work/tasks/task-one/TASK.md'
                        task.parent.mkdir(parents=True)
                        task.write_text('---\nschema: "praxis.work.card.v1"\nstatus: "blocked"\nrun_ids: ["run-old"]\nevidence: []\n---\n')
                        ledger = base / 'memory/desires/events.jsonl'
                        ledger.parent.mkdir(parents=True)
                        ledger.write_text(json.dumps({'schema': 'praxis.desire.event.v1',
                            'desire_id': 'desire-one', 'state': {'status': 'active',
                            'run_ids': ['run-old'], 'evidence_refs': []}}) + '\n')
                        run = base / 'memory/runs/2000-01/run-old'
                        run.mkdir(parents=True)
                        (run / 'manifest.json').write_text(json.dumps({
                            'schema': 'praxis.run.v1', 'context': {'run_id': 'run-old'},
                            'status': 'done', 'terminal_at': '2000-01-01T00:00:00Z',
                            'event_seq': 1}))
                        (run / 'events.jsonl').write_text(json.dumps({
                            'seq': 1, 'kind': 'run_created', 'status': 'done'}) + '\n')
                        target = {'TASK.md': task, 'events.jsonl': ledger,
                                  'work': base / 'memory/work', 'tasks': task.parent.parent,
                                  'task-one': task.parent, 'desires': ledger.parent}[target_name]
                        real_open = os.open
                        hits = []
                        def replacing_open(path, flags, *args, **kwargs):
                            if str(path) == target_name and not hits:
                                hits.append(path)
                                saved = target.with_name(target.name + '.saved')
                                target.rename(saved)
                                if replacement == 'fifo':
                                    os.mkfifo(target)
                                elif replacement == 'symlink':
                                    target.symlink_to(saved, target_is_directory=saved.is_dir())
                                elif replacement == 'hardlink':
                                    os.link(saved, target)
                                else:
                                    target.mkdir()
                            return real_open(path, flags, *args, **kwargs)
                        with mock.patch.object(os, 'open', side_effect=replacing_open):
                            report = rr.inventory(base, min_age=dt.timedelta(days=1))
                        assert hits, (target_name, replacement)
                        assert report['open_evidence_scan_errors'], report
                        assert not report['runs'][0]['eligible'], report
                        assert report['runs'][0]['excluded_reason'] == 'open_evidence_state_unreadable', report
        """)
        completed = subprocess.run([sys.executable, "-c", script],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_discovery_uses_bounded_evidence_reads(self):
        task = self.base / "memory/work/tasks/task-one/TASK.md"
        task.parent.mkdir(parents=True)
        task.write_text("oversized")
        ledger = self.base / "memory/desires/events.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("oversized")
        original = rr.read_evidence_bytes
        def small_budget(path, **kwargs):
            return original(path, max_bytes=4)
        with mock.patch.object(rr, "read_evidence_bytes", side_effect=small_budget) as reader:
            runs, errors = rr.discover_open_evidence_runs(self.base)
        self.assertEqual(runs, set())
        self.assertEqual(len(errors), 2)
        self.assertEqual({call.args[0] for call in reader.call_args_list}, {task, ledger})

    def test_malformed_or_incomplete_wal_sequences_are_excluded(self):
        cases = (("duplicate", [1, 1], 1),
                 ("starts-two", [2], 2),
                 ("incomplete", [1], 2))
        for name, sequences, cursor in cases:
            directory = self.create_run(f"run-{name}")
            (directory / "events.jsonl").write_text("".join(
                json.dumps({"seq": seq, "kind": "test"}) + "\n"
                for seq in sequences), encoding="utf-8")
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["event_seq"] = cursor
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rows = {row["run_id"]: row for row in self.inventory()["runs"]}
        for name, _, _ in cases:
            self.assertEqual(rows[f"run-{name}"]["excluded_reason"],
                             "unsettled_events")

    def test_manifest_terminal_but_complete_wal_nonterminal_is_excluded(self):
        directory = self.create_run("run-reopened", files={"results/0001-tool.log": b"result"})
        ref = {"schema": "praxis.result-ref.v1", "run_id": "run-reopened",
               "result_id": "result-0001", "path": "results/0001-tool.log",
               "size": 6, "sha256": hashlib.sha256(b"result").hexdigest()}
        rows = [{"seq": 1, "kind": "tool_result", "result": ref},
                {"seq": 2, "kind": "status_changed", "to_status": "running"}]
        (directory / "events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text()); manifest["event_seq"] = 2
        manifest_path.write_text(json.dumps(manifest))
        row = self.inventory()["runs"][0]
        self.assertEqual(row["excluded_reason"], "inconsistent_terminal_state")
        self.assertEqual(row["reclaimable_bytes_by_class"], {})

    def test_missing_safe_openat_features_fail_with_contract_error(self):
        self.create_run("run-old")
        with mock.patch.object(rr, "_SAFE_OPENAT_SUPPORTED", False):
            with self.assertRaisesRegex(rr.ContractError, "POSIX openat"):
                self.inventory(discover_open_evidence=False)
            locator = rr.ArchiveLocator(
                "run-old", "result", "result-0001", "results/0001-tool.log",
                "objects/body", "0" * 64, 0, "0" * 64, 0, OLD)
            with self.assertRaisesRegex(rr.ContractError, "POSIX openat"):
                locator.verify(self.base)
            runs, errors = rr.discover_open_evidence_runs(self.base)
            self.assertEqual(runs, set())
            self.assertTrue(errors and errors[0].startswith("compatibility:"))

    def test_runtime_unsupported_openat_fails_closed(self):
        import os
        import errno
        self.create_run("run-old")
        tasks = self.base / "memory/work/tasks/task-one"
        tasks.mkdir(parents=True)
        (tasks / "TASK.md").write_text("unused")
        locator = rr.ArchiveLocator(
            "run-old", "result", "result-0001", "results/0001-tool.log",
            "objects/body", "0" * 64, 0, "0" * 64, 0, OLD)
        real_open = os.open
        for error in (NotImplementedError("dir_fd unavailable"),
                      TypeError("dir_fd unsupported"),
                      OSError(errno.ENOSYS, "openat unavailable"),
                      OSError(errno.ENOTSUP, "openat unsupported")):
            def unsupported(path, flags, *args, **kwargs):
                if "dir_fd" in kwargs:
                    raise error
                return real_open(path, flags, *args, **kwargs)

            with self.subTest(error=type(error).__name__), mock.patch.object(
                    rr.os, "open", side_effect=unsupported):
                with self.assertRaises(rr.ContractError):
                    locator.verify(self.base)
                with self.assertRaises(rr.ContractError):
                    self.inventory()
                with self.assertRaises(rr.ContractError):
                    rr.discover_open_evidence_runs(self.base)

    def test_conflicting_result_refs_exclude_entire_run(self):
        body = b"good"
        directory = self.create_run("run-conflict", files={"results/0001-tool.log": body})
        good = hashlib.sha256(body).hexdigest()
        bad = hashlib.sha256(b"evil").hexdigest()
        def ref(digest):
            return {"schema": "praxis.result-ref.v1", "run_id": "run-conflict",
                    "result_id": "result-0001", "path": "results/0001-tool.log",
                    "size": len(body), "sha256": digest}
        (directory / "events.jsonl").write_text(
            json.dumps({"seq": 1, "kind": "run_created", "status": "done", "result": ref(good)}) + "\n" +
            json.dumps({"seq": 2, "result": ref(bad)}) + "\n", encoding="utf-8")
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text()); manifest["event_seq"] = 2
        manifest_path.write_text(json.dumps(manifest))
        row = self.inventory()["runs"][0]
        self.assertFalse(row["eligible"])
        self.assertEqual(row["excluded_reason"], "conflicting_result_refs")
        self.assertEqual(row["reclaimable_bytes_by_class"], {})

    def test_unterminated_published_wal_is_unsettled(self):
        directory = self.create_run("run-tail")
        (directory / "events.jsonl").write_text(json.dumps({"seq": 1}))
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text()); manifest["event_seq"] = 1
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(self.inventory()["runs"][0]["excluded_reason"], "unsettled_events")

    def test_strict_json_and_result_ref_rows_fail_closed(self):
        event_cases = ('{"seq":1,"seq":1}\n', '{"seq":1,"value":NaN}\n')
        for index, event in enumerate(event_cases, 1):
            directory = self.create_run(f"run-event-json-{index}")
            (directory / "events.jsonl").write_text(event)
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["event_seq"] = 1
            (directory / "manifest.json").write_text(json.dumps(manifest))
        malformed = (None, {"schema": "future"},
                     {"schema": "praxis.result-ref.v1", "run_id": "run-other"})
        for index, ref in enumerate(malformed, 1):
            directory = self.create_run(f"run-result-bad-{index}")
            (directory / "events.jsonl").write_text(json.dumps({"seq": 1, "kind": "run_created", "status": "done", "result": ref}) + "\n")
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["event_seq"] = 1
            (directory / "manifest.json").write_text(json.dumps(manifest))
        duplicate = self.create_run("run-manifest-duplicate")
        (duplicate / "manifest.json").write_text(
            '{"schema":"bad","schema":"praxis.run.v1","status":"done",'
            '"terminal_at":"2026-07-01T00:00:00Z",'
            '"context":{"run_id":"run-manifest-duplicate"}}')
        rows = {row["run_id"]: row for row in self.inventory()["runs"]}
        for index in range(1, 3):
            self.assertEqual(rows[f"run-event-json-{index}"]["excluded_reason"], "unreadable_events")
        for index in range(1, 4):
            self.assertEqual(rows[f"run-result-bad-{index}"]["excluded_reason"], "invalid_result_ref")
        self.assertEqual(rows["run-manifest-duplicate"]["excluded_reason"], "invalid_manifest")

    def test_desire_ledger_strict_json_and_termination(self):
        self.create_run("run-old")
        ledger = self.base / "memory/desires/events.jsonl"
        ledger.parent.mkdir(parents=True)
        rows = (
            '{"schema":"praxis.desire.event.v1","desire_id":"desire-one",'
            '"state":{"status":"active","run_ids":[],"evidence_refs":[]}}',
            '{"schema":"bad","schema":"praxis.desire.event.v1","desire_id":"desire-one",'
            '"state":{"status":"active","run_ids":[],"evidence_refs":[]}}\n',
            '{"schema":"praxis.desire.event.v1","desire_id":"desire-one",'
            '"state":{"status":"active","run_ids":[],"evidence_refs":[],"x":NaN}}\n',
        )
        for raw in rows:
            ledger.write_text(raw)
            report = self.inventory()
            self.assertTrue(report["open_evidence_scan_errors"])
            self.assertEqual(report["runs"][0]["excluded_reason"],
                             "open_evidence_state_unreadable")

    def test_open_evidence_rejects_symlink_task_entries_and_cards(self):
        self.create_run("run-old")
        tasks = self.base / "memory/work/tasks"
        tasks.mkdir(parents=True)
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "TASK.md").write_text("ignored")
        (tasks / "linked-dir").symlink_to(outside, target_is_directory=True)
        runs, errors = rr.discover_open_evidence_runs(self.base)
        self.assertFalse(runs)
        self.assertTrue(errors)
        (tasks / "linked-dir").unlink()
        real = tasks / "real"
        real.mkdir()
        (real / "TASK.md").symlink_to(outside / "TASK.md")
        self.assertTrue(rr.discover_open_evidence_runs(self.base)[1])

    def test_open_evidence_finds_run_ids_in_ordinary_prose(self):
        self.create_run("run-prose")
        work = self.base / "memory/work/tasks/task-prose"
        work.mkdir(parents=True)
        (work / "TASK.md").write_text(
            '---\nschema: "praxis.work.card.v1"\nstatus: "blocked"\nrun_ids: []\nevidence: ["Receipt run-prose remains required."]\n---\n',
            encoding="utf-8")
        self.assertEqual(self.inventory()["runs"][0]["excluded_reason"], "open_evidence")
        self.assertEqual(rr._refs_run_ids("notrun-prose"), set())

    def test_result_requires_matching_checksum_and_reader_size_budget(self):
        mismatch = self.create_run("run-mismatch", files={
            "results/0001-tool.log": b"wrong!"})
        oversize = self.create_run("run-oversize")
        oversized_body = oversize / "results/0001-tool.log"
        oversized_body.parent.mkdir()
        # Sparse truncation exercises the size guard without allocating 64 MiB.
        with oversized_body.open("wb") as stream:
            stream.truncate(rr.MAX_ARCHIVE_OBJECT_SIZE + 1)
        fixtures = (
            (mismatch, len(b"wrong!"), hashlib.sha256(b"right?").hexdigest()),
            (oversize, rr.MAX_ARCHIVE_OBJECT_SIZE + 1, "0" * 64),
        )
        for directory, size, checksum in fixtures:
            ref = {"schema": "praxis.result-ref.v1", "run_id": directory.name,
                   "result_id": "result-0001", "path": "results/0001-tool.log",
                   "size": size, "sha256": checksum}
            (directory / "events.jsonl").write_text(
                json.dumps({"seq": 1, "kind": "run_created", "status": "done", "result": ref}) + "\n", encoding="utf-8")
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["event_seq"] = 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rows = {row["run_id"]: row for row in self.inventory()["runs"]}
        for run_id in ("run-mismatch", "run-oversize"):
            self.assertTrue(rows[run_id]["eligible"])
            self.assertEqual(rows[run_id]["reclaimable_bytes_by_class"], {})

    def test_result_inventory_hashes_and_sizes_one_no_follow_descriptor(self):
        from unittest.mock import patch

        body_bytes = b"result"
        directory = self.create_run("run-fd", files={"results/0001-tool.log": body_bytes})
        ref = {"schema": "praxis.result-ref.v1", "run_id": "run-fd",
               "result_id": "result-0001", "path": "results/0001-tool.log",
               "size": len(body_bytes), "sha256": hashlib.sha256(body_bytes).hexdigest()}
        (directory / "events.jsonl").write_text(json.dumps({"seq": 1, "kind": "run_created", "status": "done", "result": ref}) + "\n")
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["event_seq"] = 1
        (directory / "manifest.json").write_text(json.dumps(manifest))
        body = directory / ref["path"]
        replacement = directory / "replacement"
        replacement.write_bytes(b"attacker")
        real_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            descriptor = real_open(path, flags, *args, **kwargs)
            if str(path) == body.name and kwargs.get("dir_fd") is not None and not swapped:
                opened_parent = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}"))
                if opened_parent == body.parent:
                    swapped = True
                    os.replace(replacement, body)
            return descriptor

        with patch("run_retention.os.open", side_effect=racing_open):
            row = self.inventory()["runs"][0]
        self.assertTrue(swapped)
        self.assertEqual(row["reclaimable_bytes_by_class"], {})
        self.assertEqual(body.read_bytes(), b"attacker")

    def test_result_verification_is_confined_when_results_ancestor_is_replaced(self):
        expected = b"trusted"
        directory = self.create_run("run-ancestor-race", files={
            "results/0001-tool.log": b"outside",
        })
        ref = {"schema": "praxis.result-ref.v1", "run_id": "run-ancestor-race",
               "result_id": "result-0001", "path": "results/0001-tool.log",
               "size": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}
        (directory / "events.jsonl").write_text(json.dumps({
            "seq": 1, "kind": "run_created", "status": "done", "result": ref}) + "\n")
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["event_seq"] = 1
        manifest_path.write_text(json.dumps(manifest))
        outside = self.base / "matching-outside"
        outside.mkdir()
        (outside / "0001-tool.log").write_bytes(expected)
        results = directory / "results"
        saved = directory / "results-opened"
        real_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            descriptor = real_open(path, flags, *args, **kwargs)
            if str(path) == "results" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                results.rename(saved)
                results.symlink_to(outside, target_is_directory=True)
            return descriptor

        with mock.patch("run_retention.os.open", side_effect=racing_open):
            row = self.inventory()["runs"][0]
        self.assertTrue(swapped)
        self.assertEqual(row["reclaimable_bytes_by_class"], {})

    def test_explicit_and_discovered_open_evidence_exclude_runs(self):
        for run_id in ("run-explicit", "run-work", "run-desire", "run-free"):
            self.create_run(run_id)
        work = self.base / "memory/work/tasks/task-one"
        work.mkdir(parents=True)
        (work / "TASK.md").write_text(
            '---\nschema: "praxis.work.card.v1"\nstatus: "blocked"\nrun_ids: ["run-work"]\nevidence: []\n---\n', encoding="utf-8")
        desires = self.base / "memory/desires"
        desires.mkdir(parents=True)
        state = {"status": "active", "run_ids": [],
                 "evidence_refs": ["memory/runs/2026-07/run-desire/RECAP.md"]}
        (desires / "events.jsonl").write_text(
            json.dumps({"schema": "praxis.desire.event.v1", "desire_id": "desire-one", "state": state}) + "\n",
            encoding="utf-8")

        report = self.inventory(open_evidence_run_ids=["run-explicit"])
        rows = {row["run_id"]: row for row in report["runs"]}
        for run_id in ("run-explicit", "run-work", "run-desire"):
            self.assertEqual(rows[run_id]["excluded_reason"], "open_evidence")
        self.assertTrue(rows["run-free"]["eligible"])

    def test_closed_work_and_desire_do_not_hold_evidence_open(self):
        self.create_run("run-old")
        work = self.base / "memory/work/tasks/task-one"
        work.mkdir(parents=True)
        (work / "TASK.md").write_text(
            '---\nschema: "praxis.work.card.v1"\nstatus: "done"\nrun_ids: ["run-old"]\nevidence: []\n---\n', encoding="utf-8")
        desires = self.base / "memory/desires"
        desires.mkdir(parents=True)
        (desires / "events.jsonl").write_text(json.dumps({
            "schema": "praxis.desire.event.v1", "desire_id": "desire-one", "state": {"status": "satisfied",
            "run_ids": ["run-old"], "evidence_refs": []}}) + "\n", encoding="utf-8")
        self.assertTrue(self.inventory()["runs"][0]["eligible"])

    def test_open_task_prose_and_other_fields_hold_runs(self):
        for location in ("body", "goal", "wake", "blocker"):
            with self.subTest(location=location):
                work = self.base / "memory/work/tasks/task-one"
                work.mkdir(parents=True, exist_ok=True)
                text = '---\nschema: "praxis.work.card.v1"\nstatus: "blocked"\nrun_ids: []\nevidence: []\n'
                if location != "body":
                    text += location + ': "Need run-old before closure."\n'
                text += '---\n'
                if location == "body":
                    text += 'Need provenance from run-old before closure.\n'
                (work / "TASK.md").write_text(text)
                runs, errors = rr.discover_open_evidence_runs(self.base)
                self.assertFalse(errors)
                self.assertIn("run-old", runs)

    def test_malformed_work_cards_fail_closed_even_with_terminal_status(self):
        from work_store import SCHEMA, _dump

        self.create_run("run-old")
        work = self.base / "memory/work/tasks/task-one"
        work.mkdir(parents=True)
        valid = {"schema": SCHEMA, "status": "done",
                 "run_ids": ["run-old"], "evidence": []}
        hostile = [
            '---\nstatus: "done"\n',  # reviewer's exact truncated-card reproducer
            _dump(valid, "").rsplit("---", 1)[0],
            _dump(valid, "").replace("\n---\n", "\n--- trailing\n"),
            _dump(valid, "").replace('status: "done"', 'status: "running"\nstatus: "done"'),
            _dump(valid, "").replace('status: "done"', 'status: "done"\nignored line'),
            _dump(valid, "").replace('status: "done"', 'status: "done"\n: null'),
            _dump(valid, "").replace('status: "done"', 'status: "done"\nwake: {"x": 1, "x": 2}'),
            _dump(valid, "").replace('status: "done"', 'status: "done"\nwake: NaN'),
            b"---\nstatus: \"done\"\n---\n\xff",
        ]
        for key, values in {
            "schema": [None, "future", 1, [], {}],
            "status": [None, "future", "DONE", 1, [], {}],
            "run_ids": [None, "run-old", {}, [None], [1], [["run-old"]], [{}]],
            "evidence": [None, "run-old", {}, [None], [1], [["run-old"]], [{}]],
        }.items():
            hostile.append(_dump({k: v for k, v in valid.items() if k != key}, ""))
            hostile.extend(_dump({**valid, key: value}, "") for value in values)
        for text in hostile:
            with self.subTest(text=text):
                (work / "TASK.md").write_bytes(text if isinstance(text, bytes) else text.encode())
                report = self.inventory()
                self.assertTrue(report["open_evidence_scan_errors"])
                self.assertFalse(report["runs"][0]["eligible"])
                self.assertEqual(report["runs"][0]["excluded_reason"],
                                 "open_evidence_state_unreadable")

    def test_unreadable_work_text_fails_closed(self):
        from unittest.mock import patch

        self.create_run("run-old")
        card = self.base / "memory/work/tasks/task-one/TASK.md"
        card.parent.mkdir(parents=True)
        card.write_text("placeholder")

        real_fdopen = os.fdopen
        def unreadable_card(fd, mode, **kwargs):
            if mode == "r":
                raise PermissionError("unreadable card")
            return real_fdopen(fd, mode, **kwargs)
        with patch("run_retention.os.fdopen", side_effect=unreadable_card):
            report = self.inventory()
        self.assertTrue(report["open_evidence_scan_errors"])
        self.assertEqual(report["runs"][0]["excluded_reason"],
                         "open_evidence_state_unreadable")

    def test_work_writer_status_semantics_and_full_prose(self):
        from work_store import SCHEMA, STATUSES, TERMINAL, _dump

        self.create_run("run-old")
        work = self.base / "memory/work/tasks/task-one"
        work.mkdir(parents=True)
        for status in STATUSES:
            with self.subTest(status=status):
                row = {"schema": SCHEMA, "status": status, "run_ids": [], "evidence": [],
                       "wake": {"wake_on": "Need run-old"}}
                # Markdown separators in prose are normal, not extra frontmatter.
                (work / "TASK.md").write_text(_dump(row, "Need run-old.\n---\nNotes\n"))
                report = self.inventory()
                self.assertFalse(report["open_evidence_scan_errors"])
                self.assertEqual(report["runs"][0]["eligible"], status in TERMINAL)

    def test_noncanonical_desire_rows_never_close_active_evidence(self):
        self.create_run("run-old")
        ledger = self.base / "memory/desires/events.jsonl"
        ledger.parent.mkdir(parents=True)
        active = {"schema": "praxis.desire.event.v1", "desire_id": "desire-one",
                  "state": {"status": "active", "run_ids": ["run-old"], "evidence_refs": []}}
        closed = {**active, "state": {"status": "satisfied", "run_ids": [], "evidence_refs": []}}
        bad_rows = [
            {**closed, "schema": "attacker.future"},
            {k: v for k, v in closed.items() if k != "schema"},
            {**closed, "desire_id": "../desire-one"},
            {**closed, "desire_id": 1},
            {**closed, "state": None},
            {**closed, "state": {"status": "future"}},
            {**closed, "state": {"status": "satisfied", "run_ids": "run-old"}},
            {**closed, "state": {"status": "satisfied", "evidence_refs": [None]}},
        ]
        for bad in bad_rows:
            with self.subTest(row=bad):
                ledger.write_text(json.dumps(active) + "\n" + json.dumps(bad) + "\n")
                runs, errors = rr.discover_open_evidence_runs(self.base)
                self.assertIn("run-old", runs)
                self.assertTrue(errors)
                self.assertFalse(self.inventory()["runs"][0]["eligible"])
        ledger.write_text(json.dumps(active) + "\n" + json.dumps(closed) + "\n")
        self.assertEqual(rr.discover_open_evidence_runs(self.base), (set(), []))
        self.assertTrue(self.inventory()["runs"][0]["eligible"])

    def test_existing_task_directory_without_authoritative_card_fails_closed(self):
        self.create_run("run-old")
        task = self.base / "memory/work/tasks/incomplete-task"
        task.mkdir(parents=True)
        runs, errors = rr.discover_open_evidence_runs(self.base)
        self.assertEqual(runs, set())
        self.assertTrue(errors)
        report = self.inventory()
        self.assertEqual(report["runs"][0]["excluded_reason"],
                         "open_evidence_state_unreadable")

    def test_bad_open_evidence_state_fails_closed_for_all_candidates(self):
        self.create_run("run-old")
        desires = self.base / "memory/desires"
        desires.mkdir(parents=True)
        (desires / "events.jsonl").write_text("{broken\n", encoding="utf-8")
        report = self.inventory()
        self.assertEqual(report["runs"][0]["excluded_reason"],
                         "open_evidence_state_unreadable")
        self.assertTrue(report["open_evidence_scan_errors"])

    def test_unknown_terminal_time_corrupt_manifest_and_symlink_are_excluded(self):
        self.create_run("run-time", terminal_at="")
        broken = self.create_run("run-broken")
        (broken / "manifest.json").write_text("[]", encoding="utf-8")
        linked = self.create_run("run-link")
        (linked / "results").mkdir()
        (linked / "results/link").symlink_to(linked / "manifest.json")
        rows = {r["run_id"]: r for r in self.inventory()["runs"]}
        self.assertEqual(rows["run-time"]["excluded_reason"], "terminal_time_unknown")
        self.assertEqual(rows["run-broken"]["excluded_reason"], "invalid_manifest")
        self.assertEqual(rows["run-link"]["excluded_reason"], "unsafe_symlink")


class TestArchiveLocator(unittest.TestCase):
    def test_roundtrip_and_verifies_one_copied_old_run_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Compatibility proof fixture: byte copy of an old run body; source remains.
            source = root / "source" / "run-20000101T000000-old.tar"
            source.parent.mkdir()
            source.write_bytes(b"old terminal run archive bytes\n")
            archive = root / "cold"
            target = archive / "runs/sha256/object.tar"
            target.parent.mkdir(parents=True)
            target.write_bytes(source.read_bytes())
            locator = rr.ArchiveLocator(
                run_id="run-20000101T000000-old", evidence_kind="result",
                evidence_id="result-0001", source_path="results/0001-tool.log",
                object_key="runs/sha256/object.tar",
                body_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                body_size=target.stat().st_size,
                object_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                object_size=target.stat().st_size,
                archived_at="2026-09-12T00:00:00Z")
            parsed = rr.ArchiveLocator.from_dict(locator.to_dict())
            self.assertEqual(parsed.verify(archive), target.read_bytes())
            self.assertTrue(source.is_file(), "verification must not delete source evidence")

    def test_archive_root_ancestor_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / "real"
            (real / "cold").mkdir(parents=True)
            body = b"body"
            (real / "cold/object").write_bytes(body)
            link = base / "alias"
            link.symlink_to(real, target_is_directory=True)
            digest = hashlib.sha256(body).hexdigest()
            locator = rr.ArchiveLocator(
                "run-old", "result", "result-0001", "results/0001-tool.log",
                "object", digest, len(body), digest, len(body), OLD)
            self.assertEqual(locator.verify(real / "cold"), body)
            with self.assertRaises(rr.ContractError):
                locator.verify(link / "cold")

    def test_single_descriptor_bounded_materialization(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "object"
            body = b"original"
            target.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            locator = rr.ArchiveLocator("run-old", "result", "result-0001",
                "results/0001-tool.log", "object", sha, len(body), sha, len(body), OLD)
            read = os.read
            swapped = False
            def swap(fd, count):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    replacement = root / "replacement"
                    replacement.write_bytes(b"attacker")
                    replacement.replace(target)
                return read(fd, count)
            with patch.object(rr.os, "read", side_effect=swap):
                self.assertEqual(locator.verify(root, chunk_size=2), body)
            with self.assertRaises(rr.ContractError):
                locator.verify(root, max_size=1)
            target.unlink(); target.symlink_to(root / "missing")
            with self.assertRaises(rr.ContractError):
                locator.verify(root)
            target.unlink(); os.mkfifo(target)
            with self.assertRaisesRegex(rr.ContractError, "regular"):
                locator.verify(root)
            target.unlink(); target.write_bytes(body)
            def grow(fd, count):
                with target.open("ab") as stream:
                    stream.write(b"x")
                return read(fd, count)
            with patch.object(rr.os, "read", side_effect=grow), self.assertRaisesRegex(rr.ContractError, "exceeds"):
                locator.verify(root, chunk_size=2)

    def test_rejects_traversal_bad_digest_extra_fields_and_tampering(self):
        good = dict(schema=rr.ARCHIVE_LOCATOR_SCHEMA, run_id="run-old",
                    evidence_kind="result", evidence_id="result-0001",
                    source_path="results/0001-tool.log", object_key="objects/run-old.tar",
                    body_sha256="0" * 64, body_size=1, object_sha256="0" * 64, object_size=1,
                    archived_at="2026-09-12T00:00:00Z", format=rr.ARCHIVE_FORMAT)
        for patch in ({"evidence_id": "result-1"}, {"source_path": "results/0002-tool.log"}, {"object_key": "../escape"}, {"body_sha256": "ABC"},
                      {"archived_at": "2026-09-12"}):
            with self.subTest(patch=patch), self.assertRaises(rr.ContractError):
                rr.ArchiveLocator.from_dict({**good, **patch})
        with self.assertRaises(rr.ContractError):
            rr.ArchiveLocator.from_dict({**good, "provider": "surprise"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "objects/run-old.tar"
            path.parent.mkdir()
            path.write_bytes(b"x")
            locator = rr.ArchiveLocator.from_dict(good)
            with self.assertRaisesRegex(rr.ContractError, "mismatch"):
                locator.verify(root)


if __name__ == "__main__":
    unittest.main()
