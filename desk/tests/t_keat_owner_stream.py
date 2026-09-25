# -*- coding: utf-8 -*-
"""КЕАТ личного потока владельца — швы харнесса (1.0.0, порт её прода).

Проверяется то, что делает издание, а не контракт дерева (он гейтится в дереве:
`test_keat_*`, `test_keat_receipts_2609`):
  * `boot.keat_knobs` — политика захвата файлом, канонические пары, пути абсолютные,
    выключатель `keat.enabled: false`, ручки `env` из helene.json сильнее;
  * `botapi.keat_capture` / `Rooms.record` — расписка ОБЕИМ сторонам лички, та же строка,
    что уходит в память; без захвата запись в память прежняя байт в байт;
  * `runner._dialogue` — сайдкар только если захвачена ВСЯ лента (иначе пусто, история цела);
  * `runner._run_turn` — сайдкар едет в дерево только личному потоку владельца;
  * `boot.prune_keat_epochs` — ротация эпох целиком, свежие не трогаются.

Запуск:  python tests/t_keat_owner_stream.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "localharness")]
import boot  # noqa: E402
import botapi  # noqa: E402
import runner  # noqa: E402

OWNER = "4242"


class Knobs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name)

    def test_no_owner_no_keat(self):
        self.assertEqual(boot.keat_knobs(self.tree, {}), {})
        self.assertEqual(boot.keat_knobs(self.tree, {"telegram": {"owner_id": "@egor"}}), {})
        self.assertEqual(boot.keat_knobs(self.tree, {"telegram": {"owner_id": "0"}}), {})
        self.assertFalse((self.tree / "memory").exists(), "без владельца файлов не пишем")

    def test_owner_gets_policy_and_canonical_pairs(self):
        knobs = boot.keat_knobs(self.tree, {"telegram": {"owner_id": f" {OWNER} "}})
        self.assertEqual(knobs["PRAXIS_KEAT"], "serve")
        self.assertEqual(knobs["PRAXIS_KEAT_CAPTURE"], "on")
        self.assertEqual(knobs["PRAXIS_KEAT_PAIRS"], '[["owner","4242"]]')
        root, policy_path = Path(knobs["PRAXIS_KEAT_ROOT"]), Path(knobs["PRAXIS_KEAT_CAPTURE_POLICY"])
        self.assertTrue(root.is_absolute() and policy_path.is_absolute())
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(policy["schema"], "keat.capture-policy.v1")
        self.assertEqual(Path(policy["root"]), root)
        row = policy["enrollments"][0]
        self.assertEqual((row["stream"], row["mode"]), (OWNER, "owner"))
        self.assertEqual(set(row["capture"]), {"issuer", "policy_revision", "audience",
                                               "transfer", "presence_hidden"})
        # Повтор не переписывает файл: байты политики сверяются на каждом захвате.
        before = policy_path.stat().st_mtime_ns
        time.sleep(0.02)
        boot.keat_knobs(self.tree, {"telegram": {"owner_id": OWNER}})
        self.assertEqual(policy_path.stat().st_mtime_ns, before)

    def test_switch_off_and_env_wins(self):
        cfg = {"telegram": {"owner_id": OWNER}, "keat": {"enabled": False}}
        self.assertEqual(boot.keat_knobs(self.tree, cfg), {})
        # Ревью 26.09 (W3): и короткая форма, и строка — тоже «выключено».
        for off in (False, "off", {"enabled": "false"}, {"enabled": 0}):
            cfg = {"telegram": {"owner_id": OWNER}, "keat": off}
            self.assertEqual(boot.keat_knobs(self.tree, cfg), {}, off)
        cfg = {"telegram": {"owner_id": OWNER}, "env": {"PRAXIS_KEAT": "off"}}
        knobs = boot.env_knobs(cfg, tree=self.tree)
        self.assertEqual(knobs["PRAXIS_KEAT"], "off")
        self.assertEqual(knobs["PRAXIS_KEAT_CAPTURE"], "on")
        # Без дерева (снимок ручек для анатомии без пути) КЕАТ не выдумывается.
        self.assertNotIn("PRAXIS_KEAT", boot.env_knobs({"telegram": {"owner_id": OWNER}}))

    def test_frame_v6_is_not_raised_by_default(self):
        knobs = boot.env_knobs({"telegram": {"owner_id": OWNER}}, tree=self.tree)
        self.assertNotIn("PRAXIS_FRAME_V6", knobs)
        self.assertNotIn("PRAXIS_OWNER_ID", knobs)
        self.assertEqual(knobs.get("PRAXIS_FRAME_HEAD_STABLE"), "1")


class Capture(unittest.TestCase):
    def setUp(self):
        self.calls = []
        fake = types.ModuleType("keat_live")

        def capture_ingress(chat_id, *, is_dm, payload, source_id, direction, **kw):
            self.calls.append((chat_id, is_dm, source_id, direction, payload))
            return {"namespace": "n", "event": f"native:{source_id}:{direction}",
                    "key": f"telegram:{chat_id}:message:{source_id}:{direction}",
                    "payload_digest": "0" * 64}

        fake.capture_ingress = capture_ingress
        self.mods = mock.patch.dict(sys.modules, {"keat_live": fake})
        self.mods.start()
        self.addCleanup(self.mods.stop)

    def test_off_non_dm_and_non_native_ids_are_not_captured(self):
        with mock.patch.dict(os.environ, {"PRAXIS_KEAT_CAPTURE": "off"}):
            self.assertIsNone(botapi.keat_capture(OWNER, "x", actor="Егор", outgoing=False,
                                                  source_id="10", is_dm=True))
        with mock.patch.dict(os.environ, {"PRAXIS_KEAT_CAPTURE": "on"}):
            self.assertIsNone(botapi.keat_capture("-100", "x", actor="a", outgoing=False,
                                                  source_id="10", is_dm=False))
            self.assertIsNone(botapi.keat_capture(OWNER, "x", actor="Егор", outgoing=False,
                                                  source_id="desk-1790000000000", is_dm=True))
        self.assertEqual(self.calls, [])

    def test_both_sides_of_the_dm_are_captured_with_the_memory_line(self):
        life = types.SimpleNamespace(rows=[])
        life.record_message = lambda chat_id, line, **kw: life.rows.append((chat_id, line, kw))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"PRAXIS_KEAT_CAPTURE": "on"}):
            rooms = botapi.Rooms(Path(tmp), life, "Hélène")
            rooms.describe(OWNER, title="Егор", is_dm=True)
            rooms.record(OWNER, "как дела?", outgoing=False, sender="Егор", source_id="10")
            rooms.record(OWNER, "всё идёт по плану", outgoing=True, source_id="11")
            rooms.record(OWNER, "из окна", outgoing=False, sender="Егор",
                         source_id="desk-1", source="window")
        self.assertEqual([(c[2], c[3]) for c in self.calls], [("10", "in"), ("11", "out")])
        self.assertEqual(self.calls[0][4], {"role": "user", "content": "как дела?", "actor": "Егор"})
        self.assertEqual(self.calls[1][4], {"role": "assistant", "content": "всё идёт по плану",
                                            "actor": "Hélène"})
        # Строка расписки = строка памяти; без расписки kwargs прежние (стабы без ключа живы).
        self.assertEqual(life.rows[0][1], "как дела?")
        self.assertIn("keat_occurrence", life.rows[0][2])
        self.assertIn("keat_occurrence", life.rows[1][2])
        self.assertNotIn("keat_occurrence", life.rows[2][2])


class Sidecar(unittest.TestCase):
    def rows(self, captured=True):
        out = []
        for i, (direction, line) in enumerate([("in", "первая"), ("out", "ответ"),
                                                ("in", "вторая"), ("in", "ещё"),
                                                ("out", "второй ответ"), ("in", "сейчас")]):
            meta = {"keat_occurrence": {"event": f"e{i}", "key": f"k{i}"}} if captured else {}
            out.append({"direction": direction, "line": line, "meta": meta})
        return out

    def dialogue(self, rows, sidecar):
        life = types.SimpleNamespace(HOT_HARD_HI=125, hot_records=lambda chat_id, n: rows,
                                     tape_window=lambda r, n: r, tape_chars_for=lambda chat_id: 0)
        with mock.patch.object(runner, "_life", life):
            return runner._dialogue(OWNER, sidecar)

    def test_whole_tape_captured_gives_groups_by_role(self):
        sidecar = {}
        history, current = self.dialogue(self.rows(), sidecar)
        self.assertEqual([h["role"] for h in history], ["user", "assistant", "user", "assistant"])
        self.assertEqual(history[2]["content"], "вторая\n\nещё")
        self.assertEqual(current, "сейчас")
        self.assertEqual([[r["event"] for r in g] for g in sidecar["history"]],
                         [["e0"], ["e1"], ["e2", "e3"], ["e4"]])
        self.assertEqual([r["event"] for r in sidecar["current"]], ["e5"])

    def test_one_uncaptured_row_means_no_sidecar_and_the_history_stays(self):
        rows = self.rows()
        rows[1]["meta"] = {}                     # её ответ не захвачен — как в проде 25.09
        sidecar = {"stale": 1}
        history, current = self.dialogue(rows, sidecar)
        self.assertEqual(sidecar, {})
        self.assertEqual(len(history), 4, "история не сужается до захваченного хвоста")
        self.assertEqual(current, "сейчас")

    def test_run_turn_hands_the_sidecar_only_to_the_owner_stream(self):
        seen = {}
        agent = types.SimpleNamespace(
            voice_turn_envelope=lambda *a, **kw: seen.update(kw) or "envelope")
        def filled(chat_id, sidecar=None):
            if sidecar is not None:
                sidecar.update(history=[[{}], [{}]], current=[{}])
            return [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], "z"

        owner_ctx = types.SimpleNamespace(is_dm=True, owner=True)
        with mock.patch.object(runner, "_agent", agent), \
                mock.patch.object(runner, "_dialogue", filled), \
                mock.patch.object(runner, "_orient", lambda chat_id: ""), \
                mock.patch.dict(os.environ, {"PRAXIS_KEAT_CAPTURE": "on"}):
            runner._run_turn(OWNER, "convo", "Егор", owner_ctx)
            self.assertIn("occurrence_sidecar", seen)
            seen.clear()
            runner._run_turn("-100", "convo", "Hope", types.SimpleNamespace(is_dm=False, owner=False))
            self.assertNotIn("occurrence_sidecar", seen)
        with mock.patch.object(runner, "_agent", agent), \
                mock.patch.object(runner, "_dialogue", filled), \
                mock.patch.object(runner, "_orient", lambda chat_id: ""), \
                mock.patch.dict(os.environ, {"PRAXIS_KEAT_CAPTURE": "off"}):
            seen.clear()
            runner._run_turn(OWNER, "convo", "Егор", owner_ctx)
            self.assertNotIn("occurrence_sidecar", seen)


class EpochRotation(unittest.TestCase):
    def test_keeps_young_and_newest_and_removes_the_rest_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            epochs = tree / "memory" / ".state" / "keat" / "epochs"
            now = time.time()
            for i in range(6):
                d = epochs / f"e{i}"
                d.mkdir(parents=True)
                (d / "a.json").write_bytes(b"x" * 1024)
                (d / "HEAD").write_bytes(b"h")
                age = 60 if i == 0 else 7200 + i * 60      # e0 свежая, e1..e5 старые
                os.utime(d / "HEAD", (now - age, now - age))
            report = boot.prune_keat_epochs(tree, keep=2, max_mb=64, min_age_sec=3600)
            left = sorted(p.name for p in epochs.iterdir())
            self.assertEqual(left, ["e0", "e1", "e2"])      # свежая + две новейшие из старых
            self.assertEqual(report["removed"], 3)
            report = boot.prune_keat_epochs(tree, keep=5, max_mb=0, min_age_sec=3600)
            self.assertEqual(sorted(p.name for p in epochs.iterdir()), ["e0"],
                             "свежая эпоха не снимается даже при нулевом бюджете")

    def test_missing_directory_is_a_quiet_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = boot.prune_keat_epochs(Path(tmp))
        self.assertEqual((report["kept"], report["removed"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
