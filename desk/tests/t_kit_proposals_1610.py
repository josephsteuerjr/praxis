# -*- coding: utf-8 -*-
"""Distribution proposals are visible to an owner turn, never installed soul."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))
import boot  # noqa: E402
import kit_proposals  # noqa: E402
import runner  # noqa: E402

CFG = {"agent": {"name": "Тест"}, "owner": {"name": "Егор"}}


def candidates(tree: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in (tree / "memory/.state/kit-proposals").glob("*.json")]


class DistributionBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kit-review-")
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name) / "data"
        # Git initialization is orthogonal to the Markdown boundary. Keep a
        # test-only empty process seam so the test never starts a child process.
        self.git = patch.object(boot, "seed_git", return_value=False)
        self.git.start()
        self.addCleanup(self.git.stop)

    def test_fresh_birth_and_replay_preserve_the_soul(self):
        with patch.object(boot, "_import_self_model", return_value=None):
            boot.ensure_layout(self.tree, CFG)
        self.assertEqual(json.loads((self.tree / "memory/.state/kit-birth.json")
                                    .read_text(encoding="utf-8"))["state"], "complete")
        soul = self.tree / "soul/SOUL.md"
        self.assertEqual(soul.read_text(encoding="utf-8"), boot.soul_text(CFG))
        self.assertTrue((self.tree / "soul/skills/INDEX.md").is_file())
        # Force the missing-core branch even if another test already imported it;
        # t_seed_self.py checks the real consumer and provenance.
        soul.write_text("# Моя правка\n", encoding="utf-8")
        boot.ensure_layout(self.tree, CFG)
        self.assertEqual(soul.read_text(encoding="utf-8"), "# Моя правка\n")
        self.assertTrue(any(r["target"] == "soul/SOUL.md" for r in candidates(self.tree)))
        self.assertTrue(any(r["target"] == "soul/self/CURRENT.md" for r in candidates(self.tree)),
                        "missing CURRENT after a core-less birth must remain a visible candidate")
        before = len(candidates(self.tree))
        boot.ensure_layout(self.tree, CFG)
        self.assertEqual(len(candidates(self.tree)), before)

    def test_nonempty_legacy_and_missing_soul_do_not_rebirth(self):
        (self.tree / "memory").mkdir(parents=True)
        (self.tree / "memory/INDEX.md").write_text("# lived\n", encoding="utf-8")
        boot.ensure_layout(self.tree, CFG)
        self.assertFalse((self.tree / "soul/SOUL.md").exists())
        self.assertFalse((self.tree / "soul/skills/INDEX.md").exists())
        got = candidates(self.tree)
        self.assertTrue(any(row["target"] == "soul/SOUL.md" and
                            row["candidate"] == boot.soul_text(CFG) for row in got))
        count = len(got)
        boot.ensure_layout(self.tree, CFG)
        self.assertEqual(len(candidates(self.tree)), count)
        self.assertFalse((self.tree / "soul/SOUL.md").exists())
        self.assertIn("НЕ применённые", kit_proposals.pending_line(self.tree))

    def test_agent_specific_birth_skill_becomes_postbirth_proposal(self):
        own = self.tree / "soul/skills/verify-and-report.md"
        own.parent.mkdir(parents=True)
        own.write_text("# My own skill\n", encoding="utf-8")
        boot.ensure_layout(self.tree, CFG, skills_seed={own.name: "# New kit skill\n"})
        self.assertEqual(own.read_text(encoding="utf-8"), "# My own skill\n")
        rows = [r for r in candidates(self.tree) if r["target"] == "soul/skills/verify-and-report.md"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate"], "# New kit skill\n")
        self.assertEqual(rows[0]["source"], "kit:agent-seed")

    def test_errored_or_partial_birth_cannot_be_read_as_fresh_again(self):
        with patch.object(boot, "_seed_kit", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                boot.ensure_layout(self.tree, CFG)
        with self.assertRaises(boot.LayoutError):
            boot.ensure_layout(self.tree, CFG)
        self.assertEqual(json.loads((self.tree / "memory/.state/kit-birth.json")
                                    .read_text(encoding="utf-8"))["state"], "started")

    def test_second_start_in_the_marker_parent_gap_cannot_write(self):
        entered, release = threading.Event(), threading.Event()
        real_open = os.open
        faults = []

        def held_open(path, flags, mode=0o777):
            if str(path).endswith("kit-birth.json") and threading.current_thread().name == "first-birth":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test failed to release birth writer")
            return real_open(path, flags, mode)

        def first():
            try:
                boot.ensure_layout(self.tree, CFG)
            except Exception as exc:
                faults.append(exc)

        with patch.object(boot.os, "open", side_effect=held_open):
            worker = threading.Thread(target=first, name="first-birth")
            worker.start()
            try:
                self.assertTrue(entered.wait(5), "first writer never reached marker publication")
                with self.assertRaises(boot.LayoutError):
                    boot.ensure_layout(self.tree, CFG)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(faults, [])
        self.assertEqual((self.tree / "soul/SOUL.md").read_text(encoding="utf-8"),
                         boot.soul_text(CFG))

    def test_prepared_install_receipt_survives_token_prewrite_and_data_loss(self):
        config = Path(self.tmp.name) / "kit-birth.json"
        config.write_text(json.dumps({"schema": 1, "state": "prepared",
                                      "tree": str(self.tree.resolve()), "source": "setup"}),
                          encoding="utf-8")
        token = self.tree / "memory/.state/desk-token"
        token.parent.mkdir(parents=True)
        token.write_text("preboot-token", encoding="ascii")
        with patch.object(boot, "_import_self_model", return_value=None):
            boot.ensure_layout(self.tree, CFG, birth_receipt=config)
        self.assertEqual((self.tree / "soul/SOUL.md").read_text(encoding="utf-8"),
                         boot.soul_text(CFG))
        first = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(first["state"], "partial", "missing self_model and Git are not a complete birth")
        self.assertIn("soul/self/CURRENT.md", first["gaps"])
        self.assertIn("частичным", kit_proposals.pending_line(self.tree, config).lower())
        self.assertTrue((config.with_name("kit-birth.json.claim")).is_file())
        # Simulate a lost data tree without deleting evidence: move it aside.
        rescue = Path(self.tmp.name) / "preserved-data"
        self.tree.rename(rescue)
        token.parent.mkdir(parents=True)
        token.write_text("new-token", encoding="ascii")
        boot.ensure_layout(self.tree, CFG, birth_receipt=config)
        self.assertFalse((self.tree / "soul/SOUL.md").exists())
        self.assertTrue(any(r["target"] == "soul/SOUL.md" for r in kit_proposals.active(self.tree)))

    def test_unknown_or_started_receipt_never_authorizes_seed(self):
        config = Path(self.tmp.name) / "kit-birth.json"
        boot.ensure_layout(self.tree, CFG, birth_receipt=config)
        self.assertFalse((self.tree / "soul/SOUL.md").exists())
        config.write_text(json.dumps({"schema": 1, "state": "prepared",
                                      "tree": str((Path(self.tmp.name) / "other").resolve())}),
                          encoding="utf-8")
        with self.assertRaises(boot.LayoutError):
            boot.ensure_layout(self.tree, CFG, birth_receipt=config)
        config.write_text(json.dumps({"schema": 1, "state": "started",
                                      "tree": str(self.tree.resolve())}), encoding="utf-8")
        with self.assertRaises(boot.LayoutError):
            boot.ensure_layout(self.tree, CFG, birth_receipt=config)

    def test_unretained_stock_failure_never_marks_a_birth_complete(self):
        receipt = Path(self.tmp.name) / "kit-birth.json"
        receipt.write_text(json.dumps({"schema": 1, "state": "prepared",
                                       "tree": str(self.tree.resolve()), "source": "setup"}),
                           encoding="utf-8")
        original = Path.read_bytes
        voice_source = boot._RESOURCES / "soul/VOICE.md"

        def unavailable(path):
            if path == voice_source:
                raise OSError("kit source temporarily unreadable")
            return original(path)

        with patch.object(Path, "read_bytes", unavailable), self.assertRaises(boot.LayoutError):
            boot.ensure_layout(self.tree, CFG, birth_receipt=receipt)
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["state"], "started")
        self.assertFalse((self.tree / "soul/VOICE.md").exists())

    def test_candidate_uses_exact_current_digest_and_refuses_symlink(self):
        home = self.tree / "soul"
        home.mkdir(parents=True)
        target = home / "VOICE.md"
        target.write_text("# Original\n", encoding="utf-8")
        name = kit_proposals.stage(self.tree, "soul/VOICE.md", b"# New\n", source="kit")
        self.assertEqual(name, kit_proposals.stage(self.tree, "soul/VOICE.md", b"# New\n", source="kit"))
        self.assertEqual(len(candidates(self.tree)), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "# Original\n")
        target.unlink()
        target.symlink_to(home / "absent.md")
        with self.assertRaises(OSError):
            kit_proposals.stage(self.tree, "soul/VOICE.md", b"# Other\n", source="kit")

    def test_private_preimage_store_refuses_link_to_outside_tree(self):
        voice = self.tree / "soul/VOICE.md"
        voice.parent.mkdir(parents=True)
        voice.write_text("# My private old text\n", encoding="utf-8")
        state = self.tree / "memory/.state"
        state.mkdir(parents=True)
        foreign = Path(self.tmp.name) / "foreign"
        foreign.mkdir()
        try:
            (state / "kit-proposals").symlink_to(foreign, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"filesystem cannot create directory symlink: {exc}")
        with self.assertRaises(OSError):
            kit_proposals.stage(self.tree, "soul/VOICE.md", b"# New\n", source="kit")
        self.assertEqual(list(foreign.iterdir()), [])

    def test_agent_reads_then_rejects_and_repeat_boot_does_not_offer_it(self):
        target = self.tree / "soul/skills/new.md"
        name = kit_proposals.stage(self.tree, "soul/skills/new.md", b"# Offered\n", source="kit")
        kit_proposals.publish_active(self.tree, {name})
        self.assertEqual(kit_proposals.review(self.tree, "read", name)["candidate"], "# Offered\n")
        refused = kit_proposals.review(self.tree, "reject", name, "not my voice")
        self.assertEqual(refused["decision"], "reject")
        self.assertEqual(kit_proposals.active(self.tree), [])
        self.assertFalse(target.exists())
        self.assertEqual(name, kit_proposals.stage(self.tree, "soul/skills/new.md",
                                                   b"# Offered\n", source="kit"))
        kit_proposals.publish_active(self.tree, {name})
        self.assertEqual(kit_proposals.active(self.tree), [])
        with self.assertRaises(ValueError):
            kit_proposals.review(self.tree, "accept", name, "changed my mind")

    def test_agent_accepts_only_an_effective_revision_and_keeps_preimage(self):
        target = self.tree / "soul/VOICE.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Earlier\n", encoding="utf-8")
        name = kit_proposals.stage(self.tree, "soul/VOICE.md", b"# Later\n", source="kit")
        kit_proposals.publish_active(self.tree, {name})
        with self.assertRaises(ValueError):
            kit_proposals.review(self.tree, "accept", name, "unread")
        compared = kit_proposals.review(self.tree, "read", name)
        self.assertEqual(compared["previous"].encode("utf-8"), target.read_bytes())
        with self.assertRaises(ValueError):
            kit_proposals.review(self.tree, "accept", name, "not yet applied")
        target.write_text("# Mine instead\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            kit_proposals.review(self.tree, "accept", name, "different revision")
        target.write_text("# Later\n", encoding="utf-8")
        received = kit_proposals.review(self.tree, "accept", name, "I chose the text")
        self.assertEqual(received["observed_sha256"], kit_proposals._sha(target.read_bytes()))
        self.assertEqual(received["exact_bytes"], target.read_bytes() == b"# Later\n")
        self.assertEqual(kit_proposals.active(self.tree), [])
        self.assertIsNone(kit_proposals.stage(self.tree, "soul/VOICE.md",
                                               b"# Later\n", source="kit"),
                          "newline normalization must not re-offer an accepted change")
        self.assertEqual(kit_proposals.review(self.tree, "read", name)["decision"], received)

    def test_review_tool_installs_only_with_runtime_scope_guard(self):
        name = kit_proposals.stage(self.tree, "soul/skills/new.md", b"# A\n", source="kit")
        kit_proposals.publish_active(self.tree, {name})
        holder = types.SimpleNamespace(TOOL_IMPL={}, OWNER_TOOLS=[], PRAXIS_SELF_TOOLS=[],
                                       _active_scope=lambda: "group")
        kit_proposals.install(holder, self.tree)
        kit_proposals.install(holder, self.tree)
        self.assertEqual(sum(t["name"] == "review_kit" for t in holder.OWNER_TOOLS), 1)
        self.assertIn("Не здесь", holder.TOOL_IMPL["review_kit"]("read", name))
        holder._active_scope = lambda: "owner"
        opened = json.loads(holder.TOOL_IMPL["review_kit"]("read", name))
        self.assertEqual(opened["candidate"], "# A\n")
        self.assertEqual(json.loads(holder.TOOL_IMPL["review_kit"](
            "reject", name, "not now"))["decision"], "reject")

    @unittest.skipUnless(sys.version_info >= (3, 12), "agent workshop uses Python 3.12+ f-string grammar")
    def test_index_candidate_can_use_existing_agent_fs_write_cas(self):
        sys.path.insert(0, os.environ.get('HELENE_TREE_SRC',str(DESK.parent/'helene/core')))
        import workshop
        idx = self.tree / "soul/skills/INDEX.md"
        idx.parent.mkdir(parents=True)
        idx.write_text("# My index\n", encoding="utf-8")
        proposed = "# My revised index\n"
        name = kit_proposals.stage(self.tree, "soul/skills/INDEX.md",
                                   proposed.encode("utf-8"), source="kit")
        kit_proposals.publish_active(self.tree, {name})
        shown = kit_proposals.review(self.tree, "read", name)
        with patch.object(workshop, "BASE", self.tree):
            own_write = workshop.fs_write_result("soul/skills/INDEX.md", proposed,
                                                  overwrite=True,
                                                  expected_sha256=shown["current_sha256"])
        self.assertEqual(own_write["status"], "confirmed", own_write)
        receipt = kit_proposals.review(self.tree, "accept", name, "my own index")
        self.assertEqual(receipt["decision"], "accept")
        self.assertEqual(kit_proposals.active(self.tree), [])

    def test_private_owner_turn_sees_pointer_group_owner_does_not(self):
        (self.tree / "memory").mkdir(parents=True)
        (self.tree / "memory/INDEX.md").write_text("lived\n", encoding="utf-8")
        boot.ensure_layout(self.tree, CFG)
        self.assertTrue(kit_proposals.active(self.tree))
        capture = Mock(return_value=types.SimpleNamespace(text=""))
        agent = types.SimpleNamespace(voice_turn_envelope=capture)
        with patch.object(runner, "_tree", self.tree), patch.object(runner, "_agent", agent), \
             patch.object(runner, "_dialogue", return_value=([], "hello")), \
             patch.object(runner, "_orient", return_value="context"), \
             patch.object(runner, "_ext_hook"):
            runner._run_turn("-100", "hello", "Owner",
                             types.SimpleNamespace(owner=True, is_dm=False))
            self.assertNotIn("kit-proposals", capture.call_args.kwargs["orient"])
            runner._run_turn("window", "hello", "Owner",
                             types.SimpleNamespace(owner=True, is_dm=True))
            self.assertIn("kit-proposals", capture.call_args.kwargs["orient"])
            self.assertNotIn(str(self.tree), capture.call_args.kwargs["orient"],
                             "private path is not needed in the model frame")

    def test_runner_claims_the_tree_before_any_layout_writes(self):
        source = Path(runner.__file__).read_text(encoding="utf-8")
        main = source[source.index("def main() -> None:"):]
        self.assertLess(main.index("busy = boot.claim_tree(tree,"),
                        main.index("boot.ensure_layout(tree,"),
                        "a losing runner must not create a birth/proposal record")


if __name__ == "__main__":
    unittest.main(verbosity=2)
