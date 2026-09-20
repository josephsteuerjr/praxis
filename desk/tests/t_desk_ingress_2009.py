"""Desk ingress recovery: стабильный source_id и replay processed-записок.

Что закреплено (срез 20.09.2026, аудит):

  ingress id     — штамп имени записки, а не «сейчас»: replay той же записки
                   рождает ту же identity (dedupe_key памяти жизни совпадает)
  replay         — записка в processed без парной .done переигрывается после
                   рестарта тем же id; успешный ход ставит .done, упавший — нет
  свежие записки — обычный путь тоже передаёт ingress_id (штамп), не «сейчас»

Запуск:  python praxis_test.py test_desk_ingress_2009 -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "desk"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import localharness.runner as runner  # noqa: E402


class FakeLife:
    def __init__(self):
        self.calls = []

    def record_message(self, stream, text, *, actor, direction, source, source_id,
                       is_dm, ts, dedupe_key):
        self.calls.append({"stream": stream, "direction": direction,
                           "source_id": source_id, "dedupe_key": dedupe_key})


class IngressIdentity(unittest.TestCase):
    def test_same_note_stamp_gives_same_dedupe_key(self):
        """Один и тот же штамп записки → одна и та же identity в памяти жизни.
        Раньше source_id брался из «сейчас» и replay дублировал строку жизни."""
        life = FakeLife()
        room = runner.STREAM
        with mock.patch.object(runner, "_now"), \
                mock.patch.object(runner, "_room") as room_factory, \
                mock.patch.object(runner, "_hear_attachments", return_value=([], [])), \
                mock.patch.object(runner, "_ingest_attachments", return_value=([], [])), \
                mock.patch.object(runner, "_turn_in_window", return_value="spoken") as turn:
            desk_room = mock.Mock()
            desk_room.life = mock.MagicMock()
            room_factory.return_value = desk_room
            # transport life доступен через desk_room? Нет — life пишет room.life.
            # Проверяем то, что видит аудитория: room.life вызван с одинаковым source_id
            # и _turn_in_window получил тот же id.
            for _ in range(2):
                runner.handle_desk("привет", room=room, ingress_id="note:20260920T2100__x")
        ids = [c.kwargs.get("source_id") for c in desk_room.life.call_args_list]
        self.assertEqual(ids, ["note:20260920T2100__x"] * 2,
                         "identity входа стабильна между replay")
        turn_ids = [c.args[0] for c in turn.call_args_list]
        self.assertEqual(turn_ids, ["note:20260920T2100__x"] * 2)

    def test_default_still_unique_per_call(self):
        with mock.patch.object(runner, "_now") as now, \
                mock.patch.object(runner, "_room") as room_factory, \
                mock.patch.object(runner, "_hear_attachments", return_value=([], [])), \
                mock.patch.object(runner, "_ingest_attachments", return_value=([], [])), \
                mock.patch.object(runner, "_turn_in_window", return_value="spoken") as turn:
            import datetime as dt
            now.side_effect = [dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc),
                               dt.datetime(2026, 9, 20, 12, 1, tzinfo=dt.timezone.utc)]
            desk_room = mock.Mock()
            room_factory.return_value = desk_room
            runner.handle_desk("a", room=runner.STREAM)
            runner.handle_desk("b", room=runner.STREAM)
        ids = [c.args[0] for c in turn.call_args_list]
        self.assertNotEqual(ids[0], ids[1], "без ingress_id разные вызовы различимы")


class ReplayProcessed(unittest.TestCase):
    def _processed(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        processed = base / "processed"
        processed.mkdir(parents=True)
        return processed

    def test_unclaimed_note_is_replayed_then_done(self):
        processed = self._processed()
        note = processed / "20260920T2105__a.md"
        note.write_text("# заголовок\nпривет из краша", encoding="utf-8")
        with mock.patch.object(runner, "handle_desk") as hd, \
                mock.patch.object(runner.transport, "is_room", return_value=True):
            replayed = runner._replay_unclaimed_notes(processed)
        self.assertEqual(replayed, [note.name])
        hd.assert_called_once()
        self.assertEqual(hd.call_args.kwargs.get("ingress_id"), f"note:{Path(note.name).stem}")
        self.assertTrue((processed / (note.name + ".done")).exists(),
                        "успешный replay помечает записку")

    def test_done_note_is_not_replayed_again(self):
        processed = self._processed()
        note = processed / "20260920T2106__b.md"
        note.write_text("уже отыграно", encoding="utf-8")
        (processed / (note.name + ".done")).write_text("1\n", encoding="utf-8")
        with mock.patch.object(runner, "handle_desk") as hd:
            replayed = runner._replay_unclaimed_notes(processed)
        self.assertEqual(replayed, [])
        hd.assert_not_called()

    def test_failed_replay_leaves_note_for_next_restart(self):
        processed = self._processed()
        note = processed / "20260920T2107__c.md"
        note.write_text("упадёт", encoding="utf-8")
        with mock.patch.object(runner, "handle_desk",
                               side_effect=RuntimeError("канал умер")), \
                mock.patch.object(runner.transport, "is_room", return_value=True):
            replayed = runner._replay_unclaimed_notes(processed)
        self.assertEqual(replayed, [])
        self.assertFalse((processed / (note.name + ".done")).exists(),
                         "упавший replay не помечает записку — рестарт попробует снова")

    def test_telegram_target_note_goes_to_owner_path(self):
        processed = self._processed()
        note = processed / "20260920T2108__to__12345.md"
        note.write_text("в телегу", encoding="utf-8")
        with mock.patch.object(runner, "handle_desk") as hd, \
                mock.patch.object(runner, "handle_owner_note") as hon, \
                mock.patch.object(runner.transport, "is_room", return_value=False):
            replayed = runner._replay_unclaimed_notes(processed)
        self.assertEqual(replayed, [note.name])
        hd.assert_not_called()
        hon.assert_called_once()


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
