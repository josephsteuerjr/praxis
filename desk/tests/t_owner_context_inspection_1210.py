"""Owner context and post-upgrade inspection through actual runtime functions."""
import ast
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))
sys.path.insert(0, str(DESK))
import owner_context
import package_notice
import update_inspection


def function(name, globals):
    source = ast.parse((DESK / "localharness/runner.py").read_text(encoding="utf8"))
    node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "runner.py", "exec"), globals)
    return globals[name]


class Context(unittest.TestCase):
    def life(self):
        return types.SimpleNamespace(HOT_TOKEN_CAP=40000, hot_bounds=lambda p: (150,250,300,40000), tape_chars_for=lambda p: 5500)

    def test_owner_window_and_dm_are_fuller_without_changing_other_chats(self):
        life = self.life()
        owner_context.configure(life, {"telegram": {"owner_id": 12}})
        for place in ("window", "window-abcdef12", "pult", "12"):
            self.assertEqual(life.tape_chars_for(place), 128000)
            self.assertEqual(life.hot_bounds(place)[2], 600)
        for place in ("13", "-123"):
            self.assertEqual(life.tape_chars_for(place), 5500)
            self.assertEqual(life.hot_bounds(place)[2], 300)

    def test_dialogue_keeps_actual_55_rows_and_tail_owner_request(self):
        rows = [{"direction": "out" if i % 2 else "in", "line": f"row{i}:" + "x"*400} for i in range(55)]
        life = self.life(); owner_context.configure(life, {})
        seen = []
        life.hot_records = lambda place, limit: (seen.append(limit) or rows[-limit:])
        def tape(records, cap):
            out = list(records)
            while len(out) > 1 and sum(len(x["line"]) for x in out) > cap: out.pop(0)
            return out
        life.tape_window = tape
        dialogue = function("_dialogue", {"_life": life, "log": logging.getLogger()})
        history, current = dialogue("window")
        whole = "\n".join(r["content"] for r in history) + current
        self.assertIn("row0:", whole); self.assertIn("row54:", current)
        self.assertEqual(seen, [600])
        history, current = dialogue("someone_else")
        self.assertNotIn("row0:", "\n".join(r["content"] for r in history) + current)

    def test_reconfigure_restores_other_owner_and_preserves_custom_budgets(self):
        life = self.life()
        owner_context.configure(life, {"telegram": {"owner_id": 12}})
        owner_context.configure(life, {"telegram": {"owner_id": 13}, "owner_context": {"messages": 1000, "characters": 250000}})
        self.assertEqual(life.tape_chars_for("12"), 5500)
        self.assertEqual(life.hot_bounds("13")[2], 1000)
        self.assertEqual(life.tape_chars_for("window"), 250000)


class Inspection(unittest.TestCase):
    def test_upgrade_detected_even_when_prior_runner_never_recorded_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "program"; program.mkdir(); tree = Path(tmp) / "home"
            package_notice.write(program / "helene-build.json", {"complete": True, "version": "1.6.0"})
            package_notice.write(program / "helene-install.json", {"version": "1.6.0", "previous_version": "1.5.9"})
            package_notice.observe(tree, program, {"installed": {"version": "1.6.0"}})
            receipt = package_notice.pending(tree)
            self.assertEqual(receipt["from_version"], "1.5.9")
            package_notice.observe(tree, program, {})
            self.assertEqual(package_notice.pending(tree)["id"], receipt["id"])

    def test_report_covers_all_release_goals_with_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            current = json.loads((DESK/"resources/update-inspection.json").read_text(encoding="utf8"))["version"]
            req = update_inspection.ensure(tree, {"id": "request", "to_version": current})
            self.assertEqual({g["id"] for g in req["goals"]}, {
                "agent_pause", "passive_feed", "fold_worker_status", "owner_context",
                "memory_line", "update_inspection", "system_journal", "computer_latency",
                "telegram_continuity", "emergency_stop", "compatibility",
            })
            with self.assertRaises(ValueError): update_inspection.report(tree, "request", "ok", [])
            checks = [{"id": g["id"], "status": "unverified", "evidence": "No physical access to that device"} for g in req["goals"]]
            result = update_inspection.report(tree, "request", "Physical checks remain", checks)
            self.assertEqual(result["state"], "reported")
            self.assertEqual(update_inspection.ensure(tree, {"id": "request", "to_version": current}), result)
            update_inspection.ensure(tree, {"id": "next", "to_version": "9.99.0"})
            self.assertEqual(len(list((tree / "memory/.state/update-inspections").glob("*.json"))), 1)

    def run_notice(self, tree, outcome, report=False):
        receipt = {"id": "req", "kind": "package", "state": "installed", "to_version": "1.6.0"}
        marks = []
        def turn(*args, **kwargs):
            if report:
                request = update_inspection.read(update_inspection.path(tree))
                update_inspection.report(tree, "req", "Checked", [{"id": g["id"], "status": "passed", "evidence": "isolated check output"} for g in request["goals"]])
            return outcome
        updates = types.SimpleNamespace(pending_report=lambda t: receipt, report_mark=lambda *a: {},
            report_note=lambda *a, **k: "Inspection", mark_reported=lambda *a, **k: marks.append(k), SYSTEM_SPEAKER="Hélène")
        desk = types.SimpleNamespace(archive=lambda *a, **k: None, life=lambda *a, **k: None)
        namespace = dict(_tree=tree, _desk=desk, updates=updates, _speaker="owner", _UPDATE_TRIES=3,
            _UPDATE_RETRY_SEC=120, time=types.SimpleNamespace(time=lambda: 100), _now=lambda: types.SimpleNamespace(timestamp=lambda:100),
            _turn_in_window=turn, log=logging.getLogger())
        with mock.patch.dict(sys.modules, {"llm": types.SimpleNamespace(configured=lambda:True)}):
            function("_update_report_due", namespace)()
        return marks

    def test_spoken_silent_and_checkpoint_never_substitute_for_inspection(self):
        for outcome in ("spoken", "silent", "deferred", "failed"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                marks = self.run_notice(Path(tmp), outcome)
                self.assertFalse(any(m.get("done") for m in marks))

    def test_saved_inspection_completes_even_when_turn_is_checkpointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            marks = self.run_notice(Path(tmp), "deferred", report=True)
            self.assertTrue(marks[-1]["done"])


if __name__ == "__main__": unittest.main(verbosity=2)
