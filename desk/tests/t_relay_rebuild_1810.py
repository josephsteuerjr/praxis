"""Private-source Rust relay kit: closed bytes, bounded staging, edited candidate, package seam.

All build invocations below use an explicit stub; they test the packer's
contract, NOT whether a real MSVC/linker, clean offline machine or vendor API
works. A real tracked-source `cargo --locked --offline build` is recorded
separately and does not certify the private shipping mirror.
"""
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
import tempfile
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "installer"))
import build_dist
import relay_rebuild_kit as kit


class RustKit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mirror = self.root / "private-relay"
        (self.mirror / "src").mkdir(parents=True)
        (self.mirror / "src" / "main.rs").write_text('fn main() { println!("relay"); }\n', encoding="utf-8")
        (self.mirror / "Cargo.toml").write_text('[package]\nname="codex-proxy-server"\nversion="0.1.0"\n', encoding="utf-8")
        (self.mirror / "Cargo.lock").write_text('version = 3\n[[package]]\nname = "codex-proxy-server"\nversion = "0.1.0"\n', encoding="utf-8")
        (self.mirror / "build.rs").write_text('fn main() { println!("cargo:rerun-if-changed=src/main.rs"); }\n', encoding="utf-8")
        (self.mirror / ".cargo").mkdir()
        (self.mirror / ".cargo" / "config.toml").write_text('[build]\njobs=1\n', encoding="utf-8")
        old = self.mirror / "target/release/codex-proxy-server.exe"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"older binary fixture")
        digest, count = kit._legacy_digest(self.mirror)
        (self.mirror / "RELAY-SOURCE.json").write_text(json.dumps({"digest": digest, "files": count,
                "from": "private-should-not-leak", "note": "also-private"}), encoding="utf-8")
        (self.mirror / "RELAY-BUILD.json").write_text(json.dumps({"digest": digest,
                "exe_sha256": kit._sha(old), "from": "private-should-not-leak"}), encoding="utf-8")
        self.vendor = self.root / "vendor"
        self.vendor.mkdir()
        (self.vendor / "README").write_text("fixture has no dependencies", encoding="utf-8")
        self.rust = self.root / "toolchain"
        (self.rust / "bin").mkdir(parents=True)
        for name in ("cargo.exe", "rustc.exe"):
            (self.rust / "bin" / name).write_bytes(b"fixture " + name.encode())
        self.native = self.root / "native"
        for name in ("bin", "lib", "include"):
            (self.native / name).mkdir(parents=True)
            (self.native / name / "one.dat").write_bytes(name.encode())
        (self.native / "bin" / "link.exe").write_bytes(b"synthetic linker")
        (self.native / "bin" / "cl.exe").write_bytes(b"synthetic compiler")
        (self.native / "layout.json").write_text(json.dumps({"target": kit.TARGET,
                    "path": ["bin"], "lib": ["lib"], "include": ["include"],
                    "linker": "bin/link.exe", "cc": "bin/cl.exe"}), encoding="utf-8")
        self.out = self.root / "kit"

    @staticmethod
    def fake_build(bundle, source, build_root, *, timeout=1800):
        before = kit.inventory(source, source=True)
        build_root.mkdir()
        binary = build_root / "target" / kit.TARGET / "release" / kit.BINARY
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"candidate:" + kit._digest(before).encode())
        return binary, {"source_inputs_sha256": kit._digest(before),
                        "binary_sha256": kit._sha(binary), "target": kit.TARGET,
                        "cargo_mode": "locked_offline", "network_isolation": "unverified",
                        "native_redistribution": "unreviewed"}

    def stage(self):
        with patch.object(kit, "_build", side_effect=self.fake_build):
            return kit.stage(self.mirror, self.vendor, self.rust, self.native, self.out)

    def test_stage_and_verify_complete_inventory_and_private_receipt_cut(self):
        (self.mirror / ".git").write_text("gitdir: private-should-not-leak", encoding="utf-8")
        note = self.stage()
        self.assertEqual(kit.verify(self.out), note)
        source = note["components"]["source"]["files"]
        self.assertIn("build.rs", source)
        self.assertIn(".cargo/config.toml", source)
        self.assertNotIn("RELAY-SOURCE.json", source)
        self.assertNotIn("RELAY-BUILD.json", source)
        self.assertNotIn(".git", source)
        self.assertNotIn("private-should-not-leak", json.dumps(note))
        self.assertEqual((self.out / "helene-relay.exe").read_bytes(),
                         b"candidate:" + note["components"]["source"]["sha256"].encode())
        self.assertNotEqual(note["binary_sha256"], note["origin"]["previous_binary_sha256"])
        self.assertEqual(note["qualification"], "source_bound_offline_unverified")
        self.assertFalse((self.out / ".candidate").exists())

    def test_full_inputs_distinguish_build_script_and_config_from_legacy_digest(self):
        old, _ = kit._legacy_digest(self.mirror)
        before = kit._digest(kit.inventory(self.mirror, source=True, private_mirror=True))
        (self.mirror / "build.rs").write_text("fn main() { println!(\"changed\"); }\n", encoding="utf-8")
        self.assertEqual(kit._legacy_digest(self.mirror)[0], old)
        self.assertNotEqual(kit._digest(kit.inventory(self.mirror, source=True, private_mirror=True)), before)
        (self.mirror / ".cargo/config.toml").write_text("[build]\njobs=2\n", encoding="utf-8")
        self.assertNotEqual(kit._digest(kit.inventory(self.mirror, source=True, private_mirror=True)), before)
        self.stage()
        self.assertNotEqual(kit.verify(self.out)["origin"]["source_inputs_sha256"], before)

    def test_missing_mirror_is_refusal_not_public_source_substitution(self):
        shutil.rmtree(self.mirror)
        with self.assertRaisesRegex(kit.KitError, "closure_missing_or_linked"):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_stale_receipt_and_binary_refuse_before_copy(self):
        (self.mirror / "target/release/codex-proxy-server.exe").write_bytes(b"different")
        with self.assertRaisesRegex(kit.KitError, "legacy_build_or_binary_mismatch"):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_private_filename_and_key_bytes_refuse(self):
        secret = self.mirror / "src" / "auth.json"
        secret.write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(kit.KitError, "private_source_filename"):
            self.stage()
        secret.unlink()
        secret = self.mirror / "src" / "fixture.txt"
        secret.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic", encoding="utf-8")
        with self.assertRaisesRegex(kit.KitError, "private_key_in_source"):
            self.stage()

    def test_symlink_or_junction_refuses(self):
        link = self.mirror / "src" / "foreign.txt"
        try:
            link.symlink_to(self.root / "outside.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not available to this Windows user")
        with self.assertRaisesRegex(kit.KitError, "linked_or_nonregular_file"):
            self.stage()

    def test_missing_vendor_checksum_when_locked_registry_dep(self):
        with (self.mirror / "Cargo.lock").open("a", encoding="utf-8") as stream:
            stream.write('[[package]]\nname="crate"\nversion="1.0.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n')
        old, count = kit._legacy_digest(self.mirror)
        (self.mirror / "RELAY-SOURCE.json").write_text(json.dumps({"digest": old, "files": count}), encoding="utf-8")
        (self.mirror / "RELAY-BUILD.json").write_text(json.dumps({"digest": old,
                    "exe_sha256": kit._sha(self.mirror / "target/release/codex-proxy-server.exe")}), encoding="utf-8")
        with self.assertRaisesRegex(kit.KitError, "vendor_missing_checksums"):
            self.stage()

    def test_native_path_escape_refuses(self):
        (self.native / "layout.json").write_text(json.dumps({"target": kit.TARGET,
                       "path": ["../outside"], "lib": ["lib"], "include": ["include"],
                       "linker": "bin/link.exe", "cc": "bin/cl.exe"}), encoding="utf-8")
        with self.assertRaisesRegex(kit.KitError, "native_layout_outside_or_missing"):
            self.stage()
        for section, bad in (("path", "\\Windows\\System32"), ("linker", "\\Windows\\System32\\link.exe")):
            with self.subTest(section=section):
                native = {"target": kit.TARGET, "path": ["bin"], "lib": ["lib"],
                          "include": ["include"], "linker": "bin/link.exe", "cc": "bin/cl.exe"}
                native[section] = [bad] if section == "path" else bad
                (self.native / "layout.json").write_text(json.dumps(native), encoding="utf-8")
                with self.assertRaisesRegex(kit.KitError, "native_(layout|executable)_outside_or_missing"):
                    self.stage()

    def test_original_kit_preserved_while_editing_candidate(self):
        original = self.stage()
        edited = self.root / "edited"
        shutil.copytree(self.out / "source", edited)
        (edited / "src/main.rs").write_text('fn main() { println!("edited"); }\n', encoding="utf-8")
        candidate = self.root / "next"
        with patch.object(kit, "_build", side_effect=self.fake_build):
            new = kit.candidate(self.out, edited, candidate)
        self.assertNotEqual(new["candidate_binary_sha256"], original["binary_sha256"])
        self.assertEqual(new["previous_source_sha256"], original["components"]["source"]["sha256"])
        self.assertEqual(new["switch"], "not_performed")
        self.assertEqual(kit.verify(self.out), original)
        self.assertTrue((candidate / "candidate-receipt.json").is_file())

    def test_aliased_candidate_output_cannot_mutate_edited_source(self):
        original = self.stage()
        edited = self.root / "edited"
        shutil.copytree(self.out / "source", edited)
        alias = self.root / "edited-alias"
        if os.name == "nt":
            linked = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(edited)],
                                    capture_output=True)
            if linked.returncode:
                self.skipTest("directory junction unavailable on this host")
        else:
            alias.symlink_to(edited, target_is_directory=True)
        with self.assertRaisesRegex(kit.KitError, "candidate_output_inside_input"):
            kit.candidate(self.out, edited, alias / "next")
        self.assertFalse((edited / "next").exists())
        self.assertEqual(kit.verify(self.out), original)

    def test_verify_detects_build_script_binary_and_unlisted_item_drift(self):
        self.stage()
        for rel in ("source/build.rs", "helene-relay.exe", "native/bin/one.dat"):
            with self.subTest(rel=rel):
                path = self.out / rel
                old = path.read_bytes()
                path.write_bytes(old + b"modified")
                with self.assertRaises(kit.KitError):
                    kit.verify(self.out)
                path.write_bytes(old)
        (self.out / "surprise.txt").write_bytes(b"x")
        with self.assertRaisesRegex(kit.KitError, "kit_unlisted_top_level_item"):
            kit.verify(self.out)

    def test_verifier_refuses_private_receipt_added_after_stage(self):
        self.stage()
        (self.out / "source/RELAY-SOURCE.json").write_text('{"from":"private"}', encoding="utf-8")
        with self.assertRaisesRegex(kit.KitError, "private_receipt_in_kit"):
            kit.verify(self.out)

    def test_top_level_recipe_symlink_refuses_even_with_same_bytes(self):
        self.stage()
        recipe = self.out / "rebuild.py"
        saved = self.root / "external-identical.py"
        shutil.copyfile(recipe, saved)
        recipe.unlink()
        try:
            recipe.symlink_to(saved)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(kit.KitError, "kit_executable_or_recipe_missing"):
            kit.verify(self.out)

    def test_package_passport_has_only_public_hashes_and_selected_exe(self):
        self.stage()
        row = build_dist.relay_kit_provenance(self.out, self.out / "helene-relay.exe")
        self.assertEqual(row["rebuild_kit"]["binary_sha256"], kit.verify(self.out)["binary_sha256"])
        self.assertEqual(row["built"]["digest"], kit.verify(self.out)["components"]["source"]["sha256"])
        self.assertEqual(row["built"]["digest_kind"], "byte_exact_relay_source_inputs")
        self.assertEqual(row["digest"], kit.verify(self.out)["origin"]["legacy_source_digest"])
        self.assertNotIn("private-should-not-leak", json.dumps(row))
        package = self.root / "package"
        package.mkdir()
        shutil.copyfile(self.out / "helene-relay.exe", package / "helene-relay.exe")
        copied = build_dist.copy_relay_kit(self.out, package)
        self.assertEqual(copied, kit.verify(package / "relay-rebuild"))
        self.assertEqual((package / "relay-rebuild/source/build.rs").read_bytes(),
                         (self.out / "source/build.rs").read_bytes())
        self.assertFalse((package / "relay-rebuild/source/RELAY-SOURCE.json").exists())
        manifest = build_dist.write_payload_manifest(package, "0.0.0", "Helene")
        self.assertIn("relay-rebuild", manifest["top"])
        self.assertIn("helene-relay.exe", manifest["kit"])
        self.assertGreater(manifest["files"], len(copied["components"]["source"]["files"]))
        other = self.root / "other.exe"
        other.write_bytes(b"not the same binary")
        with self.assertRaisesRegex(SystemExit, "differs"):
            build_dist.relay_kit_provenance(self.out, other)

    def test_extracted_recipe_verifies_without_installer_or_project_import(self):
        note = self.stage()
        moved = self.root / "unpacked"
        self.out.rename(moved)
        check = subprocess.run([sys.executable, "-B", str(moved / "rebuild.py"), "verify", "--kit", str(moved)],
                               cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(check.stdout)["binary_sha256"], note["binary_sha256"])

    def test_package_secret_scan_covers_new_relay_source(self):
        self.stage()
        out = self.root / "package"
        out.mkdir()
        shutil.copytree(self.out, out / "relay-rebuild")
        # The actual product credential floor must accept the *clean* recipe.
        # Mocking it away formerly hid the recipe's own literal PEM marker.
        with patch.object(build_dist, "_deny_strings", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            scanned = build_dist.scan_for_secrets(out, DESK.parent / "praxis", scan_runtime=False)
        self.assertGreater(scanned, 1)
        source = out / "relay-rebuild/source/src/main.rs"
        source.write_text('API_KEY="synthetic123456secret"\n', encoding="utf-8")
        with patch.object(build_dist, "_deny_strings", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(SystemExit, "relay-rebuild.*main.rs"):
            build_dist.scan_for_secrets(out, DESK.parent / "praxis", scan_runtime=False)

    def test_real_build_path_construction_uses_absolute_inputs_with_relative_cli(self):
        self.stage()
        work = self.root / "relative-output"
        observed = {}
        def cargo(command, *, cwd, env, capture_output, timeout):
            observed.update({"command": command, "cwd": cwd, "env": env})
            exe = Path(env["CARGO_TARGET_DIR"]) / kit.TARGET / "release" / kit.BINARY
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"synthetic-command-output")
            return type("Completed", (), {"returncode": 0})()
        # TEMP and the checkout may be on different Windows drives. Keep the
        # relative-path experiment on TEMP's own drive, not Path.cwd()'s.
        with contextlib.chdir(self.root), patch.object(kit.subprocess, "run", side_effect=cargo):
            rel = lambda path: Path(os.path.relpath(path, self.root))
            exe, row = kit._build(rel(self.out), rel(self.out / "source"), rel(work))
        self.assertEqual(exe, work / "target" / kit.TARGET / "release" / kit.BINARY)
        self.assertEqual(observed["cwd"], self.out / "source")
        self.assertEqual(observed["command"][0], str(self.out / "rust/bin/cargo.exe"))
        self.assertEqual(observed["env"]["RUSTC"], str(self.out / "rust/bin/rustc.exe"))
        self.assertEqual(observed["env"]["CARGO_TARGET_DIR"], str(work / "target"))
        self.assertEqual(observed["env"]["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"], str(self.out / "native/bin/link.exe"))
        self.assertEqual(row["binary_sha256"], kit._sha(exe))

    def test_existing_output_is_not_replaced(self):
        self.out.mkdir()
        (self.out / "keep").write_bytes(b"mine")
        with self.assertRaisesRegex(kit.KitError, "kit_output_exists_preserved"):
            self.stage()
        self.assertEqual((self.out / "keep").read_bytes(), b"mine")

    def test_output_under_mirror_refuses_before_copy(self):
        with patch.object(kit, "_build", side_effect=self.fake_build), self.assertRaisesRegex(kit.KitError, "kit_output_inside_input"):
            kit.stage(self.mirror, self.vendor, self.rust, self.native, self.mirror / "accidental-kit")
        self.assertFalse((self.mirror / "accidental-kit").exists())

    def test_candidate_nested_under_original_refuses_without_mutation(self):
        old = self.stage()
        with self.assertRaisesRegex(kit.KitError, "candidate_output_inside_input"):
            kit.candidate(self.out, self.out / "source", self.out / "nested")
        self.assertFalse((self.out / "nested").exists())
        self.assertEqual(kit.verify(self.out), old)

    def test_cannot_relabel_manifest_as_qualified_without_new_proof(self):
        self.stage()
        path = self.out / kit.MANIFEST
        original = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (("qualification", "offline_certified"), ("network_isolation", "verified"),
                           ("cargo_mode", "online")):
            changed = json.loads(json.dumps(original))
            if key == "qualification":
                changed[key] = value
            else:
                changed["build"][key] = value
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.subTest(key=key), self.assertRaisesRegex(kit.KitError, "kit_qualification_mismatch"):
                kit.verify(self.out)
        path.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(kit.verify(self.out), original)

    def test_stage_refuses_source_mutation_during_candidate_build(self):
        def mutator(bundle, source, build_root, *, timeout=1800):
            exe, result = self.fake_build(bundle, source, build_root, timeout=timeout)
            (source / "build.rs").write_text("fn main() { panic!(\"late\"); }\n", encoding="utf-8")
            return exe, result
        with patch.object(kit, "_build", side_effect=mutator), self.assertRaisesRegex(kit.KitError, "source_changed_after_build"):
            kit.stage(self.mirror, self.vendor, self.rust, self.native, self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(len(list(self.root.glob("kit.preparing-*"))), 1, "partial copy is retained, not called a kit")

    def test_selected_kit_requires_partial_and_refuses_before_output_effect(self):
        output = self.root / "dist"
        with patch.object(sys, "argv", ["build_dist.py", "--relay-rebuild-kit", str(self.out), "--out", str(output)]), \
             self.assertRaisesRegex(SystemExit, "only as --allow-partial"):
            build_dist.main()
        self.assertFalse(output.exists())
        with patch.object(sys, "argv", ["build_dist.py", "--relay-rebuild-kit", str(self.out), "--allow-partial", "--out", str(output)]), \
             self.assertRaisesRegex(SystemExit, "kit is incomplete"):
            build_dist.main()
        self.assertFalse(output.exists())

    def test_valid_kit_enters_main_builder_with_partial_mode(self):
        self.stage()
        output = self.root / "dist"
        class EnteredStands(Exception):
            pass
        with patch.object(sys, "argv", ["build_dist.py", "--relay-rebuild-kit", str(self.out),
                                        "--allow-partial", "--out", str(output)]), \
             patch.object(build_dist, "live_root", return_value=self.mirror), \
             patch.object(build_dist, "product_version", return_value=("0.0.0", {})), \
             patch.object(build_dist, "run_stands", side_effect=EnteredStands), \
             self.assertRaises(EnteredStands):
            build_dist.main()
        # A positive selection reaches the normal builder; the unrelated
        # frontend/runtime/full-distribution steps have not run here.
        self.assertFalse(output.exists())

    def test_output_nested_inside_selected_kit_refuses_before_effect(self):
        self.stage()
        output = self.out / "dist"
        with patch.object(sys, "argv", ["build_dist.py", "--relay-rebuild-kit", str(self.out),
                                        "--allow-partial", "--out", str(output)]), \
             self.assertRaisesRegex(SystemExit, "must not contain each other"):
            build_dist.main()
        self.assertFalse(output.exists())
        kit.verify(self.out)  # input still byte-identical and complete


if __name__ == "__main__":
    unittest.main(verbosity=2)
