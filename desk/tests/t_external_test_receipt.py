"""External gate joins exact bytes and retained output, not a claimed green flag."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import test_receipt


class ExternalReceipt(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "tests.json"
        self.note = {"kind": test_receipt.KIND, "head": "a" * 40,
                     "source_sha256": "b" * 64, "edition_digest": "c" * 64,
                     "relay_input_sha256": "d" * 64, "lanes": {}}
        for name, (platform, command) in test_receipt.COMMANDS.items():
            log = self.root / (name + ".log")
            text = ("Finished `dev` profile\n" if name.endswith("clippy") else
                    "  ok  fixture\nвсе зелёные\n" if name == "desk" else
                    "WINDOWS_SAFE_PASSED=1\n" if name == "windows-safe" else
                    "test result: ok. 1 passed; 0 failed\n" if name in {"body", "relay"} else
                    "Ran 1 test in 0.1s\n\nOK\n")
            log.write_text(text, encoding="utf-8")
            self.note["lanes"][name] = {"platform": platform, "command": command,
                "cwd_role": test_receipt.lane_root(name), "tests_executed": 1,
                "invocation_id": name + "-unique", "selected_edition_digest": "c" * 64,
                "environment": {"HELENE_RUST_PAYLOAD_CONSUMER": "1"},
                "exit_code": 0, "completed": True, "skips": [], "log": log.name,
                "log_sha256": test_receipt.sha(log)}
        selected = {n: str(self.root / n) for n in ("edition", "package", "relay", "logs")}
        row = self.note["lanes"]["windows-safe"]
        row["selected_paths"] = selected
        row["command"] = ["python", "windows_safe_gate.py", *sum((["--" + n, v] for n, v in selected.items()), [])]
        self.patch = mock.patch.object(test_receipt, "source_identity", return_value="b" * 64)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def verify(self):
        self.file.write_text(json.dumps(self.note), encoding="utf-8")
        return test_receipt.verify(self.file, self.root, "a" * 40, "c" * 64, "d" * 64)

    def test_exact_complete_bundle_keeps_skips_and_log_identity(self):
        self.note["lanes"]["desk"]["skips"] = [{"test": "setup crate", "reason": "separate Windows lane"}]
        got = self.verify()
        self.assertEqual(got["lanes"]["desk"]["skips"], self.note["lanes"]["desk"]["skips"])
        self.assertNotIn("log", got["lanes"]["desk"])

    def test_wrong_source_edition_relay_or_roster_refuses(self):
        # The exact private command is checked, but paths never ship in passport.
        private_command = list(self.note["lanes"]["windows-safe"]["command"])
        got = self.verify()
        public = got["lanes"]["windows-safe"]["command"]
        self.assertEqual(public[3::2], ["<edition>", "<package>", "<relay>", "<logs>"])
        self.assertNotIn(str(self.root), json.dumps(got))
        self.assertEqual(self.note["lanes"]["windows-safe"]["command"], private_command)
        for field in ("head", "source_sha256", "edition_digest", "relay_input_sha256"):
            with self.subTest(field=field):
                old = self.note[field]; self.note[field] = "wrong"
                with self.assertRaises(test_receipt.ReceiptError): self.verify()
                self.note[field] = old
        self.note["lanes"].pop("core-2")
        with self.assertRaises(test_receipt.ReceiptError): self.verify()

    def test_red_unknown_unfinished_or_smoke_command_refuses(self):
        row = self.note["lanes"]["edition-1"]
        for key, value in (("exit_code", 1), ("exit_code", False), ("completed", False),
                           ("command", ["python3", "smoke.py"]), ("platform", "win32"),
                           ("skips", [{"test": "unknown"}])):
            with self.subTest(key=key, value=value):
                old = row[key]; row[key] = value
                with self.assertRaises(test_receipt.ReceiptError): self.verify()
                row[key] = old

    def test_changed_missing_empty_or_outside_log_refuses(self):
        row = self.note["lanes"]["core-1"]
        log = self.root / row["log"]
        for value in (b"different\n", b""):
            log.write_bytes(value)
            with self.assertRaises(test_receipt.ReceiptError): self.verify()
        log.unlink()
        with self.assertRaises(test_receipt.ReceiptError): self.verify()
        row["log"] = "../outside.log"
        with self.assertRaises(test_receipt.ReceiptError): self.verify()

    def test_setup_skip_only_and_wrong_composed_working_directory_refuse(self):
        row = self.note["lanes"]["setup-consumer"]
        row["environment"] = {}
        with self.assertRaises(test_receipt.ReceiptError): self.verify()
        row["environment"] = {"HELENE_RUST_PAYLOAD_CONSUMER": "1"}
        row["tests_executed"] = 0
        with self.assertRaises(test_receipt.ReceiptError): self.verify()
        row["tests_executed"] = 1
        self.note["lanes"]["edition-1"]["cwd_role"] = "source/praxis"
        with self.assertRaises(test_receipt.ReceiptError): self.verify()

    def test_arbitrary_success_text_or_reused_invocation_does_not_prove_execution(self):
        row = self.note["lanes"]["core-1"]
        log = self.root / row["log"]
        log.write_text("a successful smoke", encoding="utf-8")
        row["log_sha256"] = test_receipt.sha(log)
        with self.assertRaises(test_receipt.ReceiptError): self.verify()
        self.assertEqual(test_receipt.log_count("setup-consumer", "Ran 1 test in 0.1s\nOK (skipped=1)\n"), 0)


if __name__ == "__main__": unittest.main()
