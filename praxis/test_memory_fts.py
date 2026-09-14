from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import memory_fts
import memory_index
import memory_life


class MemoryFtsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        (self.memory / "people").mkdir(parents=True)
        self.skills.mkdir(parents=True)
        # Default-contract fixtures must not inherit process-wide whole-doc rollout.
        self._fts_mode_env = mock.patch.dict(
            os.environ,
            {"PRAXIS_INDEX_RUNS": "1", "PRAXIS_MEMORY_WHOLE_DOCS": ""},
        )
        self._fts_mode_env.start()
        self.addCleanup(self._fts_mode_env.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_runs_are_not_canon_by_default(self):
        """12.09 (решение Егора): прогоны не индексируются, пока PRAXIS_INDEX_RUNS не поднят.

        Положительный контроль: с рычагом тот же источник виден, без него — нет.
        """
        run_md = self.memory / "runs" / "2026-09" / "run-x" / "context.md"
        run_md.parent.mkdir(parents=True)
        run_md.write_text("# Context\n\nруноконтекст маркер\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PRAXIS_INDEX_RUNS": "1"}):
            with_runs = [s.rel for s in memory_fts.iter_sources(
                base=self.base, memory_dir=self.memory, skills_dir=self.skills)]
        env = dict(os.environ); env.pop("PRAXIS_INDEX_RUNS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(memory_fts.index_runs_enabled())
            without = [s.rel for s in memory_fts.iter_sources(
                base=self.base, memory_dir=self.memory, skills_dir=self.skills)]
        self.assertTrue(any("runs/" in r for r in with_runs))
        self.assertFalse(any("runs/" in r for r in without))

    def search(self, query: str, *, scope: str = "owner",
               purpose: str = "explicit") -> list[dict]:
        return memory_fts.search(
            query,
            base=self.base,
            memory_dir=self.memory,
            skills_dir=self.skills,
            scope=scope,
            purpose=purpose,
        )

    def test_indexes_markdown_and_jsonl_with_visibility_and_provenance(self):
        person = self.memory / "people" / "egor.md"
        person.write_text(
            "# Egor\n\n"
            "- [public] \u0441\u0435\u0432\u0435\u0440\u043d\u043e\u0435 \u0441\u0438\u044f\u043d\u0438\u0435\n"
            "- [private] \u0444\u0438\u043e\u043b\u0435\u0442\u043e\u0432\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c\n",
            encoding="utf-8",
        )
        events = self.memory / "life" / "events" / "2026-07-13.jsonl"
        events.parent.mkdir(parents=True)
        event = {
            "schema": "praxis.life.event.v1",
            "id": "evt-1",
            "ts": "2026-07-13T12:00:00Z",
            "run_id": "run-kraken",
            "text": "\u0432\u0438\u0434\u0435\u043b\u0430 \u043f\u043e\u043b\u044f\u0440\u043d\u043e\u0435 \u0441\u0438\u044f\u043d\u0438\u0435 \u043d\u0430\u0434 \u0434\u043e\u043c\u043e\u043c",
            "refs": ["telegram:message:77"],
            "supersedes": ["evt-0"],
        }
        events.write_text(
            json.dumps(event, ensure_ascii=False)
            + "\n"
            + json.dumps({**event, "text": event["text"] + " repeated"}, ensure_ascii=False)
            + "\n{broken tail\n",
            encoding="utf-8",
        )

        state = memory_fts.rebuild(
            base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        self.assertEqual(state["corrupt_lines"], 1)
        refreshed = memory_fts.upsert(
            events, base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        self.assertTrue(refreshed["indexed"])
        ensured = memory_fts.ensure(
            base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        self.assertEqual(ensured["corrupt_lines"], 1)

        public = self.search("\u0441\u0438\u044f\u043d\u0438\u0435", scope="group")
        self.assertTrue(any(row["path"].endswith("people/egor.md") for row in public))
        self.assertFalse(self.search("\u0444\u0438\u043e\u043b\u0435\u0442\u043e\u0432\u044b\u0439", scope="group"))
        self.assertTrue(self.search("\u0444\u0438\u043e\u043b\u0435\u0442\u043e\u0432\u044b\u0439", scope="owner"))

        recalled = self.search("\u043f\u043e\u043b\u044f\u0440\u043d\u043e\u0435")
        row = next(item for item in recalled if item["event_id"] == "evt-1")
        self.assertEqual(row["source_type"], "life_event")
        self.assertEqual(row["run_id"], "run-kraken")
        self.assertEqual(row["refs"], ["telegram:message:77"])
        self.assertEqual(row["supersedes"], ["evt-0"])
        self.assertEqual(row["provenance"], ["telegram:message:77", "evt-0"])

    def test_life_revisions_remove_old_text_from_recall_and_stale_compacts(self):
        for directory in (
            self.memory / "life" / "events",
            self.memory / "life" / "compacts" / "-1001",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        with mock.patch.multiple(
            memory_life,
            BASE=self.base,
            MEM_DIR=self.memory,
            LIFE_DIR=self.memory / "life",
            EVENTS_DIR=self.memory / "life" / "events",
            COMPACTS_DIR=self.memory / "life" / "compacts",
            EPISODES_DIR=self.memory / "life" / "episodes",
            CLAIMS_DIR=self.memory / "life" / "claims",
            PATCHES_DIR=self.memory / "life" / "patches",
            REFLECTIONS_DIR=self.memory / "life" / "reflections",
            STATE_DIR=self.memory / ".state" / "life",
            LEGACY_SUMMARIES_DIR=self.memory / ".summaries",
            DIALOGUES_DIR=self.memory / "dialogues",
        ):
            original = memory_life.record_message(
                "-1001", "Alice: recall obsolete platypus", actor="Alice",
                direction="in", source_id="41", ts=100.0,
                dedupe_key="telegram:-1001:41:in",
            )
            memory_life._write_compact(
                "-1001", {"summary": "recall obsolete platypus", "open_threads": [],
                          "claims": [], "episodes": []},
                tier=1, depth=1, source_events=[original["id"]], source_compacts=[],
                event_count=1, continued=False,
                first_ts=original["ts"], last_ts=original["ts"],
            )
            memory_life.record_message(
                "-1001", "Alice [edited #41]: corrected narwhal", actor="Alice",
                direction="in", source_id="41:edit:1970-01-01T00:03:20Z:abc",
                ts=200.0,
                dedupe_key="telegram:-1001:41:edit:1970-01-01T00:03:20Z:abc:in",
            )
            memory_life.note_message_revision("-1001", 41, "Alice: corrected narwhal")

        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        self.assertFalse(self.search("obsolete platypus"))
        corrected = self.search("corrected narwhal")
        self.assertTrue(corrected)
        self.assertEqual(corrected[0]["source_type"], "life_event")

    def test_automatic_cache_revalidates_an_unchanged_previous_day(self):
        with mock.patch.multiple(
            memory_life,
            BASE=self.base,
            MEM_DIR=self.memory,
            LIFE_DIR=self.memory / "life",
            EVENTS_DIR=self.memory / "life" / "events",
            COMPACTS_DIR=self.memory / "life" / "compacts",
            EPISODES_DIR=self.memory / "life" / "episodes",
            CLAIMS_DIR=self.memory / "life" / "claims",
            PATCHES_DIR=self.memory / "life" / "patches",
            REFLECTIONS_DIR=self.memory / "life" / "reflections",
            STATE_DIR=self.memory / ".state" / "life",
            LEGACY_SUMMARIES_DIR=self.memory / ".summaries",
            DIALOGUES_DIR=self.memory / "dialogues",
        ):
            memory_fts._CANON_CACHE.clear()
            memory_fts._INDEX_CACHE.clear()
            original = memory_life.record_message(
                "-1001", "Alice: crossday obsolete pangolin", actor="Alice",
                direction="in", source_id="41", ts=86_300.0,
                dedupe_key="telegram:-1001:41:in",
            )
            self.assertTrue(self.search("obsolete pangolin", purpose="automatic"))
            old_path = memory_life._event_file(86_300.0)
            old_snapshot = old_path.stat().st_mtime_ns, old_path.stat().st_size

            memory_life.record_message(
                "-1001", "Alice [edited #41]: crossday corrected otter", actor="Alice",
                direction="in", source_id="41:edit:1970-01-02T00:01:40Z:abc",
                ts=86_500.0,
                dedupe_key="telegram:-1001:41:edit:1970-01-02T00:01:40Z:abc:in",
            )
            memory_life.note_message_revision("-1001", 41, "Alice: crossday corrected otter")
            self.assertEqual(
                (old_path.stat().st_mtime_ns, old_path.stat().st_size), old_snapshot,
                "the first day's bytes must remain unchanged for this cache regression",
            )

        self.assertFalse(self.search("obsolete pangolin", purpose="automatic"))
        corrected = self.search("corrected otter", purpose="automatic")
        self.assertTrue(corrected)
        self.assertNotEqual(corrected[0]["event_id"], original["id"])

    def test_group_deletion_tombstone_removes_old_text_from_recall_index(self):
        archive = self.memory / "groups" / "room" / "archive.jsonl"
        archive.parent.mkdir(parents=True)
        message = {
            "schema": "praxis.group.message.v1", "kind": "message",
            "peer_id": "-1001", "topic_id": 77, "message_id": 41,
            "sender_id": 10, "sender_name": "Alice", "reply_to_message_id": 77,
            "timestamp": "2026-07-14T12:00:00Z", "edited_at": None,
            "text": "recall tombstone secret", "media": "", "outgoing": False,
            "topic_title": "Ideas",
        }
        deletion = {
            "schema": "praxis.group.deletion.v1", "kind": "deletion",
            "peer_id": "-1001", "topic_id": 77, "message_id": 41,
            "sender_id": 10, "sender_name": "Alice", "reply_to_message_id": 77,
            "timestamp": "2026-07-14T12:00:00Z",
            "deleted_at": "2026-07-14T12:05:00Z", "original_known": True,
            "outgoing": False, "topic_title": "Ideas",
        }
        archive.write_text(
            json.dumps(message, ensure_ascii=False) + "\n"
            + json.dumps(deletion, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)

        self.assertFalse(self.search("tombstone secret"))

    def test_generated_views_are_excluded_and_rebuild_is_logically_deterministic(self):
        canonical = self.memory / "home.md"
        canonical.write_text("# Home\n\ncanonical narwhal memory\n", encoding="utf-8")
        (self.memory / "INDEX.md").write_text(
            "# Index\n\nindexphantom must not enter recall\n", encoding="utf-8"
        )
        maps = self.memory / "maps"
        maps.mkdir()
        (maps / "PEOPLE.md").write_text(
            "# People\n\nmapphantom must not enter recall\n", encoding="utf-8"
        )
        computer = self.memory / "computer"
        (computer / "devices").mkdir(parents=True)
        (computer / "tasks").mkdir()
        (computer / "inventory" / "windows-pc").mkdir(parents=True)
        (computer / "MAP.md").write_text("# Map\n\ncomputermapphantom\n", encoding="utf-8")
        (computer / "devices" / "windows-pc.md").write_text(
            "# Device\n\ndeviceprojectionphantom\n", encoding="utf-8",
        )
        (computer / "tasks" / "task-1.md").write_text(
            "# Task\n\ntaskprojectionphantom\n", encoding="utf-8",
        )
        (computer / "inventory" / "windows-pc" / "CURRENT.md").write_text(
            "# Inventory\n\ninventoryprojectionphantom\n", encoding="utf-8",
        )
        desires = self.memory / "desires"
        desires.mkdir()
        (desires / "CURRENT.md").write_text(
            "<!-- praxis-generated: {} -->\n# Desires\n\ndesireprojectionphantom\n",
            encoding="utf-8",
        )
        (desires / "events.jsonl").write_text(
            json.dumps({
                "schema": "praxis.desire.event.v1",
                "event_id": "desire-event-1",
                "ts": "2026-07-13T12:00:00Z",
                "desire_id": "desire-kraken",
                "stage": "wanted",
                "note": "learn deliberate kraken scrolling",
                "run_id": "run-kraken",
            }) + "\n" + json.dumps({
                "schema": "praxis.desire.event.v1",
                "event_id": "desire-event-2",
                "ts": "2026-07-13T12:10:00Z",
                "desire_id": "desire-kraken",
                "stage": "chosen",
                "note": "choose deliberate kraken scrolling",
                "run_id": "run-kraken",
            }) + "\n" + json.dumps({
                "schema": "praxis.desire.event.v1",
                "event_id": "desire-event-3",
                "ts": "2026-07-13T12:20:00Z",
                "desire_id": "desire-kraken",
                "stage": "changed",
                "status": "satisfied",
                "note": "release obsolete scrolling intention",
                "run_id": "run-kraken",
            }) + "\n",
            encoding="utf-8",
        )

        first_state = memory_fts.rebuild(
            base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        first = [(row["id"], row["path"], row["text"]) for row in self.search("narwhal")]
        self.assertTrue(first)
        self.assertFalse(self.search("indexphantom"))
        self.assertFalse(self.search("mapphantom"))
        self.assertFalse(self.search("computermapphantom"))
        self.assertFalse(self.search("deviceprojectionphantom"))
        self.assertFalse(self.search("taskprojectionphantom"))
        self.assertFalse(self.search("inventoryprojectionphantom"))
        self.assertFalse(self.search("desireprojectionphantom"))
        desired = self.search("kraken scrolling")
        self.assertEqual(desired[0]["source_type"], "desire_event")
        self.assertEqual(desired[0]["run_id"], "run-kraken")
        self.assertEqual(desired[0]["desire_id"], "desire-kraken")
        self.assertEqual(sum(row["source_type"] == "desire_event" for row in desired), 1)
        automatic_desired = self.search("kraken scrolling", purpose="automatic")
        self.assertEqual(len(automatic_desired), 1)
        self.assertEqual(automatic_desired[0]["event_id"], "desire-event-3")
        self.assertIn("satisfied", automatic_desired[0]["text"])

        database = self.base / first_state["database"]
        database.unlink()
        second_state = memory_fts.rebuild(
            base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        second = [(row["id"], row["path"], row["text"]) for row in self.search("narwhal")]
        self.assertEqual(first, second)
        self.assertEqual(first_state["fingerprint"], second_state["fingerprint"])

        with mock.patch.multiple(
            memory_index,
            BASE=self.base,
            MEM_DIR=self.memory,
            SKILLS_DIR=self.skills,
            VECTORS_DIR=self.memory / ".vectors",
            INDEX_MD=self.memory / "INDEX.md",
        ):
            vector_sources = {path.resolve() for path in memory_index._iter_source_files()}
        self.assertIn(canonical.resolve(), vector_sources)
        for projection in (
            self.memory / "INDEX.md", maps / "PEOPLE.md", computer / "MAP.md",
            computer / "devices" / "windows-pc.md", computer / "tasks" / "task-1.md",
            computer / "inventory" / "windows-pc" / "CURRENT.md", desires / "CURRENT.md",
        ):
            self.assertNotIn(projection.resolve(), vector_sources)

        projection = computer / "MAP.md"
        projection_rel = projection.relative_to(self.base).as_posix()
        legacy_vector = {
            "model": "legacy",
            "files": {projection_rel: projection.stat().st_mtime},
            "records": [{
                "id": "legacy-generated-map-record", "path": projection_rel,
                "text": "computermapphantom", "vector": [1.0],
            }],
        }
        with mock.patch.multiple(
            memory_index,
            BASE=self.base,
            MEM_DIR=self.memory,
            SKILLS_DIR=self.skills,
            VECTORS_DIR=self.memory / ".vectors",
            INDEX_MD=self.memory / "INDEX.md",
        ), mock.patch.object(memory_index, "_embeddings_on", return_value=True), \
             mock.patch.object(memory_index, "_load", return_value=legacy_vector), \
             mock.patch.object(memory_index, "_save") as save:
            memory_index.upsert(projection)
        cleaned = save.call_args.args[0]
        self.assertNotIn(projection_rel, cleaned["files"])
        self.assertEqual(cleaned["records"], [])

    def test_latest_canonical_inventory_and_self_history_are_recallable(self):
        device = self.memory / "computer" / "inventory" / "windows-pc"
        device.mkdir(parents=True)
        old = {
            "schema": "praxis.computer.inventory.v1", "device_id": "windows-pc",
            "captured_at": "2099-07-12T12:00:00Z",
            "payload": {"hostname": "OLD", "apps": [{"name": "OldAppPhantom"}]},
        }
        current = {
            "schema": "praxis.computer.inventory.v1", "device_id": "windows-pc",
            "captured_at": "2026-07-13T12:00:00Z", "observed_at": "2026-07-13T12:00:01Z",
            "payload": {
                "hostname": "LOVE", "user": "yegor",
                "os": {"caption": "Windows 11 Pro", "build": "26200"},
                "apps": [{"name": "CurrentAppNarwhal", "publisher": "Praxis"}],
                "tools": [{"name": "cargo", "path": r"C:\\Rust\\cargo.exe"}],
                "project_roots": [r"C:\\Users\\yegor\\Downloads\\Praxis"],
            },
        }
        # Legacy filenames came from the Windows clock and may be skewed far
        # into the future.  observed_at on the new record must win.
        (device / "20990712T120000Z.json").write_text(json.dumps(old), encoding="utf-8")
        latest = device / "20260713T120001Z.json"
        latest.write_text(json.dumps(current), encoding="utf-8")
        (device / "CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
        (device / "CURRENT.md").write_text(
            "# Computer\n\ninventoryprojectionphantom\n", encoding="utf-8",
        )

        history = self.base / "soul" / "self" / "history"
        history.mkdir(parents=True)
        (history / "0001.md").write_text(
            "# Previous self\n\nI learned deliberate lighthouse checking.\n", encoding="utf-8",
        )

        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        app = self.search("CurrentAppNarwhal")
        self.assertEqual(app[0]["source_type"], "inventory_snapshot")
        self.assertEqual(app[0]["path"], latest.relative_to(self.base).as_posix())
        self.assertEqual(app[0]["visibility"], "owner")
        self.assertFalse(self.search("OldAppPhantom"))
        self.assertFalse(self.search("inventoryprojectionphantom"))
        self.assertEqual(self.search("lighthouse checking")[0]["source_type"], "self_history")
        self.assertFalse(self.search("CurrentAppNarwhal", scope="group"))

        with mock.patch.multiple(
            memory_index,
            BASE=self.base,
            MEM_DIR=self.memory,
            SKILLS_DIR=self.skills,
            VECTORS_DIR=self.memory / ".vectors",
            INDEX_MD=self.memory / "INDEX.md",
        ):
            vector_sources = {path.resolve() for path in memory_index._iter_source_files()}
        self.assertIn((history / "0001.md").resolve(), vector_sources)
        self.assertNotIn(latest.resolve(), vector_sources)  # canonical JSON is covered by FTS

    def test_upsert_canonical_journal_skips_discovery_but_later_ensure_discovers_roster(self):
        seed = self.memory / "people" / "seed.md"
        seed.write_text("- existing corpus fact\n", encoding="utf-8")
        initial = memory_fts.rebuild(
            base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )

        journal = self.memory / "journal" / "2026-07-15.md"
        journal.parent.mkdir()
        journal.write_text("# July 15\n\nfast journal narwhal marker\n", encoding="utf-8")
        with mock.patch.object(
            memory_fts, "iter_sources",
            side_effect=AssertionError("journal upsert must not walk the corpus"),
        ):
            result = memory_fts.upsert(
                journal, base=self.base, memory_dir=self.memory, skills_dir=self.skills
            )
        self.assertTrue(result["indexed"])
        # The immediate FTS row exists, but upsert has not pretended it knows the
        # complete corpus fingerprint.
        database = memory_fts._db_path(self.memory)
        self.assertEqual(memory_fts._meta(database)["fingerprint"], initial["fingerprint"])
        journal_hits = self.search("fast journal narwhal")
        self.assertTrue(journal_hits)
        self.assertEqual(journal_hits[0]["source_type"], "journal_episode")

        outsider = self.memory / "people" / "later.md"
        outsider.write_text("- later discovered capybara\n", encoding="utf-8")
        original = memory_fts.iter_sources
        with mock.patch.object(memory_fts, "iter_sources", wraps=original) as walked:
            discovered = memory_fts.ensure(
                base=self.base, memory_dir=self.memory, skills_dir=self.skills
            )
        walked.assert_called_once()
        self.assertGreaterEqual(discovered.get("refreshed", 0), 1)
        # Discovery reconciled the independent source and preserved the journal row,
        # both of which are available through the public search API.
        self.assertTrue(self.search("fast journal narwhal"))
        self.assertTrue(self.search("later discovered capybara"))

        journal.unlink()
        with mock.patch.object(
            memory_fts, "iter_sources",
            side_effect=AssertionError("journal deletion must not walk the corpus"),
        ):
            removed = memory_fts.upsert(
                journal, base=self.base, memory_dir=self.memory, skills_dir=self.skills
            )
        self.assertFalse(removed["indexed"])
        self.assertFalse(self.search("fast journal narwhal"))

    def test_upsert_noncanonical_journal_like_path_still_walks_sources(self):
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        journal_like = self.memory / "journal" / "2026-07-15-draft.md"
        journal_like.parent.mkdir()
        journal_like.write_text("draft journal-like ibex marker\n", encoding="utf-8")
        original = memory_fts.iter_sources
        with mock.patch.object(memory_fts, "iter_sources", wraps=original) as walked:
            result = memory_fts.upsert(
                journal_like, base=self.base, memory_dir=self.memory, skills_dir=self.skills
            )
        self.assertTrue(result["indexed"])
        walked.assert_called_once()
        self.assertTrue(self.search("journal-like ibex"))

    def test_upsert_tracks_edits_and_deletion_without_touching_source(self):
        person = self.memory / "people" / "egor.md"
        person.write_text("# Egor\n\n- [public] copper albatross\n", encoding="utf-8")
        original = person.read_bytes()
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        self.assertTrue(self.search("albatross"))
        self.assertEqual(person.read_bytes(), original)

        person.write_text("# Egor\n\n- [public] silver cuttlefish\n", encoding="utf-8")
        memory_fts.upsert(
            person, base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        self.assertFalse(self.search("albatross"))
        self.assertTrue(self.search("cuttlefish"))

        person.unlink()
        result = memory_fts.upsert(
            person, base=self.base, memory_dir=self.memory, skills_dir=self.skills
        )
        self.assertFalse(result["indexed"])
        self.assertFalse(self.search("cuttlefish"))

    def test_computer_goal_is_explicit_only_while_outcome_remains_automatic(self):
        events = self.memory / "computer" / "events" / "2026-07-15.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text(
            json.dumps({
                "id": "obs-social-pulse",
                "at": "2026-07-15T15:15:44Z",
                "task_id": "task-social-pulse",
                "goal": "hourly wake prompt recursive marker",
                "summary": "body status succeeded compact outcome marker",
                "capability": "body.status",
                "status": "succeeded",
                "refs": ["computer:receipt:social-pulse"],
            }) + "\n",
            encoding="utf-8",
        )

        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        explicit_goal = self.search("hourly wake prompt recursive", purpose="explicit")
        self.assertTrue(any("hourly wake prompt recursive marker" in row["text"]
                            for row in explicit_goal))
        self.assertFalse(self.search("hourly wake prompt recursive", purpose="automatic"))
        automatic_outcome = self.search("compact outcome marker", purpose="automatic")
        self.assertTrue(automatic_outcome)
        self.assertNotIn("hourly wake prompt recursive marker", automatic_outcome[0]["text"])

    def test_existing_memory_search_api_returns_jsonl_provenance(self):
        events = self.memory / "computer" / "events" / "2026-07-13.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text(
            json.dumps(
                {
                    "id": "obs-9",
                    "at": "2026-07-13T12:00:00Z",
                    "task_id": "task-kraken",
                    "summary": "scroll calibration completed for messenger",
                    "refs": ["computer:receipt:9"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with mock.patch.multiple(
            memory_index,
            BASE=self.base,
            MEM_DIR=self.memory,
            SKILLS_DIR=self.skills,
        ), mock.patch.dict(os.environ, {"PRAXIS_EMBEDDINGS": "0"}):
            memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
            result = memory_index.search("scroll calibration", k=3, scope="owner")
        self.assertTrue(result)
        self.assertEqual(result[0]["event_id"], "obs-9")
        self.assertEqual(result[0]["source_type"], "computer_event")
        self.assertEqual(result[0]["refs"], ["computer:receipt:9"])
        self.assertEqual(result[0]["provenance"], ["computer:receipt:9"])

    def test_explicit_ensure_reconciles_incrementally_without_full_reparse(self):
        person = self.memory / "people" / "egor.md"
        person.write_text("- любит скорость\n", encoding="utf-8")
        run_events = self.memory / "runs" / "2026-07" / "run-1" / "events.jsonl"
        run_events.parent.mkdir(parents=True)
        run_events.write_text(
            json.dumps({"kind": "run_event", "text": "first tool step"}) + "\n",
            encoding="utf-8",
        )
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        self.assertTrue(self.search("first tool"))

        with run_events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "run_event", "text": "second unheard step"}) + "\n")
        original = memory_fts._source_chunks
        parsed: list[str] = []

        def spy(source):
            parsed.append(source.rel)
            return original(source)

        # A run event append is the every-turn case: it must refresh only that
        # source in place, never re-parse the whole corpus (the old minutes-long
        # rebuild) and never touch unrelated sources.
        with mock.patch.object(
            memory_fts, "rebuild",
            side_effect=AssertionError("full rebuild on live explicit path"),
        ), mock.patch.object(memory_fts, "_source_chunks", side_effect=spy):
            rows = self.search("unheard")
        # A newly appended word cannot be discovered without scanning the corpus;
        # explicit recall returns no unverified result and nightly rebuild indexes it.
        self.assertEqual(rows, [])
        self.assertEqual(parsed, [])

    def test_explicit_ensure_drops_removed_sources_incrementally(self):
        person = self.memory / "people" / "guest.md"
        person.write_text("- transient visitor fact\n", encoding="utf-8")
        keeper = self.memory / "people" / "keeper.md"
        keeper.write_text("- keeper stays around\n", encoding="utf-8")
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        self.assertTrue(self.search("transient visitor"))

        person.unlink()
        with mock.patch.object(
            memory_fts, "rebuild",
            side_effect=AssertionError("full rebuild on live explicit path"),
        ):
            self.assertEqual(self.search("transient visitor"), [])
            self.assertTrue(self.search("keeper stays"))

    def test_life_change_rederives_all_life_event_sources(self):
        first = self.memory / "life" / "events" / "2026-07-01.jsonl"
        first.parent.mkdir(parents=True)
        first.write_text(
            json.dumps({"schema": "praxis.life.event.v1", "id": "evt-a",
                        "ts": "2026-07-01T10:00:00Z", "text": "aurora over the house"})
            + "\n", encoding="utf-8",
        )
        second = self.memory / "life" / "events" / "2026-07-02.jsonl"
        second.write_text(
            json.dumps({"schema": "praxis.life.event.v1", "id": "evt-b",
                        "ts": "2026-07-02T10:00:00Z", "text": "quiet morning walk"})
            + "\n", encoding="utf-8",
        )
        bystander = self.memory / "people" / "egor.md"
        bystander.write_text("- untouched bystander\n", encoding="utf-8")
        # The initial corpus build belongs to maintenance, not this explicit query.
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
        self.assertTrue(self.search("aurora"))

        with first.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema": "praxis.life.event.v1", "id": "evt-c",
                                 "ts": "2026-07-01T11:00:00Z",
                                 "text": "aurora faded slowly"}) + "\n")
        original = memory_fts._source_chunks
        parsed: list[str] = []

        def spy(source):
            parsed.append(source.rel)
            return original(source)

        # Eligibility of life events (and claim kinds) is derived from the
        # provenance evidence index, so touching any memory/life source must
        # re-derive every life_event source — but still not the whole corpus.
        with mock.patch.object(memory_fts, "_source_chunks", side_effect=spy):
            # The stale FTS has no matching row yet, so only the durable request
            # is observable on this hand; maintenance rederives all life files.
            self.assertEqual(self.search("faded"), [])
        self.assertEqual(parsed, [])
        # No matching cached row is itself not evidence of mismatch; writers and
        # maintenance schedule the normal refresh cadence for newly appended data.


if __name__ == "__main__":
    unittest.main()


class ExplicitSearchHotPathTests(unittest.TestCase):
    """Interactive explicit recall reads only its disposable DB and final sources."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        (self.memory / "people").mkdir(parents=True)
        self.skills.mkdir(parents=True)
        self.note = self.memory / "people" / "fast.md"
        self.note.write_text("# Fast\n\n- canonical hotpath narwhal\n", encoding="utf-8")
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _search(self, query: str) -> list[dict]:
        return memory_fts.search(query, base=self.base, memory_dir=self.memory,
                                 skills_dir=self.skills, purpose="explicit")

    def test_explicit_hot_path_never_walks_or_repairs_the_index(self) -> None:
        with mock.patch.object(memory_fts, "iter_sources",
                               side_effect=AssertionError("corpus walk")), \
                mock.patch.object(memory_fts, "ensure",
                                  side_effect=AssertionError("synchronous ensure")), \
                mock.patch.object(memory_fts, "rebuild",
                                  side_effect=AssertionError("synchronous rebuild")):
            hits = self._search("canonical hotpath")
        self.assertEqual(len(hits), 1)
        self.assertIn("canonical hotpath narwhal", hits[0]["text"])

    def test_explicit_source_matches_registry_for_underscore_markdown(self) -> None:
        cases = {
            "_internal.md": False,
            "people/_internal.md": False,
            "people/nested/_internal.md": False,
            "_private/allowed.md": True,
            "people/nested/allowed.md": True,
        }
        for relative in cases:
            path = self.memory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Note\n\ncanonical parity narwhal\n", encoding="utf-8")
        registered = {source.rel for source in memory_fts.iter_sources(
            base=self.base, memory_dir=self.memory, skills_dir=self.skills)}
        for relative, allowed in cases.items():
            with self.subTest(relative=relative):
                rel = "memory/" + relative
                source = memory_fts._explicit_source(
                    rel, base=self.base, memory_dir=self.memory, skills_dir=self.skills)
                self.assertEqual(rel in registered, allowed)
                self.assertEqual(source is not None, allowed)

    def test_poisoned_cached_locator_cannot_promote_excluded_markdown(self) -> None:
        self.assertTrue(self._search("canonical hotpath"))
        for relative in ("_internal.md", "people/_internal.md",
                         "people/nested/_internal.md"):
            with self.subTest(relative=relative):
                path = self.memory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self.note.read_text(encoding="utf-8"), encoding="utf-8")
                # Keep genuine text/chunk metadata, but redirect the disposable
                # locator (and its source label) to a maintenance-excluded file.
                with sqlite3.connect(memory_fts._db_path(self.memory)) as db:
                    db.execute("UPDATE chunks SET path = ?, source = ?, visibility = ?",
                               ("memory/" + relative, path.stem,
                                "public" if relative.startswith("people/") else "owner"))
                memory_fts.complete_refresh_request(memory_dir=self.memory)
                with mock.patch.object(memory_fts, "iter_sources",
                                       side_effect=AssertionError("corpus walk")), \
                        mock.patch.object(memory_fts, "ensure",
                                          side_effect=AssertionError("synchronous ensure")), \
                        mock.patch.object(memory_fts, "rebuild",
                                          side_effect=AssertionError("synchronous rebuild")):
                    self.assertEqual(self._search("canonical hotpath"), [])
                self.assertTrue(memory_fts.refresh_requested(memory_dir=self.memory))

    def test_tampered_cached_row_is_rejected_without_sync_repair(self) -> None:
        database = memory_fts._db_path(self.memory)
        poison = "sqlite stale poison narwhal"
        with sqlite3.connect(database) as db:
            db.execute("UPDATE chunks SET text = ?, terms = ? WHERE path = ?",
                       (poison, memory_fts._terms(poison), "memory/people/fast.md"))
            db.commit()
        with mock.patch.object(memory_fts, "iter_sources",
                               side_effect=AssertionError("corpus walk")), \
                mock.patch.object(memory_fts, "ensure",
                                  side_effect=AssertionError("synchronous ensure")), \
                mock.patch.object(memory_fts, "rebuild",
                                  side_effect=AssertionError("synchronous rebuild")):
            self.assertEqual(self._search("sqlite stale poison"), [])
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.memory))

    def test_missing_db_returns_empty_and_leaves_one_durable_refresh_request(self) -> None:
        database = memory_fts._db_path(self.memory)
        database.unlink()
        with mock.patch.object(memory_fts, "iter_sources",
                               side_effect=AssertionError("corpus walk")), \
                mock.patch.object(memory_fts, "ensure",
                                  side_effect=AssertionError("synchronous ensure")), \
                mock.patch.object(memory_fts, "rebuild",
                                  side_effect=AssertionError("synchronous rebuild")):
            self.assertEqual(self._search("canonical hotpath"), [])
            self.assertEqual(self._search("canonical hotpath"), [])
        request = memory_fts._refresh_request_path(self.memory)
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.memory))
        self.assertTrue(request.is_file(), "request must survive the interactive process")
        memory_fts.complete_refresh_request(memory_dir=self.memory)
        self.assertFalse(request.exists(), "nightly success is the acknowledgement point")

    def test_explicit_memory_index_never_uses_legacy_vectors_or_scanner_fallback(self) -> None:
        with mock.patch.object(memory_index, "_fulltext_candidates", return_value=[]), \
                mock.patch.object(memory_index, "_vector_candidates",
                                  side_effect=AssertionError("stale vector index")), \
                mock.patch.object(memory_index, "_all_chunks",
                                  side_effect=AssertionError("canonical scanner")):
            self.assertEqual(memory_index.search("canonical hotpath", semantic=False), [])

    def test_background_build_consumes_durable_refresh_only_after_fts_success(self) -> None:
        memory_fts.request_refresh(memory_dir=self.memory, reason="test")
        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory,
                                 SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild", wraps=memory_fts.rebuild) as rebuild:
            memory_index.build()
        self.assertTrue(rebuild.called)
        self.assertFalse(memory_fts.refresh_requested(memory_dir=self.memory))

    def test_refresh_request_has_one_cross_process_owner(self) -> None:
        ctx = multiprocessing.get_context("fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn")
        gate, output = ctx.Event(), ctx.Queue()
        def worker():
            gate.wait(3); output.put(memory_fts.request_refresh(memory_dir=self.memory, reason="race"))
        children = [ctx.Process(target=worker) for _ in range(2)]
        for child in children: child.start()
        gate.set()
        owners = [output.get(timeout=3) for _ in children]
        for child in children: child.join(3)
        self.assertEqual(owners.count(True), 1)

    def test_new_request_during_rebuild_survives_prior_ack(self) -> None:
        memory_fts.request_refresh(memory_dir=self.memory, reason="old")
        def rebuild(**kwargs):
            self.assertTrue(memory_fts.request_refresh(memory_dir=self.memory, reason="new"))
            return {}
        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory, SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild", side_effect=rebuild):
            memory_index.build()
        marker = memory_fts._refresh_request_path(self.memory)
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(marker.read_text())["reason"], "new")

    def test_concurrent_builds_only_one_claimed_refresh_rebuild(self) -> None:
        import threading
        memory_fts.request_refresh(memory_dir=self.memory, reason="race")
        entered, release = threading.Event(), threading.Event()
        calls: list[str] = []

        def rebuild(**kwargs):
            calls.append(threading.current_thread().name)
            entered.set()
            self.assertTrue(release.wait(2), "owner rebuild was not released")
            return {}

        def owner():
            memory_index.build()

        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory, SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild", side_effect=rebuild):
            first = threading.Thread(target=owner, name="claim-owner")
            first.start()
            self.assertTrue(entered.wait(1), "owner never entered rebuild")
            # The first builder has atomically moved the request into its private
            # claim; this call must not start a second request-driven rebuild.
            memory_index.build()
            release.set()
            first.join(2)
        self.assertFalse(first.is_alive())
        self.assertEqual(calls, ["claim-owner"])
        self.assertFalse(memory_fts.refresh_requested(memory_dir=self.memory))

    def test_failed_claim_owner_is_recoverable_and_later_acknowledged(self) -> None:
        memory_fts.request_refresh(memory_dir=self.memory, reason="retry")
        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory, SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild", side_effect=RuntimeError("broken")):
            memory_index.build()
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.memory),
                        "failed owner must restore its request")
        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory, SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild", return_value={}) as rebuild:
            memory_index.build()
        self.assertEqual(rebuild.call_count, 1)
        self.assertFalse(memory_fts.refresh_requested(memory_dir=self.memory),
                         "later successful owner must acknowledge retry")

    def test_locked_db_is_prompt_and_never_repairs(self) -> None:
        db = sqlite3.connect(memory_fts._db_path(self.memory), timeout=0)
        db.execute("BEGIN EXCLUSIVE")
        try:
            with mock.patch.object(memory_fts, "iter_sources", side_effect=AssertionError), \
                    mock.patch.object(memory_fts, "ensure", side_effect=AssertionError), \
                    mock.patch.object(memory_fts, "rebuild", side_effect=AssertionError):
                start = time.monotonic()
                self.assertEqual(self._search("canonical hotpath"), [])
                elapsed = time.monotonic() - start
        finally:
            db.rollback(); db.close()
        self.assertLess(elapsed, .5)
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.memory))

def _maintenance_process(base, entered, release, output):
    """Spawn-safe probe: events bracket the expensive operation, not sleeps."""
    base = Path(base)
    def rebuild(**kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test did not release builder")
        return {"probe": True}
    with mock.patch.multiple(memory_index, BASE=base, MEM_DIR=base / "memory",
                             SKILLS_DIR=base / "soul" / "skills"), \
            mock.patch.object(memory_index, "_embeddings_on", return_value=False), \
            mock.patch.object(memory_fts, "rebuild", side_effect=rebuild):
        output.put(memory_index.build())


class RefreshClaimRecoveryTests(unittest.TestCase):
    """Crash recovery is process-independent and does not consult PID liveness."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        self.skills.mkdir(parents=True)
        (self.memory / "people").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"PRAXIS_MEMORY_WHOLE_DOCS": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_expired_orphan_claim_is_recovered_and_rebuilt(self) -> None:
        state = self.memory / ".state"
        state.mkdir()
        orphan = state / "recall_refresh.claim.999999.dead.json"
        orphan.write_text(json.dumps({
            "schema": "praxis.recall-refresh.v1", "token": "orphan",
            "owner": {"pid": os.getpid()},  # deliberately unreliable/reused
            "claimed_at": 1, "lease_expires_at": 2,
        }), encoding="utf-8")
        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory,
                                 SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild", return_value={}) as rebuild:
            memory_index.build()
        self.assertEqual(rebuild.call_count, 1)
        self.assertFalse(orphan.exists())
        self.assertFalse(memory_fts.refresh_requested(memory_dir=self.memory))

    def test_expired_but_locked_claim_is_not_stolen(self) -> None:
        self.assertTrue(memory_fts.request_refresh(memory_dir=self.memory))
        claim = memory_fts.claim_refresh_request(memory_dir=self.memory)
        self.assertIsNotNone(claim)
        data = json.loads(claim.read_text(encoding="utf-8"))
        data["lease_expires_at"] = 0
        claim.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory,
                                 SKILLS_DIR=self.skills), \
                mock.patch.object(memory_fts, "rebuild") as rebuild:
            memory_index.build()
        self.assertFalse(rebuild.called)
        self.assertTrue(claim.exists())
        memory_fts.complete_refresh_request(memory_dir=self.memory, claim=claim)

    def test_process_builders_serialize_new_requests_and_scheduled_passes(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        for requested in (True, False):
            with self.subTest(requested=requested):
                if requested:
                    self.assertTrue(memory_fts.request_refresh(memory_dir=self.memory, reason="A"))
                entered, release, output = ctx.Event(), ctx.Event(), ctx.Queue()
                first = ctx.Process(target=_maintenance_process,
                                    args=(str(self.base), entered, release, output))
                first.start()
                try:
                    self.assertTrue(entered.wait(10))
                    # First test competes for a new marker B, second competes
                    # with a purely scheduled no-pending build.
                    if requested:
                        self.assertTrue(memory_fts.request_refresh(memory_dir=self.memory, reason="B"))
                        before = memory_fts._refresh_request_path(self.memory).read_bytes()
                    competing_entered = ctx.Event()
                    competing_release = ctx.Event()
                    competing_release.set()
                    second = ctx.Process(target=_maintenance_process,
                                         args=(str(self.base), competing_entered,
                                               competing_release, output))
                    second.start()
                    second.join(10)
                    if second.is_alive():
                        second.terminate(); second.join()
                        self.fail("competing maintenance blocked instead of coalescing")
                    self.assertEqual(second.exitcode, 0)
                    self.assertFalse(competing_entered.is_set(), "concurrent corpus build")
                    self.assertEqual(output.get(timeout=3)["fts"], {})
                finally:
                    release.set()
                    first.join(10)
                    if first.is_alive():
                        first.terminate(); first.join()
                self.assertEqual(first.exitcode, 0)
                self.assertEqual(output.get(timeout=3)["fts"], {"probe": True})
                if requested:
                    self.assertEqual(memory_fts._refresh_request_path(self.memory).read_bytes(), before)
                    with mock.patch.multiple(memory_index, BASE=self.base, MEM_DIR=self.memory,
                                             SKILLS_DIR=self.skills), \
                            mock.patch.object(memory_fts, "rebuild", return_value={}) as rebuilt:
                        memory_index.build()
                    rebuilt.assert_called_once()
                    self.assertFalse(memory_fts.refresh_requested(memory_dir=self.memory))
                output.close()
                output.join_thread()

    def test_restore_link_failure_releases_ownership_and_recovers(self) -> None:
        self.assertTrue(memory_fts.request_refresh(memory_dir=self.memory, reason="retry"))
        claim = memory_fts.claim_refresh_request(memory_dir=self.memory)
        self.assertIsNotNone(claim)
        with mock.patch.object(memory_fts.os, "link", side_effect=OSError("temporary I/O failure")):
            memory_fts.restore_refresh_request(memory_dir=self.memory, claim=claim)
        self.assertTrue(claim.exists(), "failed link must preserve durable evidence")
        self.assertNotIn(str(claim), memory_fts._CLAIM_FDS)
        self.assertNotIn(str(claim), memory_fts._CLAIM_BUILDER_FDS)
        # Expire lease and recover in a separate interpreter while the original
        # process remains alive: a leaked descriptor would prevent this pass.
        data = json.loads(claim.read_text())
        data["lease_expires_at"] = 1
        claim.write_text(json.dumps(data))
        ctx = multiprocessing.get_context("spawn")
        entered, release, output = ctx.Event(), ctx.Event(), ctx.Queue()
        release.set()
        child = ctx.Process(target=_maintenance_process,
                            args=(str(self.base), entered, release, output))
        child.start(); child.join(10)
        if child.is_alive():
            child.terminate(); child.join()
            self.fail("recovery process blocked")
        self.assertEqual(child.exitcode, 0)
        self.assertTrue(entered.is_set())
        self.assertEqual(output.get(timeout=3)["fts"], {"probe": True})
        self.assertFalse(memory_fts.refresh_requested(memory_dir=self.memory))
        self.assertFalse(claim.exists())
        output.close(); output.join_thread()
