"""Exact vNext source -> real distribution packer seams; never run an installer."""
from __future__ import annotations

import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
import zipfile

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / "installer"), str(HERE / "tests"), str(HERE)]
import build_dist
import relay_src
import vnext_candidate
import vnext_dist


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PinnedSource(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vnext-dist-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.repo, self.stage = base / "source", base / "selection"
        self.repo.mkdir()
        for name, text in {
            "praxis/agent.py": "answer = 1\n",
            "praxis/private.py": "secret = 'fixture-only'\n",
            "praxis/test_contract.py": "def test_example(): pass\n",
            "helene/core/agent.py": "answer = 2\n",
            "praxis/relay/Cargo.toml": "[package]\nname='relay'\nversion='0.1.0'\n",
            "praxis/relay/Cargo.lock": "version = 3\n",
            "praxis/relay/src/main.rs": "fn main() {}\n",
            "praxis/relay/Dockerfile": "FROM rust:1\n",
            "praxis/relay/build.rs": "fn main() {}\n",
        }.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "-c", "core.autocrlf=false", "add", "-A")
        git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=test@example.invalid",
            "commit", "-q", "-m", "source")
        self.head = git(self.repo, "rev-parse", "HEAD")
        vnext_candidate.stage(self.stage / "edition", root=self.repo, expected_head=self.head)
        vnext_dist.stage_relay(self.repo, self.head, self.stage / "relay")

    def test_edition_and_copied_payload_are_distinct_attested_bytes(self):
        edition = vnext_dist.verify_edition(self.repo, self.head, self.stage / "edition")
        cache = self.stage / "edition/__pycache__/agent.cpython-314.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"ephemeral")
        self.assertEqual(vnext_dist.verify_edition(self.repo, self.head, self.stage / "edition")["source_digest"],
                         edition["source_digest"])
        self.assertEqual((self.stage / "edition/agent.py").read_text(), "answer = 2\n")
        self.assertEqual(edition["source_files"], 3)
        dest = self.stage / "copy"
        build_dist.copy_tree(self.stage / "edition", dest)
        before = vnext_dist.verify_copied(edition, dest, build_dist._excluded)
        self.assertEqual(before["shipped_source_files"], 2)
        self.assertNotIn(".vnext-source.json", vnext_dist._inventory(dest))
        (dest / "LICENSE").write_bytes(b"license\n")
        final = vnext_dist.final_payload(dest)
        self.assertNotEqual(final["sha256"], before["staged_source_digest"])
        (dest / "agent.py").write_bytes(b"foreign\n")
        with self.assertRaisesRegex(vnext_dist.SourceMismatch, "moved"):
            vnext_dist.verify_copied(edition, dest, build_dist._excluded)
        (self.repo / "praxis/agent.py").write_text("answer = 3\n", encoding="utf-8")
        with self.assertRaises(vnext_dist.SourceMismatch):
            vnext_dist.verify_edition(self.repo, self.head, self.stage / "edition")

    def test_relay_tracked_source_and_dockerfile_must_match(self):
        relay = self.stage / "relay"
        original = vnext_dist.verify_relay(self.repo, self.head, relay)
        self.assertEqual(original["kind"], "new_tracked_adaptation")
        self.assertEqual(original["input_files"], 5)
        self.assertEqual(relay_src.stamp(relay)["head"], self.head)
        (relay / "Dockerfile").write_text("FROM rust:2\n", encoding="utf-8")
        with self.assertRaisesRegex(vnext_dist.SourceMismatch, "staged relay source moved"):
            vnext_dist.verify_relay(self.repo, self.head, relay)

    def test_full_relay_receipts_required_for_both_targets(self):
        root = self.stage / "relay"
        win = root / "target/release/codex-proxy-server.exe"
        linux = relay_src.linux_binary(root)
        for path, value in ((win, b"win"), (linux, b"linux")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        with self.assertRaisesRegex(SystemExit, "неизвестно"):
            build_dist.relay_provenance(win, False, root)
        with self.assertRaisesRegex(SystemExit, "неизвестно"):
            build_dist.relay_linux_provenance(linux, False, root)
        self.assertTrue(build_dist.relay_provenance(win, True, root)["stale"])
        legacy, files = relay_src.digest(root)
        exact, n = relay_src.build_inputs(root)
        for path, binary, method, extra in ((root / relay_src.BUILT, win,
                                              relay_src.WINDOWS_BUILD_METHOD,
                                              {"target": "x86_64-pc-windows-msvc"}),
                                             (root / relay_src.LINUX_BUILT, linux,
                                              relay_src.LINUX_BUILD_METHOD,
                                              {"image_id": "sha256:" + "a" * 64})):
            path.write_text(json.dumps({"digest": legacy, "exe_sha256": sha(binary),
                                        "input_digest_kind": relay_src.INPUT_DIGEST_KIND,
                                        "input_sha256": exact, "input_files": n,
                                        "build_method": method, **extra}), encoding="utf-8")
        self.assertEqual(build_dist.relay_provenance(win, False, root)["built"]["input_sha256"], exact)
        self.assertEqual(build_dist.relay_linux_provenance(linux, False, root)["input_sha256"], exact)
        (root / "Dockerfile").write_text("FROM rust:2\n", encoding="utf-8")
        # The historical LF digest stays the same; the complete input identity doesn't.
        self.assertEqual(relay_src.digest(root)[0], legacy)
        with self.assertRaisesRegex(SystemExit, "exact build inputs"):
            build_dist.relay_provenance(win, False, root)
        with self.assertRaisesRegex(SystemExit, "не соответствует"):
            build_dist.relay_linux_provenance(linux, False, root)

    def test_full_candidate_status_never_hides_skipped_tests_or_partial(self):
        args = SimpleNamespace(allow_partial=False, skip_runtime=False, skip_tests=False,
                               skip_smokes=False, skip_setup_exe=False)
        staged = {"phone": True, "desk": {"skipped": []}}
        clean = build_dist.qualification_reasons(args, [], staged, False, False,
                                                  {}, {"built": {"input_sha256": "ok"}},
                                                  {"input_sha256": "ok"}, None)
        self.assertEqual(clean, [])
        args.skip_tests = True
        self.assertIn("tests_skipped", build_dist.qualification_reasons(
            args, [], staged, False, False, {}, {}, {}, None))
        args.skip_tests = False
        args.allow_partial = True
        self.assertIn("allow_partial", build_dist.qualification_reasons(
            args, [], staged, False, False, {}, {}, {}, None))
        args.allow_partial = False
        self.assertIn("relay_unverified", build_dist.qualification_reasons(
            args, [], staged, False, False, {}, {}, {}, None))
        args.vnext_head = self.head
        self.assertIn("desk_build_unverified", build_dist.qualification_reasons(
            args, [], staged, False, False, {}, {"built": {"input_sha256": "ok"}},
            {"input_sha256": "ok"}, None))
        self.assertNotIn("desk_build_unverified", build_dist.qualification_reasons(
            args, [], staged, False, False, {}, {"built": {"input_sha256": "ok"}},
            {"input_sha256": "ok"}, None, {"source_sha256": "ok"}))

    def test_desk_build_receipt_binds_same_version_sources_fronts_and_three_exes(self):
        desk = self.stage / "desk"
        paths = {"shell/src/main.rs": b"fn main() {}\n",
                 "setup/ui/dist/index.html": b"<html>old</html>",
                 "svc/Cargo.toml": b"[package]\nversion='1.8.0'\n"}
        for name, content in paths.items():
            path = desk / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for part, exe in vnext_dist.DESK_EXES:
            path = desk / part / "target/release" / exe
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"MZ" + part.encode())
        receipt = desk / "installer/build/DESK-BUILT.json"
        note = vnext_dist.stamp_desk_build(desk, receipt)
        self.assertEqual(vnext_dist.verify_desk_build(desk, receipt), note)
        self.assertEqual(note["source_files"], len(paths))
        # The DIRECT packer route, not only build_win, must refuse a missing
        # build receipt before tests/downloads. A matching one reaches tests.
        body = self.stage / "body-target/release"
        body.mkdir(parents=True)
        for exe in ("praxis-body.exe", "praxis-bridge.exe"):
            (body / exe).write_bytes(b"MZ-body")
        relay = self.stage / "relay"
        for exe in (relay / "target/release/codex-proxy-server.exe", relay_src.linux_binary(relay)):
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"MZ-relay")
        argv = ["build_dist.py", "--tree", str(self.stage / "edition"), "--out", str(self.stage / "dist"),
                "--vnext-head", self.head, "--relay-root", str(relay)]
        with mock.patch.object(build_dist, "ROOT", self.repo), mock.patch.object(build_dist, "DESK", desk), \
             mock.patch.object(build_dist, "BODY_TARGET", body), \
             mock.patch.object(build_dist, "product_version", return_value=("1.8.0", {})), \
             mock.patch.object(sys, "argv", argv):
            with self.assertRaisesRegex(SystemExit, "desk executables/fronts lack"):
                build_dist.main()
        self.assertFalse((self.stage / "dist").exists())
        class ReachedTests(Exception):
            pass
        with mock.patch.object(build_dist, "ROOT", self.repo), mock.patch.object(build_dist, "DESK", desk), \
             mock.patch.object(build_dist, "BODY_TARGET", body), \
             mock.patch.object(build_dist, "product_version", return_value=("1.8.0", {})), \
             mock.patch.object(build_dist, "run_stands", side_effect=ReachedTests), \
             mock.patch.object(sys, "argv", argv + ["--desk-build-receipt", str(receipt)]):
            with self.assertRaises(ReachedTests):
                build_dist.main()
        # Build output and a secret-bearing local file are never source inputs.
        (desk / "setup/target/release/ignored.exe").write_bytes(b"other product")
        (desk / ".env.local").write_text("fixture-secret=123", encoding="utf-8")
        self.assertEqual(vnext_dist.verify_desk_build(desk, receipt), note)
        self.assertNotIn("fixture-secret", receipt.read_text(encoding="utf-8"))
        for name in paths:
            path = desk / name
            old = path.read_bytes()
            path.write_bytes(old + b"changed with version still 1.8.0")
            with self.assertRaisesRegex(vnext_dist.SourceMismatch, "differ"):
                vnext_dist.verify_desk_build(desk, receipt)
            path.write_bytes(old)
        for part, exe in vnext_dist.DESK_EXES:
            path = desk / part / "target/release" / exe
            old = path.read_bytes()
            path.write_bytes(b"old exe, same version")
            with self.assertRaisesRegex(vnext_dist.SourceMismatch, "differ"):
                vnext_dist.verify_desk_build(desk, receipt)
            path.write_bytes(old)

    def test_direct_builder_refuses_missing_native_inputs_before_download_or_output(self):
        output = self.stage / "distribution"
        argv = ["build_dist.py", "--tree", str(self.stage / "edition"),
                "--vnext-head", self.head, "--relay-root", str(self.stage / "relay"),
                "--out", str(output), "--skip-tests"]
        with mock.patch.object(build_dist, "ROOT", self.repo), mock.patch.object(sys, "argv", argv):
            with self.assertRaisesRegex(SystemExit, "binary inputs absent") as refusal:
                build_dist.main()
        self.assertIn("relay Linux", str(refusal.exception))
        self.assertFalse(output.exists())

    def test_builder_refuses_output_cleanup_within_selected_source(self):
        args = ["build_dist.py", "--tree", str(self.stage / "edition"),
                "--vnext-head", self.head, "--relay-root", str(self.stage / "relay"),
                "--out", str(self.stage / "edition")]
        with mock.patch.object(sys, "argv", args):
            with self.assertRaisesRegex(SystemExit, "cleanup overlaps edition"):
                build_dist.main()
        self.assertTrue((self.stage / "edition/agent.py").is_file())

    def test_binary_copy_drift_refuses_before_passport(self):
        src, dst = self.stage / "src.exe", self.stage / "out.exe"
        src.write_bytes(b"one")
        real_copy = shutil.copy2

        def altered(source, target):
            real_copy(source, target)
            Path(target).write_bytes(b"different")

        with mock.patch.object(build_dist.shutil, "copy2", side_effect=altered):
            with self.assertRaisesRegex(SystemExit, "changed during package copy"):
                build_dist.copy_checked_binary(src, dst)

    def test_full_test_runner_suppresses_edition_bytecode_writes(self):
        runner = HERE / "tests/run_all.py"
        self.assertTrue(runner.is_file())
        with mock.patch.object(build_dist.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as call:
            build_dist.run_stands(False, self.stage / "edition")
        self.assertEqual(call.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(call.call_args.kwargs["env"]["HELENE_TREE_SRC"], str(self.stage / "edition"))

    def test_windows_relay_build_selects_explicit_fresh_target_not_old_exe(self):
        root = self.stage / "relay"
        old = root / "target/release/codex-proxy-server.exe"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old")

        def build(argv, **_kwargs):
            self.assertEqual(argv[:4], ["cargo", "build", "--release", "--locked"])
            self.assertIn("--target", argv)
            self.assertIn("--target-dir", argv)
            self.assertEqual(argv[argv.index("--target-dir") + 1], str(root / "target"))
            new = root / "target/x86_64-pc-windows-msvc/release/codex-proxy-server.exe"
            new.parent.mkdir(parents=True)
            new.write_bytes(b"new")
            return SimpleNamespace(returncode=0)

        with contextlib.chdir(self.stage), mock.patch.object(relay_src.subprocess, "run", side_effect=build):
            note = relay_src.build(Path("relay"))  # relative CLI mirror must not double its target path
        self.assertEqual(old.read_bytes(), b"new")
        self.assertEqual(note["exe_sha256"], sha(old))
        self.assertEqual(note["build_method"], relay_src.WINDOWS_BUILD_METHOD)

    def test_linux_relay_extracts_by_immutable_image_id_not_shared_tag(self):
        root = self.stage / "relay"
        wanted_id = "sha256:" + "b" * 64
        calls = []

        def docker(argv, **_kwargs):
            calls.append(argv)
            if argv[1] == "build":
                Path(argv[argv.index("--iidfile") + 1]).write_text(wanted_id, encoding="ascii")
                return SimpleNamespace(returncode=0)
            if argv[1] == "create":
                return SimpleNamespace(returncode=0, stdout="fixture-container\n", stderr="")
            if argv[1] == "cp":
                out = Path(argv[-1])
                out.write_bytes(b"linux")
                return SimpleNamespace(returncode=0, stderr="")
            return SimpleNamespace(returncode=0)

        with mock.patch.object(relay_src.subprocess, "run", side_effect=docker):
            note = relay_src.build_linux(root, image="helene-relay-build:fixture", memory_limit="1536m")
        self.assertEqual(calls[0][2:4], ["--memory", "1536m"])
        self.assertEqual(calls[1], ["docker", "create", wanted_id])
        self.assertEqual(note["image_id"], wanted_id)
        self.assertEqual(note["memory_limit"], "1536m")
        self.assertEqual(note["build_method"], relay_src.LINUX_BUILD_METHOD)

    def test_server_cli_forwards_resource_limit_to_linux_builder(self):
        with mock.patch.object(sys, "argv", ["relay_src.py", "--mirror", str(self.stage / "relay"),
                                            "--build-linux", "--docker-memory", "1536m"]), \
             mock.patch.object(relay_src, "build_linux") as builder:
            self.assertEqual(relay_src.main(), 0)
        builder.assert_called_once_with(self.stage / "relay", memory_limit="1536m")


class RealPayloadWriter(unittest.TestCase):
    @staticmethod
    def fixture(base: Path) -> Path:
        out = base / "Helene"
        out.mkdir()
        for name, data in {"helene-setup.exe": b"MZfixture\n", "tree/agent.py": b"answer=1\n",
                           "app/static/index.html": b"<h1>hello</h1>",
                           "\u041f\u0415\u0420\u0412\u042b\u0419-\u0417\u0410\u041f\u0423\u0421\u041a.md": b"start\n"}.items():
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        build_dist.write_payload_manifest(out, "1.8.0", "Helene")
        return out

    def test_zip_and_setup_tail_contain_identical_product_bytes(self):
        from compression import zstd
        with tempfile.TemporaryDirectory(prefix="setup-payload-") as tmp:
            base = Path(tmp)
            out = self.fixture(base)
            zip_path = Path(shutil.make_archive(str(base / "Helene-1.8.0"), "zip", base, "Helene"))
            setup = build_dist.build_setup_exe(out, "1.8.0")
            tail = build_dist.check_tail(setup)
            with setup.open("rb") as fh:
                fh.seek(tail["offset"])
                compressed = io.BytesIO(fh.read(tail["length"]))
            with zstd.ZstdFile(compressed, "r") as stream:
                with tarfile.open(fileobj=stream, mode="r|") as archive:
                    from_setup = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
            with zipfile.ZipFile(zip_path) as archive:
                from_zip = {n.removeprefix("Helene/"): archive.read(n) for n in archive.namelist()
                            if not n.endswith("/")}
            self.assertEqual(from_zip, from_setup)
            self.assertEqual(from_setup["tree/agent.py"], b"answer=1\n")
            self.assertEqual(from_setup[".helene-payload.json"],
                             (out / ".helene-payload.json").read_bytes())

    @unittest.skipUnless(os.name == "nt" and os.environ.get("HELENE_RUST_PAYLOAD_CONSUMER") == "1",
                         "full Windows --rust gate drives the Rust setup consumer")
    def test_python_generated_setup_is_read_and_extracted_by_rust(self):
        with tempfile.TemporaryDirectory(prefix="setup-rust-consumer-") as tmp:
            out = self.fixture(Path(tmp))
            setup = build_dist.build_setup_exe(out, "1.8.0")
            env = dict(os.environ, HELENE_TEST_SETUP_EXE=str(setup),
                       HELENE_TEST_AGENT_SHA256=sha(out / "tree/agent.py"))
            result = subprocess.run(["cargo", "test", "--offline", "--locked", "--release",
                                     "--features", "custom-protocol",
                                     "python_packer_payload_is_readable_by_setup", "--", "--ignored"],
                                    cwd=HERE / "setup", env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=900)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            self.assertIn("1 passed", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows Rust setup consumer gate")
    def test_full_runner_arms_the_cross_language_consumer(self):
        import run_all
        observed = []
        def fake_run(_cmd, _cwd, name, _quiet, extra_env=None):
            observed.append((name, extra_env))
            return True, name
        with mock.patch.object(run_all, "python_stands", return_value=[HERE / "tests/t_vnext_dist_1810.py"]), \
             mock.patch.object(run_all, "window_stands", return_value=[]), \
             mock.patch.object(run_all, "CRATES", ()), \
             mock.patch.object(run_all.shutil, "which", return_value="cargo"), \
             mock.patch.object(run_all, "run", side_effect=fake_run), \
             mock.patch.object(sys, "argv", ["run_all.py", "--rust"]):
            self.assertEqual(run_all.main(), 0)
        self.assertEqual(observed[0][1], {"HELENE_RUST_PAYLOAD_CONSUMER": "1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
