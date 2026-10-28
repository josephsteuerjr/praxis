"""Disposable workshop acceptance gate; run on an isolated writable Linux checkout.

This is a focused consumer check, not the full release battery or a live install.
`--list` is safe on the owner's Windows laptop; execution is Linux-only because
Forge tests can start process trees. When Python product dependencies and Node
reside in different sealed images, run `--slice core` and `--slice ui` on the
same SHA; neither slice alone is the complete focused gate. The provenance
block is printed before tests so a red run cannot be confused with another tree.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRAXIS = ROOT / "praxis"
DESK = ROOT / "desk"
PY_MODULES = (
    "test_tool_pointers", "test_workshop", "test_hands",
    "test_forge_contract_0610", "test_forge_tree_1310", "test_forge_focus_read_1410",
    "test_forge_timeout_custody_1410",
)
DESK_MODULES = (
    "t_activity.py", "t_run_display.py", "t_anatomy_tools.py", "t_forge_tree_ui.py",
)
NODE_TESTS = ("actions.test.mjs",)
EDITION_TEST = ROOT / "helene" / "core" / "test_edition_workshop_1410.py"


def _identity() -> dict[str, object]:
    def git(*args: str) -> str:
        run = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20)
        if run.returncode:
            raise RuntimeError(f"git {' '.join(args)} refused: {run.stderr.strip()[:300]}")
        return run.stdout.strip()
    # The release container's Git predates `branch --show-current`; the
    # supported rev-parse spelling preserves detached HEAD as an explicit fact.
    return {"head": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="source-bound, disposable workshop acceptance")
    ap.add_argument("--list", action="store_true", help="show commands without running tests")
    ap.add_argument("--slice", choices=("all", "core", "ui"), default="all",
                    help="all when Python+Node are installed together; otherwise run core and ui on the same SHA")
    ap.add_argument("--require-clean", action="store_true", help="refuse dirty source for a release gate")
    args = ap.parse_args(argv)
    # Enter the EXISTING hermetic Praxis runner before importing the edition's
    # test module; edition overrides must lead sys.path for the composed product.
    edition_entry = ("import runpy,sys; "
                     f"sys.path[:0]=[{str(EDITION_TEST.parent)!r},{str(PRAXIS)!r}]; "
                     "sys.argv=['praxis_test.py','test_edition_workshop_1410','-v']; "
                     f"runpy.run_path({str(PRAXIS / 'praxis_test.py')!r},run_name='__main__')")
    commands = [
        ("Praxis model/Forge", [sys.executable, "praxis_test.py", "-v", *PY_MODULES], PRAXIS),
        ("Helene edition core", [sys.executable, "-c", edition_entry], ROOT),
        *((name, [sys.executable, str(DESK / "tests" / name)], DESK)
          for name in DESK_MODULES),
        ("Helene shared steps", ["node", "--test", *(str(DESK / "app" / "test" / p)
                                                       for p in NODE_TESTS)], DESK / "app"),
    ]
    if args.slice == "core":
        commands = commands[:2]
    elif args.slice == "ui":
        commands = commands[2:]
    if args.list:
        for name, command, cwd in commands:
            print(f"{name} [{cwd}]: {' '.join(command)}")
        return 0
    if sys.platform != "linux":
        print("REFUSED: process-capable Forge acceptance runs only in isolated Linux; use --list here.",
              file=sys.stderr)
        return 2
    try:
        identity = _identity()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"REFUSED: source identity unavailable: {exc}", file=sys.stderr)
        return 2
    print("SOURCE " + json.dumps(identity, sort_keys=True), flush=True)
    if args.require_clean and identity["dirty"]:
        print("REFUSED: dirty source is not a frozen release candidate", file=sys.stderr)
        return 2
    if args.slice != "core" and not shutil.which("node"):
        print("REFUSED: node missing; Hélène UI consumer would go untested", file=sys.stderr)
        return 2
    core_inputs = [EDITION_TEST, *(PRAXIS / (n + ".py") for n in PY_MODULES)]
    ui_inputs = [*(DESK / "tests" / n for n in DESK_MODULES),
                 *(DESK / "app" / "test" / n for n in NODE_TESTS)]
    selected_inputs = (core_inputs if args.slice == "core" else
                       ui_inputs if args.slice == "ui" else core_inputs + ui_inputs)
    missing = [str(p) for p in selected_inputs if not p.is_file()]
    if missing:
        print("REFUSED: acceptance input missing: " + ", ".join(missing), file=sys.stderr)
        return 2
    failed = []
    for name, command, cwd in commands:
        print(f"RUN {name}", flush=True)
        try:
            result = subprocess.run(command, cwd=cwd, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
            failed.append(name)
            continue
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {name} (exit={result.returncode})",
              flush=True)
        if result.returncode:
            failed.append(name)
    print(f"ACCEPTANCE {args.slice} " + ("PASS" if not failed else "FAIL: " + ", ".join(failed)), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
