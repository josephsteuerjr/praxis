"""Source-only join of the composed agent, desk executors and native body bytes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "installer"))
import build_mac
import vnext_candidate
import vnext_product as product


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   stderr=subprocess.DEVNULL).decode().strip()


class JoinedSource(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vnext-product-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.edition, self.desk, self.out = (
            base / name for name in ("source", "edition", "desk-package", "product.json"))
        self.root.mkdir()
        (self.root / "praxis").mkdir()
        (self.root / "praxis/agent.py").write_text("answer = 1\n", encoding="utf-8")
        body = self.root / "helene/core/body"
        (body / "crates/praxis-body/src").mkdir(parents=True)
        (body / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        (body / "Cargo.lock").write_text("version = 3\n", encoding="utf-8")
        (body / "crates/praxis-body/Cargo.toml").write_text("[package]\nname='body'\nversion='0.1.0'\n", encoding="utf-8")
        (body / "crates/praxis-body/src/main.rs").write_text("fn main() {}\n", encoding="utf-8")
        local = self.root / "desk/localharness"
        local.mkdir(parents=True)
        for name in ("body.py", "cu_read_cli.py", "cu_fs_cli.py", "desktop_supervisor.py", "desktop_process.py"):
            (local / name).write_text(f"# {name}\n", encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "-c", "core.autocrlf=false", "add", "-A")
        git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-q", "-m", "fixture")
        self.head = git(self.root, "rev-parse", "HEAD")
        vnext_candidate.stage(self.edition, root=self.root, expected_head=self.head)
        self.desk.mkdir()
        for path in local.iterdir():
            target = self.desk / "localharness" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        files = dict(sorted(product._files(self.desk, exclude="desk.json").items()))
        digest = hashlib.sha256("\n".join(f"{n} {v}" for n, v in files.items()).encode()).hexdigest()
        (self.desk / "desk.json").write_text(json.dumps({"product": "desk", "flavor": "windows",
                                      "digest": digest, "files": files, "skipped": [{"name": "static"}]}), encoding="utf-8")
        self.body_dir = base / "bin"
        self.body_dir.mkdir()
        for name in ("praxis-body", "praxis-bridge"):
            (self.body_dir / f"{name}.exe").write_bytes(name.encode())
        digest, count = build_mac.body_source_digest(self.edition / "body")
        self.body_receipt = base / "BODY-BUILT.json"
        self.body_receipt.write_text(json.dumps({"source": str(self.edition / "body"),
                            "source_digest": digest, "files": count,
                            "exe_sha256": {name: sha(self.body_dir / f"{name}.exe")
                                           for name in ("praxis-body", "praxis-bridge")}}), encoding="utf-8")

    def observe(self):
        return product.inspect(self.root, self.head, self.edition, self.desk,
                               self.body_receipt, self.body_dir)

    def test_join_and_previous_receipt_verify_same_bytes(self):
        row = self.observe()
        self.assertEqual(row["head"], self.head)
        self.assertEqual(row["relay"]["state"], "missing")
        self.assertEqual(row["body"]["toolchain"], "unverified")
        self.assertTrue(row["desk"]["skipped"])
        argv = ["--root", str(self.root), "--head", self.head, "--edition", str(self.edition),
                "--desk", str(self.desk), "--body-receipt", str(self.body_receipt),
                "--body-dir", str(self.body_dir)]
        with mock.patch.object(sys, "argv", ["vnext_product.py", *argv, "--out", str(self.out)]):
            self.assertEqual(product.main(), 0)
        with mock.patch.object(sys, "argv", ["vnext_product.py", *argv, "--verify", str(self.out)]):
            self.assertEqual(product.main(), 0)
        desk_note = self.desk / "desk.json"
        original = desk_note.read_bytes()
        changed = json.loads(original)
        changed["skipped"] = []
        desk_note.write_text(json.dumps(changed), encoding="utf-8")
        with mock.patch.object(sys, "argv", ["vnext_product.py", *argv, "--verify", str(self.out)]):
            self.assertEqual(product.main(), 2)  # same payload; old receipt is not relabelled
        desk_note.write_bytes(original)
        with mock.patch.object(sys, "argv", ["vnext_product.py", *argv, "--out", str(self.out)]):
            self.assertEqual(product.main(), 2)  # preserved, never overwritten
        nested = self.edition / "vnext-product.json"
        with mock.patch.object(sys, "argv", ["vnext_product.py", *argv, "--out", str(nested)]):
            self.assertEqual(product.main(), 2)
        self.assertFalse(nested.exists())
        (self.body_dir / "praxis-body.exe").write_bytes(b"moved")
        with mock.patch.object(sys, "argv", ["vnext_product.py", *argv, "--verify", str(self.out)]):
            self.assertEqual(product.main(), 2)

    def test_edition_and_desk_bytes_are_both_bound_to_head(self):
        self.assertEqual(self.observe()["edition"]["files"], 5)
        cli = self.desk / "localharness/cu_fs_cli.py"
        cli.write_text("# a different executor\n", encoding="utf-8")
        with self.assertRaisesRegex(product.CompositionError, "desk inventory"):
            self.observe()
        cli.write_bytes((self.root / "desk/localharness/cu_fs_cli.py").read_bytes())
        (self.edition / "agent.py").write_text("answer = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(product.CompositionError, "edition source or bytes"):
            self.observe()

    def test_modified_manifests_extra_files_missing_binary_and_wrong_head_refuse(self):
        with self.assertRaisesRegex(product.CompositionError, "clean candidate"):
            product.inspect(self.root, "0" * 40, self.edition, self.desk, self.body_receipt, self.body_dir)
        (self.desk / "unlisted.txt").write_text("foreign", encoding="utf-8")
        with self.assertRaisesRegex(product.CompositionError, "desk inventory"):
            self.observe()
        (self.desk / "unlisted.txt").unlink()
        (self.body_dir / "praxis-bridge.exe").unlink()
        with self.assertRaisesRegex(product.CompositionError, "body binary"):
            self.observe()

    def test_source_checkout_drift_refuses_even_if_git_hides_it(self):
        (self.root / "desk/localharness/body.py").write_text("# new\n", encoding="utf-8")
        with self.assertRaisesRegex(product.CompositionError, "desk executor"):
            self.observe()


if __name__ == "__main__":
    unittest.main(verbosity=2)
