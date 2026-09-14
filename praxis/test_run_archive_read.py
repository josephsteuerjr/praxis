from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from unittest import mock
import os
from pathlib import Path

import run_resume
from run_context import RunContext
from run_manager import ArchivedResultUnavailable, RunConflict, RunError, RunManager
from run_retention import ArchiveLocator


class TestArchivedRunResultCompatibility(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "old-source"
        source_manager = RunManager(self.source)
        context = RunContext(
            run_id="run-20000101T000000-old", kind="computer", goal="old fixture",
            principal_id="telegram:1", scope="owner", created_at="2000-01-01T00:00:00Z",
        )
        source_manager.create(context, "# copied old terminal fixture\n")
        source_manager.transition(context.run_id, "running", expected="pending")
        self.payload = b"first line\nsecond line\nthird line\n"
        self.ref = source_manager.store_result(context.run_id, self.payload.decode(), name="tool")
        source_manager.transition(context.run_id, "done", expected="running")

        # Compatibility is exercised on a byte copy. The old source tree is never
        # mutated; only the copied fixture's hot result is made unavailable.
        self.compat = self.root / "compat-copy"
        shutil.copytree(self.source, self.compat)
        self.archive = self.root / "cold"
        object_path = self.archive / "objects" / self.ref["sha256"]
        object_path.parent.mkdir(parents=True)
        copied_run = next((self.compat / "memory/runs").glob("*/run-20000101T000000-old"))
        copied_body = copied_run / self.ref["path"]
        object_path.write_bytes(copied_body.read_bytes())
        locator = ArchiveLocator(
            run_id=context.run_id, evidence_kind="result",
            evidence_id=self.ref["result_id"], source_path=self.ref["path"],
            object_key=f"objects/{self.ref['sha256']}",
            body_sha256=self.ref["sha256"], body_size=self.ref["size"],
            object_sha256=self.ref["sha256"], object_size=self.ref["size"],
            archived_at="2026-09-12T00:00:00Z",
        )
        sidecar = copied_run / "archive-locators" / f"{self.ref['result_id']}.json"
        sidecar.parent.mkdir()
        sidecar.write_text(json.dumps(locator.to_dict()), encoding="utf-8")
        copied_body.unlink()
        self.manager = RunManager(self.compat, archive_root=self.archive)

    def test_cursors_and_provenance_read_exact_archived_body(self):
        original_snapshot = {
            path.relative_to(self.source): path.read_bytes()
            for path in self.source.rglob("*") if path.is_file()
        }

        page = self.manager.read_result(
            self.ref["run_id"], self.ref["result_id"], byte_offset=6, byte_limit=12,
        )
        self.assertEqual(base64.b64decode(page["data_base64"]), self.payload[6:18])
        self.assertEqual(page["path"], self.ref["path"])
        self.assertEqual(page["sha256"], self.ref["sha256"])
        lines = self.manager.read_result(
            self.ref["run_id"], self.ref["path"], line_start=2, line_count=1,
        )
        self.assertEqual(lines["text"], "second line\n")
        self.assertEqual(
            run_resume.read_full_result_bytes(self.manager, self.ref["run_id"], self.ref),
            self.payload,
        )
        after = {
            path.relative_to(self.source): path.read_bytes()
            for path in self.source.rglob("*") if path.is_file()
        }
        self.assertEqual(after, original_snapshot, "authoritative old source must remain untouched")

    def test_valid_locator_is_returned_honestly_when_archive_is_not_configured(self):
        manager = RunManager(self.compat)
        with self.assertRaises(ArchivedResultUnavailable) as caught:
            manager.read_result(self.ref["run_id"], self.ref["result_id"])
        self.assertEqual(caught.exception.locator["object_key"],
                         f"objects/{self.ref['sha256']}")
        self.assertIn("archive root is not configured", str(caught.exception))

    def test_reader_uses_only_exact_published_wal_prefix(self):
        run_dir = next((self.compat / "memory/runs").glob("*/run-20000101T000000-old"))
        events = run_dir / "events.jsonl"
        original = events.read_bytes()
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        published = manifest["event_seq"]

        # Append-before-manifest publication is an ordinary writer state. The
        # result reader must neither trust nor reject that unpublished suffix.
        for suffix in (
            json.dumps({"seq": published + 1, "result": "hostile"}).encode() + b"\n",
            b"{concurrent partial",
        ):
            with self.subTest(suffix=suffix):
                events.write_bytes(original + suffix)
                self.assertEqual(
                    self.manager.read_result(self.ref["run_id"], self.ref["result_id"])["text"],
                    self.payload.decode(),
                )

        gapped_rows = [json.loads(line) for line in original.decode().splitlines()]
        gapped_rows[0]["seq"] = 99
        cases = {
            "malformed published": b"{broken\n" + original.split(b"\n", 1)[1],
            "unterminated published": original.rstrip(b"\n"),
            "gapped published": b"".join(json.dumps(row).encode() + b"\n" for row in gapped_rows),
            "truncated published": b"".join(original.splitlines(keepends=True)[:-1]),
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                events.write_bytes(body)
                with self.assertRaisesRegex(RunError, "event|unterminated|invalid|truncated|contiguous"):
                    self.manager.read_result(self.ref["run_id"], self.ref["result_id"])
        events.write_bytes(original)

    def test_result_lookup_scans_wal_once_and_honors_deadline(self):
        original = self.manager._iter_events
        calls = []

        def counted(*args, **kwargs):
            calls.append(kwargs)
            yield from original(*args, **kwargs)

        with mock.patch.object(self.manager, "_iter_events", side_effect=counted):
            page = self.manager.read_result(
                self.ref["run_id"], self.ref["result_id"],
                deadline_monotonic=float("inf"),
            )
        self.assertEqual(page["text"], self.payload.decode())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["deadline_monotonic"], float("inf"))

        # The hot id-addressed path also validates the WAL exactly once (the old
        # recursive id-to-path implementation scanned it twice).
        (self.manager._find(self.ref["run_id"]) / self.ref["path"]).write_bytes(self.payload)
        calls.clear()
        with mock.patch.object(self.manager, "_iter_events", side_effect=counted):
            page = self.manager.read_result(
                self.ref["run_id"], self.ref["result_id"],
                deadline_monotonic=float("inf"),
            )
        self.assertEqual(page["text"], self.payload.decode())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["deadline_monotonic"], float("inf"))
        with self.assertRaisesRegex(RunError, "budget exceeded"):
            self.manager.read_result(
                self.ref["run_id"], self.ref["result_id"], deadline_monotonic=0,
            )

    def test_unrelated_hot_prefix_files_never_suppress_bound_body(self):
        run_dir = self.manager._find(self.ref["run_id"])
        for name in ("0001-junk.log", "0001-other.bin"):
            (run_dir / "results" / name).write_bytes(b"EVIL")
            for verify in (True, False):
                page = self.manager.read_result(self.ref["run_id"], self.ref["result_id"],
                                                verify_sha256=verify)
                self.assertEqual(page["text"].encode(), self.payload)
                self.assertEqual(page["path"], self.ref["path"])
        self.assertEqual(self.manager.read_result(self.ref["run_id"],
                         "results/0001-junk.log")["text"], "EVIL")
        (run_dir / self.ref["path"]).write_bytes(self.payload)
        self.assertEqual(self.manager.read_result(self.ref["run_id"],
                         self.ref["result_id"])["text"].encode(), self.payload)

    def test_global_wal_conflicts_cannot_be_selected_away(self):
        run_dir = self.manager._find(self.ref["run_id"])
        events = run_dir / "events.jsonl"
        manifest_path = run_dir / "manifest.json"
        original_events = events.read_text()
        manifest = json.loads(manifest_path.read_text())
        conflicts = [
            {**self.ref, "path": "results/0001-other.log"},
            {**self.ref, "result_id": "result-0002"},
            {**self.ref, "sha256": "0" * 64},
            {**self.ref, "size": self.ref["size"] + 1},
        ]
        for hot in (False, True):
            if hot:
                (run_dir / self.ref["path"]).write_bytes(self.payload)
            for conflict in conflicts:
                with self.subTest(conflict=conflict, hot=hot):
                    events.write_text(original_events + json.dumps({
                        "seq": manifest["event_seq"] + 1, "result": conflict}) + "\n")
                    manifest_path.write_text(json.dumps({**manifest, "event_seq": manifest["event_seq"] + 1}))
                    for address in (self.ref["result_id"], self.ref["path"]):
                        with self.assertRaises(RunError):
                            self.manager.read_result(self.ref["run_id"], address)

    def test_authoritative_json_rejects_duplicates_and_non_json_constants(self):
        run_dir = self.manager._find(self.ref["run_id"])
        manifest_path = run_dir / "manifest.json"
        events_path = run_dir / "events.jsonl"
        sidecar = run_dir / "archive-locators" / f"{self.ref['result_id']}.json"
        originals = {
            manifest_path: manifest_path.read_text(encoding="utf-8"),
            events_path: events_path.read_text(encoding="utf-8"),
            sidecar: sidecar.read_text(encoding="utf-8"),
        }
        mutations = (
            ("manifest duplicate", manifest_path, originals[manifest_path].replace(
                '"event_seq":', '"event_seq": 999, "event_seq":', 1)),
            ("manifest constant", manifest_path, originals[manifest_path].replace(
                '"event_seq":', '"hostile": NaN, "event_seq":', 1)),
            ("WAL duplicate", events_path, originals[events_path].replace(
                '"seq":', '"seq": 999, "seq":', 1)),
            ("WAL constant", events_path, originals[events_path].replace(
                '"seq":', '"hostile": Infinity, "seq":', 1)),
            ("locator duplicate", sidecar, originals[sidecar].replace(
                '"schema":', '"schema": null, "schema":', 1)),
            ("locator constant", sidecar, originals[sidecar].replace(
                '"schema":', '"hostile": -Infinity, "schema":', 1)),
        )
        try:
            for hot in (False, True):
                if hot:
                    (run_dir / self.ref["path"]).write_bytes(self.payload)
                for name, path, hostile in mutations:
                    # Hot bodies do not consume archive locators.
                    if hot and path == sidecar:
                        continue
                    for address in (self.ref["result_id"], self.ref["path"]):
                        with self.subTest(name=name, hot=hot, address=address):
                            self.assertNotEqual(hostile, originals[path])
                            path.write_text(hostile, encoding="utf-8")
                            try:
                                with self.assertRaisesRegex(RunError, "strict JSON|invalid event JSON"):
                                    self.manager.read_result(self.ref["run_id"], address)
                            finally:
                                path.write_text(originals[path], encoding="utf-8")
        finally:
            for path, original in originals.items():
                path.write_text(original, encoding="utf-8")

    def test_every_malformed_result_bearing_event_fails_closed(self):
        run_dir = self.manager._find(self.ref["run_id"])
        events = run_dir / "events.jsonl"
        manifest_path = run_dir / "manifest.json"
        original_events = events.read_text(encoding="utf-8")
        original_manifest = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(original_manifest)
        malformed = (
            None,
            "not-an-object",
            {},
            [],
            {**self.ref, "schema": "praxis.result-ref.v999", "sha256": "0" * 64},
            {key: value for key, value in self.ref.items() if key != "schema"},
            {**self.ref, "run_id": "run-other"},
            {**self.ref, "result_id": "bad"},
            {**self.ref, "path": "../escape.log"},
            {**self.ref, "sha256": "not-a-checksum"},
            {**self.ref, "size": -1},
        )
        try:
            for hot in (False, True):
                if hot:
                    (run_dir / self.ref["path"]).write_bytes(self.payload)
                for ref in malformed:
                    with self.subTest(ref=ref, hot=hot):
                        events.write_text(original_events + json.dumps({
                            "seq": manifest["event_seq"] + 1, "result": ref,
                        }) + "\n", encoding="utf-8")
                        manifest_path.write_text(json.dumps({
                            **manifest, "event_seq": manifest["event_seq"] + 1,
                        }), encoding="utf-8")
                        for address in (self.ref["result_id"], self.ref["path"]):
                            with self.subTest(address=address):
                                with self.assertRaisesRegex(RunError, "invalid ResultRef"):
                                    self.manager.read_result(self.ref["run_id"], address)
                # An event without a result field is not malformed evidence.
                events.write_text(original_events + json.dumps({
                    "seq": manifest["event_seq"] + 1, "kind": "annotation",
                }) + "\n", encoding="utf-8")
                for address in (self.ref["result_id"], self.ref["path"]):
                    self.assertEqual(self.manager.read_result(
                        self.ref["run_id"], address)["text"].encode(), self.payload)
        finally:
            events.write_text(original_events, encoding="utf-8")
            manifest_path.write_text(original_manifest, encoding="utf-8")

    def test_symlinked_locator_sidecar_is_rejected(self):
        run_dir = next((self.compat / "memory/runs").glob("*/run-20000101T000000-old"))
        sidecar = run_dir / "archive-locators" / f"{self.ref['result_id']}.json"
        outside = self.root / "outside-locator.json"
        outside.write_bytes(sidecar.read_bytes())
        sidecar.unlink()
        try:
            sidecar.symlink_to(outside)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(RunError, "safely read strict JSON|single regular file"):
            self.manager.read_result(self.ref["run_id"], self.ref["result_id"])

    def test_fifo_locator_fails_immediately_instead_of_blocking(self):
        run_dir = self.manager._find(self.ref["run_id"])
        sidecar = run_dir / "archive-locators" / f"{self.ref['result_id']}.json"
        sidecar.unlink()
        try:
            os.mkfifo(sidecar)
        except (AttributeError, NotImplementedError, OSError) as exc:
            self.skipTest(f"FIFO unavailable: {exc}")
        with self.assertRaisesRegex(RunError, "single regular file"):
            self.manager.read_result(self.ref["run_id"], self.ref["result_id"])

    def test_symlinked_run_component_cannot_escape_runs_root(self):
        real_run = self.manager._find(self.ref["run_id"])
        month = real_run.parent
        outside = self.root / "outside-run"
        shutil.copytree(real_run, outside)
        shutil.rmtree(real_run)
        try:
            real_run.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.manager._paths[self.ref["run_id"]] = real_run
        with self.assertRaisesRegex(RunError, "safely read strict JSON"):
            self.manager.read_result(self.ref["run_id"], self.ref["result_id"])

    def test_symlinked_ancestors_of_trusted_runs_root_are_rejected(self):
        for relative in ("memory", "."):
            with self.subTest(component=relative):
                original = self.compat if relative == "." else self.compat / relative
                displaced = self.root / "displaced"
                original.rename(displaced)
                original.symlink_to(displaced, target_is_directory=True)
                try:
                    with self.assertRaises(RunError):
                        self.manager.read_result(self.ref["run_id"], self.ref["result_id"])
                finally:
                    original.unlink()
                    displaced.rename(original)

    def test_runtime_unsupported_openat_is_a_run_error(self):
        real_open = os.open
        for error in (NotImplementedError("dir_fd unavailable"),
                      TypeError("dir_fd unsupported"), OSError("not supported")):
            def unsupported(path, flags, *args, **kwargs):
                if "dir_fd" in kwargs:
                    raise error
                return real_open(path, flags, *args, **kwargs)

            with self.subTest(error=type(error).__name__), mock.patch(
                    "run_manager.os.open", side_effect=unsupported):
                with self.assertRaises(RunError):
                    self.manager.read_result(self.ref["run_id"], self.ref["result_id"])

    def test_unsupported_platform_archive_read_fails_explicitly(self):
        with mock.patch("run_manager._SAFE_ARCHIVE_OPENAT_SUPPORTED", False):
            with self.assertRaisesRegex(RunError, "unsupported on this platform"):
                self.manager.read_result(self.ref["run_id"], self.ref["result_id"])

    def test_locator_must_bind_the_immutable_result_ref(self):
        run_dir = next((self.compat / "memory/runs").glob("*/run-20000101T000000-old"))
        sidecar = run_dir / "archive-locators" / f"{self.ref['result_id']}.json"
        value = json.loads(sidecar.read_text(encoding="utf-8"))
        value["body_sha256"] = value["object_sha256"] = hashlib.sha256(b"wrong").hexdigest()
        value["body_size"] = value["object_size"] = len(b"wrong")
        sidecar.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(RunError, "durable ResultRef"):
            self.manager.read_result(self.ref["run_id"], self.ref["result_id"])


if __name__ == "__main__":
    unittest.main()
