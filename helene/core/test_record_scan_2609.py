"""26.09 — новое сообщение не обходит всю ленту с вопросом о месте на каждый ключ.

Профиль бута 26.09 (py-spy с хоста): главный поток стоял в `record_message` →
`_recent_duplicate` / поиск ревизий → `iter_events` → `_same_place` → `place_key` →
`telegram_routes.read` → `read_text`. На КАЖДОЕ новое сообщение — два полного обхода ленты
(~68 тыс. записей), и для сотен ключей чатов место резолвилось с чтением файла маршрутов —
в главном цикле, под `_WRITE_LOCK`. Раннер стоял на 100 % ЦП, бут шёл 10–18 минут.

Прибито: дешёвый фильтр по содержимому (`where`) идёт до вопроса о месте — место
спрашивается только у записей того же ключа дедупа / того же сообщения; смысл отбора
тот же (запись проходит, только если выполнены оба условия).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_life as ml


class RecordScanAsksPlaceOnlyForMatches(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.asked: list[tuple[str, str]] = []

        def same_place(a, b):
            self.asked.append((str(a), str(b)))
            return str(a) == str(b)
        for patcher in (patch.object(ml, 'EVENTS_DIR', self.dir),
                        patch.object(ml, '_same_place', same_place)):
            patcher.start()
            self.addCleanup(patcher.stop)
        ml._EVENTS_CACHE.clear()
        self.addCleanup(ml._EVENTS_CACHE.clear)
        rows = []
        for i in range(600):
            chat = f'-100{i % 60}'
            rows.append({'id': f'ev{i:04d}', 'ts': f'2026-09-25T10:{i % 60:02d}:{i % 60:02d}.000Z',
                         'kind': 'conversation_message', 'chat_id': chat, 'text': f't{i}',
                         'source': 'telegram', 'source_id': str(1000 + i),
                         'dedupe_key': f'{chat}:{1000 + i}', 'meta': {'is_dm': False}})
        with (self.dir / '2026-09-25.jsonl').open('w', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    def test_duplicate_miss_asks_no_place_at_all(self):
        self.assertIsNone(ml._recent_duplicate('-1007', 'нет-такого-ключа', {'dedupe': []}))
        self.assertEqual(self.asked, [], 'ни одна запись не совпала по ключу — место не спрашивается')

    def test_duplicate_hit_asks_place_only_for_the_match(self):
        found = ml._recent_duplicate('-1007', '-1007:1007', {'dedupe': []})
        self.assertEqual(found['id'], 'ev0007')
        self.assertEqual(len(self.asked), 1)
        self.assertIsNone(ml._recent_duplicate('-1008', '-1007:1007', {'dedupe': []}),
                          'тот же ключ в другом месте — не дубль этого места')

    def test_where_is_the_same_selection_as_filtering_after(self):
        wanted = {1003, 1063, 1123}
        pick = lambda row: int(row['source_id']) in wanted  # noqa: E731
        after = [r['id'] for r in ml.iter_events(chat_id='-1003') if pick(r)]
        self.asked.clear()
        before = [r['id'] for r in ml.iter_events(chat_id='-1003', where=pick)]
        self.assertEqual(before, after)
        self.assertLessEqual(len(self.asked), 1, 'место — один раз на совпавший ключ')


if __name__ == '__main__':
    unittest.main()
