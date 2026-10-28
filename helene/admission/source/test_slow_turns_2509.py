# -*- coding: utf-8 -*-
"""Стенды кандидата slow-turns-2509 (25.09): реестр захвата продолжается с хвоста, лента
событий разбирается по файлу дня один раз, наружу уходят копии.

Замер 25.09 (ход в личке владельца, 20 вызовов модели): 718 с одних «канареек» КЕАТ,
потому что реестр 19,6 МБ разбирался целиком на каждую операцию; правка сообщения в
абстракте — четыре полных прохода по 79 МБ ленты под общим замком, цикл Telegram
стоял по 30–92 с.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import keat_capture
from keat_candidate import ContractError
from keat_capture import CaptureLedger

import memory_life as ml


class LedgerStateContinuesFromTheTail(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ns = 'telegram:account:slow'
        self.ledger = CaptureLedger(self.temp.name, self.ns)
        self.path = Path(self.temp.name) / 'capture.jsonl'
        self.key = (str(self.ledger.directory), self.ns)

    def capture(self, ledger, event, mid):
        grant = dict(issuer='capture:test', policy_revision='p1', grant='grant:' + event,
                     audience=['owner'], transfer='none', presence_hidden=False)
        return ledger.issue(occurrence_id=event, key=f'telegram:chat:1:topic:0:message:{mid}',
                            kind='message', payload={'role': 'user', 'content': 'x' * mid},
                            capture=grant)

    def full_parse(self):
        raw = self.path.read_bytes()
        return keat_capture._state_view(
            keat_capture._continue_state(keat_capture._fresh_state(), raw, self.ns))

    def test_cached_state_equals_a_full_parse_and_tracks_the_file(self):
        for i in (1, 2, 3):
            self.capture(self.ledger, f'e{i}', i)
        # После `issue` кэш отстаёт на дописанную строку — её подберёт следующий вызов.
        self.assertEqual(self.ledger._state(), self.full_parse())
        cached = keat_capture._STATE_CACHE[self.key]
        self.assertEqual(cached['consumed'], self.path.stat().st_size)
        # Дописал кто-то другой (второй экземпляр без кэша) — хвост продолжает разбор,
        # полного разбора с нуля не было.
        with patch.dict(os.environ, {'PRAXIS_KEAT_STATE_CACHE': 'off'}):
            self.capture(CaptureLedger(self.temp.name, self.ns), 'e4', 4)
        with patch.object(keat_capture, '_fresh_state', wraps=keat_capture._fresh_state) as fresh:
            receipts = self.ledger._state()[0]
        self.assertEqual([r['event'] for r in receipts], ['e1', 'e2', 'e3', 'e4'])
        self.assertEqual(fresh.call_count, 0, 'хвост должен был продолжить разбор, а не начать заново')
        self.assertEqual(self.ledger._state(), self.full_parse())
        self.assertEqual(keat_capture._STATE_CACHE[self.key]['consumed'], self.path.stat().st_size)

    def test_rewritten_file_is_parsed_from_scratch(self):
        for i in (1, 2, 3):
            self.capture(self.ledger, f'e{i}', i)
        self.ledger._state()
        raw3 = self.path.read_bytes()
        self.assertEqual(keat_capture._STATE_CACHE[self.key]['consumed'], len(raw3))
        # Кто-то дописал валидную строку, а внутри уже разобранного префикса подменили
        # аудиторию той же длины. Сцепка хвоста цела (он ссылается на нетронутую строку),
        # и без сверки префикса подмена ушла бы читателям молча. Префикс по sha256 не
        # сходится — полный разбор, и контракт падает там же, где падал раньше.
        with patch.dict(os.environ, {'PRAXIS_KEAT_STATE_CACHE': 'off'}):
            self.capture(CaptureLedger(self.temp.name, self.ns), 'e4', 4)
        raw4 = self.path.read_bytes()
        lines = raw4.split(b'\n')
        lines[1] = lines[1].replace(b'"audience":["owner"]', b'"audience":["ownes"]', 1)
        tampered = b'\n'.join(lines)
        self.assertNotEqual(tampered, raw4)
        self.assertEqual(len(tampered), len(raw4))
        self.path.write_bytes(tampered)
        with self.assertRaises(ContractError):
            self.ledger._state()
        # Порча той же длины без хвоста — хэш не сходится, полный разбор, тоже падает.
        self.path.write_bytes(raw4)
        self.ledger._state()
        self.path.write_bytes(tampered)
        with self.assertRaises(ContractError):
            self.ledger._state()
        # Укороченный по границе строки реестр — снова разбирается целиком и годен.
        self.path.write_bytes(b'\n'.join(raw4.split(b'\n')[:2]) + b'\n')
        receipts = self.ledger._state()[0]
        self.assertEqual([r['event'] for r in receipts], ['e1', 'e2'])
        self.assertEqual(self.ledger._state(), self.full_parse())

    def test_switch_off_means_full_parse_every_time(self):
        self.capture(self.ledger, 'e1', 1)
        keat_capture._STATE_CACHE.pop(self.key, None)
        with patch.dict(os.environ, {'PRAXIS_KEAT_STATE_CACHE': 'off'}):
            self.assertEqual(self.ledger._state(), self.full_parse())
            self.assertNotIn(self.key, keat_capture._STATE_CACHE)

    def test_views_do_not_leak_the_cache(self):
        self.capture(self.ledger, 'e1', 1)
        receipts, payloads, heads, current, _parent, invalidations = self.ledger._state()
        receipts.clear(); payloads.clear(); heads.clear(); current.clear(); invalidations['x'] = True
        again = self.ledger._state()
        self.assertEqual(len(again[0]), 1)
        self.assertNotIn('x', again[5])


class EventsAreParsedOncePerDayFile(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        for patcher in (patch.object(ml, 'EVENTS_DIR', self.dir),
                        patch.object(ml, '_same_place', lambda a, b: str(a) == str(b))):
            patcher.start()
            self.addCleanup(patcher.stop)
        ml._EVENTS_CACHE.clear()
        self.addCleanup(ml._EVENTS_CACHE.clear)

    def write(self, name, rows):
        with (self.dir / name).open('a', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    def row(self, i, chat='A', kind='conversation_message'):
        return {'id': f'ev{i:03d}', 'ts': f'2026-09-2{i % 5}T10:{i:02d}:00.000Z', 'kind': kind,
                'chat_id': chat, 'text': f't{i}', 'meta': {'is_dm': True}}

    def test_second_read_comes_from_cache_and_appends_are_seen(self):
        self.write('2026-09-24.jsonl', [self.row(1), self.row(2, chat='B')])
        self.write('2026-09-25.jsonl', [self.row(3)])
        first = ml.iter_events(chat_id='A')
        self.assertEqual([r['id'] for r in first], ['ev001', 'ev003'])
        with patch.object(ml.json, 'loads', wraps=ml.json.loads) as loads:
            again = ml.iter_events(chat_id='A')
        self.assertEqual(loads.call_count, 0, 'файлы не менялись — разбора быть не должно')
        self.assertEqual([r['id'] for r in again], ['ev001', 'ev003'])
        self.write('2026-09-25.jsonl', [self.row(4)])
        self.assertEqual([r['id'] for r in ml.iter_events(chat_id='A')], ['ev001', 'ev003', 'ev004'])
        self.assertEqual([r['id'] for r in ml.iter_events(chat_id='B')], ['ev002'])
        self.assertEqual(len(ml.iter_events()), 4)

    def test_records_are_copies(self):
        self.write('2026-09-25.jsonl', [self.row(1)])
        one = ml.iter_events(chat_id='A')[0]
        one['text'] = 'changed'; one['meta']['is_dm'] = False
        fresh = ml.iter_events(chat_id='A')[0]
        self.assertEqual(fresh['text'], 't1')
        self.assertTrue(fresh['meta']['is_dm'])

    def test_switch_off_reads_the_files_every_time(self):
        self.write('2026-09-25.jsonl', [self.row(1)])
        with patch.dict(os.environ, {'PRAXIS_EVENTS_CACHE': 'off'}):
            self.assertEqual([r['id'] for r in ml.iter_events(chat_id='A')], ['ev001'])
            self.assertEqual(ml._EVENTS_CACHE, {})
            with patch.object(ml.json, 'loads', wraps=ml.json.loads) as loads:
                ml.iter_events(chat_id='A')
            self.assertEqual(loads.call_count, 1)

    def test_memory_ceiling_leaves_a_big_file_uncached(self):
        self.write('2026-09-25.jsonl', [self.row(1)])
        with patch.object(ml, '_EVENTS_CACHE_LIMIT', 10):
            ml.iter_events(chat_id='A')
            self.assertEqual(ml._EVENTS_CACHE, {})


if __name__ == '__main__':
    unittest.main()
