# -*- coding: utf-8 -*-
"""Хвосты журналов в канале окна не читают диск в простое (1.0.1).

Жалоба тестера на Windows: «Элен в простое грузит диск». `readers.tail_lines` читал с конца
ВСЕГДА 4 МБ, даже ради пяти строк, а окно спрашивает `/api/state` раз в 8 с, `/api/pulse` —
раз в 20 с (замер: ~20 МБ на цикл опросов, ~130 ГБ в сутки на дереве «пары недель»).
Проверяется: неизменный файл отвечает из кэша без чтения; дописанный — новыми строками;
малое n читает малый кусок; нехватка строк расширяет кусок до потолка; рваная первая
строка среза не попадает в ответ.

Запуск:  python tests/t_tail_idle_2609.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent)]
from deskd import readers  # noqa: E402


class TailLines(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "llm_calls.jsonl"
        self.path.write_text("".join(f'{{"i": {i}, "pad": "{"x" * 180}"}}\n' for i in range(40000)),
                             encoding="utf-8")
        readers._TAIL_CACHE.clear()
        self.addCleanup(readers._TAIL_CACHE.clear)

    def reads(self):
        """Сколько байт прочитали открытые на чтение файлы — через подмену Path.open."""
        seen = {"bytes": 0}
        real_open = Path.open

        def counting(path, *a, **k):
            fh = real_open(path, *a, **k)
            real_read = fh.read

            def read(*ra):
                data = real_read(*ra)
                seen["bytes"] += len(data)
                return data
            fh.read = read
            return fh
        return seen, mock.patch.object(Path, "open", counting)

    def test_small_n_reads_a_small_chunk_and_exact_lines(self):
        seen, patch = self.reads()
        with patch:
            rows = readers.tail_lines(self.path, 5)
        self.assertEqual([r.split(",")[0] for r in rows],
                         [f'{{"i": {i}' for i in range(39995, 40000)])
        self.assertLessEqual(seen["bytes"], readers._TAIL_FIRST_CHUNK, "ради пяти строк — один малый кусок")

    def test_unchanged_file_answers_from_cache_without_reading(self):
        first = readers.tail_lines(self.path, 12)
        with mock.patch.object(Path, "open", side_effect=AssertionError("диск трогать нельзя")):
            again = readers.tail_lines(self.path, 12)
        self.assertEqual(first, again)
        again.append("чужая правка")
        self.assertNotIn("чужая правка", readers.tail_lines(self.path, 12), "кэш не отдаётся наружу по ссылке")

    def test_appended_file_is_reread(self):
        readers.tail_lines(self.path, 3)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write('{"i": "new"}\n')
        self.assertEqual(readers.tail_lines(self.path, 1), ['{"i": "new"}'])

    def test_many_lines_expand_up_to_the_ceiling_and_drop_torn_first_line(self):
        rows = readers.tail_lines(self.path, 15000)      # ~3 МБ — под потолком в 4 МБ
        self.assertEqual(len(rows), 15000)
        self.assertTrue(all(r.startswith('{"i": ') for r in rows), "рваная первая строка не попала")
        capped = readers.tail_lines(self.path, 100000, max_bytes=100_000)
        self.assertLess(len(capped), 1000, "потолок max_bytes по-прежнему держит объём")
        self.assertTrue(all(r.startswith('{"i": ') for r in capped))

    def test_same_n_sees_the_append(self):
        """26.09 (W3): прежний стенд читал с n=3 и n=1 — разные ключи кэша, и мутант,
        отдающий кэш без сверки отпечатка, проходил. Тот же n до и после дозаписи."""
        self.assertEqual(readers.tail_lines(self.path, 1)[0].split(",")[0], '{"i": 39999')
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write('{"i": "new"}\n')
        self.assertEqual(readers.tail_lines(self.path, 1), ['{"i": "new"}'])

    def test_unicode_line_separators_do_not_split_a_record(self):
        """26.09 (W3 S6): U+2028/U+2029/U+0085 внутри JSON-строки — не конец строки."""
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write('{"i": "sep", "t": "до после и\x85конец"}\n')
        rows = readers.tail_jsonl(self.path, 1)
        self.assertEqual(rows[-1]["t"], "до после и\x85конец")

    def test_big_reads_are_not_held_by_the_cache(self):
        """26.09 (W3 S8): карточки прогонов читают хвосты до 16 МБ — кэш их не держит."""
        readers.tail_lines(self.path, 15000)          # ~3 МБ
        self.assertEqual(readers._TAIL_CACHE, {}, "запись больше потолка — мимо кэша")
        readers.tail_lines(self.path, 5)
        held = sum(sum(len(x) for x in rows) for _s, rows in readers._TAIL_CACHE.values())
        self.assertLessEqual(held, readers._TAIL_CACHE_CHARS)

    def test_nonpositive_n_is_empty(self):
        self.assertEqual(readers.tail_lines(self.path, 0), [])
        self.assertEqual(readers.tail_lines(self.path, -1), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
