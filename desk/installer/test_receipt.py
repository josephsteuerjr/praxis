"""Exact-source external test evidence for the existing distribution builder.

The Linux-only Forge gate cannot run on a Windows build host. This join checks
its retained logs and the separate safe Windows consumer lane; it is not
installed-update acceptance, a signed certificate, or a second test runner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import re

KIND = "helene_external_tests_v1"
# Commands are explicit so a successful smoke cannot impersonate a full lane.
COMMANDS = {
    "core-1": ("linux", ["python3", "praxis_test.py", "discover", "-q"]),
    "core-2": ("linux", ["python3", "praxis_test.py", "discover", "-q"]),
    "edition-1": ("linux", ["python3", "praxis_test.py", "discover", "-q"]),
    "edition-2": ("linux", ["python3", "praxis_test.py", "discover", "-q"]),
    "desk": ("linux", ["python3", "tests/run_all.py", "--rust"]),
    "body": ("linux", ["cargo", "test", "--offline", "--locked", "--workspace"]),
    "body-clippy": ("linux", ["cargo", "clippy", "--offline", "--locked", "--workspace", "--", "-D", "warnings"]),
    "relay": ("linux", ["cargo", "test", "--offline", "--locked", "--bin", "codex-proxy-server"]),
    "relay-clippy": ("linux", ["cargo", "clippy", "--offline", "--locked", "--bin", "codex-proxy-server", "--", "-D", "warnings"]),
    "windows-safe": ("win32", ["python", "windows_safe_gate.py"]),
    "setup-consumer": ("win32", ["python", "tests/t_vnext_dist_1810.py", "RealPayloadWriter.test_python_generated_setup_is_read_and_extracted_by_rust"]),
}


class ReceiptError(ValueError):
    pass


def lane_root(name: str) -> str:
    if name.startswith("core-"):
        return "source/praxis"
    if name.startswith("edition-") or name.startswith("body"):
        return "stage/edition" + ("/body" if name.startswith("body") else "")
    if name.startswith("relay"):
        return "stage/relay"
    return "source/desk"


def log_count(name: str, text: str) -> int:
    """Join a successful command to its actual runner completion summary."""
    if name in {"body-clippy", "relay-clippy"}:
        return int(bool(re.search(r"Finished `dev` profile|Finished dev ", text)))
    if name == "desk":
        return len(re.findall(r"(?m)^  ok  ", text)) if "все зелёные" in text else 0
    if name == "windows-safe":
        return int(text.rsplit("WINDOWS_SAFE_PASSED=", 1)[-1].strip()) if "WINDOWS_SAFE_PASSED=" in text else 0
    if name in {"body", "relay"}:
        counts = re.findall(r"test result: ok\. (\d+) passed", text)
        return sum(map(int, counts))
    summaries = re.findall(r"Ran (\d+) tests? in ", text)
    if not summaries or not re.search(r"(?m)^OK(?: \(skipped=\d+\))?$", text):
        return 0
    count = int(summaries[-1])
    skipped = re.findall(r"(?m)^OK \(skipped=(\d+)\)$", text)
    return count - (int(skipped[-1]) if skipped else 0)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_identity(repo: Path, head: str) -> str:
    """Tracked executable/test/build inputs, portable across OS checkout paths.

    Generated fronts have their own build receipt. Git blob bytes bind this
    evidence; the builder separately checks the live clean source composition.
    """
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    if actual != head:
        raise ReceiptError("test source HEAD differs")
    rows = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "-z", head,
                                    "desk", "praxis", "helene/core"])
    if not rows:
        raise ReceiptError("test source inventory empty")
    return hashlib.sha256(rows).hexdigest()


def command_matches(name: str, actual: object, base: list[str], row: dict) -> bool:
    if name != "windows-safe":
        return actual == base
    if not isinstance(actual, list) or actual[:2] != base or len(actual) != 10:
        return False
    args = actual[2:]
    if args[::2] != ["--edition", "--package", "--relay", "--logs"]:
        return False
    selected = row.get("selected_paths")
    return (isinstance(selected, dict)
            and all(isinstance(v, str) and v for v in args[1::2])
            and selected == dict(zip(("edition", "package", "relay", "logs"), args[1::2])))


def verify(path: Path, repo: Path, head: str, edition_digest: str,
           relay_input: str) -> dict:
    path = Path(path).resolve()
    try:
        note = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptError("external test receipt unavailable") from exc
    expected = {"kind": KIND, "head": head,
                "source_sha256": source_identity(repo, head),
                "edition_digest": edition_digest, "relay_input_sha256": relay_input}
    if not isinstance(note, dict) or any(note.get(k) != v for k, v in expected.items()):
        raise ReceiptError("external test source/edition/relay identity differs")
    lanes = note.get("lanes")
    if not isinstance(lanes, dict) or set(lanes) != set(COMMANDS):
        raise ReceiptError("external test lane roster incomplete or unexpected")
    seen = set()
    for name, (platform, command) in COMMANDS.items():
        row = lanes[name]
        if (not isinstance(row, dict) or row.get("platform") != platform
                or not command_matches(name, row.get("command"), command, row) or type(row.get("exit_code")) is not int
                or row["exit_code"] != 0 or row.get("completed") is not True
                or row.get("cwd_role") != lane_root(name)
                or type(row.get("tests_executed")) is not int or row["tests_executed"] < 1
                or not isinstance(row.get("skips"), list)):
            raise ReceiptError("external test failed/incomplete: " + name)
        identity = row.get("invocation_id")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ReceiptError("external test invocation reused/missing: " + name)
        seen.add(identity)
        if name in {"desk", "windows-safe"} and row.get("selected_edition_digest") != edition_digest:
            raise ReceiptError("desk tested another selected edition")
        if name == "setup-consumer" and (row.get("environment", {}).get("HELENE_RUST_PAYLOAD_CONSUMER") != "1"
                                         or row["skips"]):
            raise ReceiptError("Windows setup consumer did not execute")
        # The bundle travels with the receipt. No arbitrary host-path reads.
        rel = row.get("log")
        if not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ReceiptError("external test log path invalid: " + name)
        log = path.parent / rel
        if (not log.is_file() or log.is_symlink() or log.stat().st_size == 0
                or sha(log) != row.get("log_sha256")):
            raise ReceiptError("external test log missing/changed: " + name)
        if log_count(name, log.read_text(encoding="utf-8", errors="replace")) != row["tests_executed"]:
            raise ReceiptError("external test execution summary differs: " + name)
        for skip in row["skips"]:
            if not isinstance(skip, dict) or not skip.get("test") or not skip.get("reason"):
                raise ReceiptError("external test skip lacks scope/reason: " + name)
    # Keep audit details in the shipped passport, never local log paths.
    public_lanes = {}
    for name, row in lanes.items():
        public = {k: row[k] for k in ("platform", "cwd_role", "tests_executed", "exit_code", "log_sha256", "skips")}
        public["command"] = list(COMMANDS[name][1])
        if name == "windows-safe":
            for role in ("edition", "package", "relay", "logs"):
                public["command"].extend(["--" + role, "<" + role + ">"])
        public_lanes[name] = public
    return {**expected, "receipt_sha256": sha(path), "lanes": public_lanes}
