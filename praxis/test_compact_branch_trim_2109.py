"""Корень форума тоже режется: свёртка МЕСТА против буфера ОДНОЙ ветки.

Свёртка считается по месту — по всем веткам комнаты сразу, — а кольцо раннера держит
одну ветку. Смежным куском свёрнутый блок в таком кольце не лежит НИКОГДА, и прежняя
проверка на смежность отвечала «не режем» вечно: с 13.09.2026 это 202 отказа подряд,
корневой буфер форума Ouroboros AI дорос до 279 КБ и ехал в кадр каждый ход.

Режем по принадлежности: строка буфера уходит, только если она входит в свёрнутый блок,
и ровно по одному разу на каждое её вхождение, считая от головы.

Запуск:  python praxis_test.py test_compact_branch_trim_2109 -v
"""

from __future__ import annotations

import unittest
from collections import deque

import mtproto_runner as runner


class BranchBufferIsTrimmedByMembership(unittest.TestCase):
    CHAT = "8008"

    def setUp(self) -> None:
        self._buf = runner._buf.get(self.CHAT)
        self._ids = runner._buffer_message_ids.get(self.CHAT)

    def tearDown(self) -> None:
        for store, saved in ((runner._buf, self._buf),
                             (runner._buffer_message_ids, self._ids)):
            if saved is None:
                store.pop(self.CHAT, None)
            else:
                store[self.CHAT] = saved
        runner._buf_dirty.discard(self.CHAT)

    def _arm(self, lines, ids=None):
        runner._buf[self.CHAT] = deque(lines, maxlen=runner.BUF_MAXLEN)
        if ids is not None:
            runner._buffer_message_ids[self.CHAT] = deque(ids, maxlen=runner.BUF_MAXLEN)
        else:
            runner._buffer_message_ids.pop(self.CHAT, None)
        return runner._buf[self.CHAT]

    def test_the_rooms_fold_trims_this_branch_and_leaves_the_rest(self):
        """Свёртка охватила две ветки; из этой уходит только её часть."""
        lines = ["корень-1", "ветка-A-1", "корень-2", "ветка-A-2", "свежее"]
        buf = self._arm(lines, [1, 2, 3, 4, 5])
        folded = ["корень-1", "чужая-ветка-B-1", "корень-2", "чужая-ветка-B-2"]
        dropped = runner._drop_folded_by_membership(self.CHAT, buf, list(lines), folded)
        self.assertEqual(dropped, 2)
        self.assertEqual(list(buf), ["ветка-A-1", "ветка-A-2", "свежее"])
        self.assertEqual(list(runner._buffer_message_ids[self.CHAT]), [2, 4, 5])

    def test_a_line_outside_the_fold_is_never_dropped(self):
        """Единственный твёрдый предел: чего нет в свёртке — не трогаем."""
        lines = ["непредставленное", "свёрнутое"]
        buf = self._arm(lines, [10, 11])
        dropped = runner._drop_folded_by_membership(self.CHAT, buf, list(lines),
                                                    ["свёрнутое"])
        self.assertEqual(dropped, 1)
        self.assertEqual(list(buf), ["непредставленное"])
        self.assertEqual(list(runner._buffer_message_ids[self.CHAT]), [10])

    def test_a_fresh_repeat_of_the_same_text_survives(self):
        """Кратность: свёртка гасит своё вхождение, свежий повтор остаётся."""
        lines = ["привет", "между", "привет"]
        buf = self._arm(lines, [20, 21, 22])
        dropped = runner._drop_folded_by_membership(self.CHAT, buf, list(lines),
                                                    ["привет"])
        self.assertEqual(dropped, 1)
        self.assertEqual(list(buf), ["между", "привет"])
        self.assertEqual(list(runner._buffer_message_ids[self.CHAT]), [21, 22])

    def test_nothing_of_the_fold_here_means_nothing_is_cut(self):
        lines = ["своё-1", "своё-2"]
        buf = self._arm(lines, [30, 31])
        dropped = runner._drop_folded_by_membership(self.CHAT, buf, list(lines),
                                                    ["чужое-1", "чужое-2"])
        self.assertEqual(dropped, 0)
        self.assertEqual(list(buf), lines)

    def test_a_desynced_id_queue_cancels_the_cut_entirely(self):
        """Рассинхрон строк и message_id хуже, чем несрезанный буфер."""
        lines = ["a", "b", "c"]
        buf = self._arm(lines, [40, 41])          # на одну расписку меньше
        dropped = runner._drop_folded_by_membership(self.CHAT, buf, list(lines), ["a"])
        self.assertEqual(dropped, 0)
        self.assertEqual(list(buf), lines)
        self.assertEqual(list(runner._buffer_message_ids[self.CHAT]), [40, 41])

    def test_no_ids_queue_at_all_still_trims_the_buffer(self):
        lines = ["x", "y"]
        buf = self._arm(lines, None)
        dropped = runner._drop_folded_by_membership(self.CHAT, buf, list(lines), ["x"])
        self.assertEqual(dropped, 1)
        self.assertEqual(list(buf), ["y"])

    def test_an_empty_fold_is_not_a_cut(self):
        lines = ["z"]
        buf = self._arm(lines, [50])
        self.assertEqual(runner._drop_folded_by_membership(self.CHAT, buf, list(lines), []), 0)
        self.assertEqual(list(buf), lines)


if __name__ == "__main__":
    unittest.main()
