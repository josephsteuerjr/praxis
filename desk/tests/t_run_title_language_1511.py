"""Actual on-disk trigger projection; no agent/network/process execution."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deskd import readers


class TitleRecipes(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, HELENE_TREE=str(self.root))
        env.start(); self.addCleanup(env.stop)
        self.rid = "run-20261115T100000000000Z-abcdef12"
        self.path = self.root / "memory/runs/2026-11" / self.rid
        self.path.mkdir(parents=True)
        self.context = {"kind": "chat_turn", "origin_chat_id": "123",
                        "origin_message_ids": [9], "principal_id": "42", "goal": "OLD HEAD"}
        self.manifest = {"context": self.context, "status": "running",
                         "created_at": "2026-11-15T10:00:00Z"}

    def write(self, text="Actual trigger. Later sentence.", author="Authored 作者 中文"):
        authority = {"schema": "praxis.run.authority.v2", "origin_chat_id": "123",
                     "origin_message_ids": [9], "origin_text": text}
        (self.path / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        fence = chr(96) * 3
        (self.path / "context.md").write_text("# Immutable run context\n\n## Authority and address\n\n"
            + fence + "json\n" + json.dumps(authority) + "\n" + fence + "\n\n## Goal\nOLD HEAD", encoding="utf-8")
        turns = self.root / "memory/.state/turns.jsonl"
        turns.parent.mkdir(parents=True, exist_ok=True)
        turns.write_text(json.dumps({"run_id": self.rid, "who": author,
            "in": "WRONG conversation head", "out": "RESPONSE is not the trigger"}), encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def test_disk_projection_preserves_trigger_author_and_origin(self):
        self.write(); before = self.snapshot()
        row = readers.list_runs(with_titles=True, audience="work")[0]
        recipe = row["display_title"]
        self.assertEqual(recipe["author"], "Authored 作者 中文")
        self.assertEqual(recipe["text"], "Actual trigger.")
        self.assertNotIn("RESPONSE", json.dumps(recipe))
        self.assertEqual(readers.run_detail(self.rid)["origin"],
                         {"text": "Actual trigger. Later sentence.", "source": "snapshot"})
        self.assertEqual(before, self.snapshot())

    def test_unavailable_snapshot_is_explicit_without_reading_old_head(self):
        self.write(); (self.path / "context.md").unlink()
        row = readers.list_runs(with_titles=True)[0]
        self.assertEqual(row["display_title"]["kind"], "origin_unavailable")
        self.assertEqual(row["display_title"]["message_ids"], ["9"])
        origin = readers.run_detail(self.rid)["origin"]
        self.assertEqual(origin["display_title"], row["display_title"])
        self.assertNotIn("OLD HEAD", json.dumps(origin))

    def test_author_equal_to_product_caption_is_still_authored(self):
        self.write(author="Пробуждение")
        self.assertEqual(readers.list_runs(with_titles=True)[0]["display_title"]["author"], "Пробуждение")

    def test_unicode_title_payload_does_not_expose_beyond_legacy_cut(self):
        for author in ("名" * 160, "A😀" * 80, "Author"):
            with self.subTest(author=author[:8]):
                self.write(text="😀" * 300 + " SECRET_AFTER_CUT", author=author)
                legacy, recipe = readers._run_title_projection(self.path, self.manifest)
                self.assertLessEqual(len(legacy), 140)
                self.assertLessEqual(len(recipe["author"]) + len(recipe["text"]), 140)
                self.assertTrue(recipe["truncated"])
                self.assertNotIn("SECRET", json.dumps(recipe))

    def test_fallback_author_kind_is_separate_from_literal_ids(self):
        self.write(author="")
        with patch.object(readers, "_room_titles", return_value={}):
            recipe = readers.list_runs(with_titles=True)[0]["display_title"]
        self.assertEqual((recipe["author_kind"], recipe["author"]), ("participant", "42"))

    def test_operator_history_does_not_enter_work_audience(self):
        self.context["principal_id"] = "operator:Пробуждение"; self.write()
        self.assertEqual(readers.list_runs(audience="work"), [])
        row = readers.list_runs(audience="operator", with_titles=True)[0]
        self.assertEqual(row["display_title"]["name"], "Пробуждение")
        self.assertTrue(row["operator"])

    def test_untitled_listing_does_not_read_context(self):
        self.write()
        with patch.object(readers, "_run_title_projection", side_effect=AssertionError("unexpected title read")):
            row = readers.list_runs(with_titles=False)[0]
        self.assertEqual(row["goal_head"], "")
        self.assertIsNone(row["display_title"])

    def test_one_projection_per_list_item(self):
        self.write()
        with patch.object(readers, "_run_origin", wraps=readers._run_origin) as origin:
            readers.list_runs(with_titles=True)
        self.assertEqual(origin.call_count, 1)

    def test_event_kind_and_task_authored_goal_stay_independent(self):
        self.context.update(kind="task_window", goal="Пробуждение"); self.write()
        row = readers.list_runs(with_titles=True)[0]
        self.assertEqual(row["display_title"], {"schema": "helene.run-title.v1", "kind": "event", "event": "task_window"})
        (self.path / "context.md").unlink()
        self.assertEqual(readers.run_detail(self.rid)["origin"], {"text": "Пробуждение", "source": "task_goal"})
