"""Проекция КЕАТ под лентой-вызовами — с историей целиком (издание, 26.09).

Замер её прода 25.09: в личке владельца КЕАТ служил одну реплику — её ответы уходят рукой
`reply` и не захватывались, а захваченный хвост после её последнего ответа — это одна
реплика владельца (49–78 сообщений ленты выпадали из вызова). Захватить её ответы мало:
сборщик рисует её реплику парой «вызов reply + расписка», и у расписки нет оригинала, —
счёт сообщений не сходился с реестром. Проекция признаёт расписку в одной точной форме,
выводимой из предыдущего сообщения (`keat_candidate.render_receipt`), — и только её.

Запуск:  python praxis_test.py test_keat_receipts_2609 -v
"""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import frame_layout
import keat_live as live
from keat_candidate import (ContractError, RECEIPT_TRANSFORM, RENDER_TRANSFORM,
                            is_render_receipt, render_receipt)
from keat_capture import CaptureLedger
from keat_candidate import _digest
from keat_epoch import validate


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'policy.json'
        self.policy = dict(schema='keat.capture-policy.v1', namespace='test', root=self.tmp.name,
                           enrollments=[dict(stream='1', mode='owner', capture=dict(
                               issuer='helene:self', policy_revision='p1', audience=['owner'],
                               transfer='closed', presence_hidden=False))])
        self.path.write_text(json.dumps(self.policy), encoding='utf-8')
        env = patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'on',
            'PRAXIS_KEAT_CAPTURE_POLICY': str(self.path), 'PRAXIS_KEAT': 'serve',
            'PRAXIS_KEAT_PAIRS': '[["owner","1"]]', 'PRAXIS_FRAME_FORM': 'new'})
        env.start()
        self.addCleanup(env.stop)
        self.ctx = SimpleNamespace(chat_id=1, owner=True, is_dm=True, owner_audience=True)

    def ingress(self, mid, direction, content, actor='Егор'):
        role = 'assistant' if direction == 'out' else 'user'
        return live.capture_ingress('1', is_dm=True, source_id=mid, direction=direction,
                                    payload={'role': role, 'content': content, 'actor': actor})

    def dialogue(self):
        """Две реплики владельца, два её ответа, текущая — все захвачены (обе стороны)."""
        refs = [self.ingress(10, 'in', 'первая'), self.ingress(11, 'out', 'ответ один', 'Hélène'),
                self.ingress(12, 'in', 'вторая'), self.ingress(13, 'out', 'ответ два', 'Hélène'),
                self.ingress(14, 'in', 'текущая')]
        self.assertTrue(all(refs))
        history = [dict(role='user', content='первая'), dict(role='assistant', content='ответ один'),
                   dict(role='user', content='вторая'), dict(role='assistant', content='ответ два')]
        sidecar = dict(history=[[refs[0]], [refs[1]], [refs[2]], [refs[3]]], current=[refs[4]])
        return history, 'текущая', sidecar

    def render(self, history, current, hands=True):
        return frame_layout.tape(history, hands=hands) + [dict(role='user', content=current)]

    def select(self, state, tape, run='run1', call='call1'):
        with live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('system', tape, [])
            receipt = live.select('system', provider, [], run, call)
            return receipt, provider

    def test_hand_tape_is_served_with_the_whole_dialogue(self):
        history, current, sidecar = self.dialogue()
        state = live.adopt_projection(self.ctx, history, current, sidecar)
        self.assertIsNotNone(state)
        tape = self.render(history, current)
        # Её два ответа — две пары «вызов + расписка»: шесть сообщений ленты и текущее.
        self.assertEqual(len(tape), 7)
        self.assertEqual(sum(is_render_receipt(tape, i) for i in range(len(tape))), 2)
        receipt, provider = self.select(state, tape)
        self.assertIsNotNone(receipt, live.activation_reason())
        self.assertEqual(receipt['status'], 'served')
        self.assertEqual(provider, tape)          # история не выпала: ровно лента хода
        store = live._store(state)
        source = json.loads(store.epochs._load(state.head)['source'])
        kinds = [(entry['transform'], len(entry['origins'])) for entry in source['projection']]
        self.assertEqual(kinds, [(RENDER_TRANSFORM, 1), (RENDER_TRANSFORM, 1),
                                 (RECEIPT_TRANSFORM, 0), (RENDER_TRANSFORM, 1),
                                 (RENDER_TRANSFORM, 1), (RECEIPT_TRANSFORM, 0),
                                 (RENDER_TRANSFORM, 1)])

    def test_prose_tape_still_served(self):
        """Без руки reply (рычаг опущен) лента — проза, расписок нет, счёт один к одному."""
        history, current, sidecar = self.dialogue()
        state = live.adopt_projection(self.ctx, history, current, sidecar)
        tape = self.render(history, current, hands=False)
        self.assertEqual(len(tape), 5)
        receipt, _ = self.select(state, tape)
        self.assertIsNotNone(receipt, live.activation_reason())

    def test_forged_receipt_is_not_a_receipt(self):
        history, current, sidecar = self.dialogue()
        state = live.adopt_projection(self.ctx, history, current, sidecar)
        tape = self.render(history, current)
        forged = copy.deepcopy(tape)
        forged[2]['content'][0]['tool_use_id'] = 'tape_000000000000'   # не свой вызов
        self.assertFalse(is_render_receipt(forged, 2))
        with live.bind_turn(state, forged):
            # Причина — сразу после привязки: `select` без привязки пишет свою.
            self.assertEqual(live.activation_reason(), 'projection_invalid')
        receipt, _ = self.select(state, forged)
        self.assertIsNone(receipt)
        # Расписка без вызова перед ней — тоже не расписка.
        self.assertIsNone(render_receipt(dict(role='user', content='x')))
        self.assertFalse(is_render_receipt([tape[2]], 0))

    def test_ledger_refuses_receipt_transform_on_ordinary_message(self):
        history, current, sidecar = self.dialogue()
        tape = self.render(history, current, hands=False)
        ledger = CaptureLedger(Path(self.tmp.name) / 'capture' / _digest('test'), 'test')
        events = [r['event'] for r in ledger.snapshot()['receipts']]
        origins = [[e] for e in events]
        with self.assertRaises(ContractError):
            ledger.project(messages=tape, origins=origins[:4] + [[]],
                           transforms=[RENDER_TRANSFORM] * 4 + [RECEIPT_TRANSFORM],
                           issuer='helene:self', tools=[], historical_count=4)

    def test_validate_refuses_forged_receipt_in_saved_source(self):
        history, current, sidecar = self.dialogue()
        state = live.adopt_projection(self.ctx, history, current, sidecar)
        tape = self.render(history, current)
        receipt, _ = self.select(state, tape)
        self.assertIsNotNone(receipt)
        obj = live._store(state).epochs._load(state.head)
        source = json.loads(obj['source'])
        # Подменить преобразование обычного сообщения на расписку — отказ контракта.
        source['projection'][0]['transform'] = RECEIPT_TRANSFORM
        source['projection'][0]['origins'] = []
        raw = json.dumps(source, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        from keat_capture import NarrowingReader
        reader = NarrowingReader(state.ledger.directory, 'test')
        with self.assertRaises(ContractError):
            reader.read(source_bytes=raw, audience=('owner',), now=1)

    def test_tool_loop_and_resume_keep_receipt_transforms(self):
        history, current, sidecar = self.dialogue()
        state = live.adopt_projection(self.ctx, history, current, sidecar)
        tape = self.render(history, current)
        with live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('system', tape, [])
            first = live.select('system', provider, [], 'run1', 'call1')
            self.assertIsNotNone(first)
            additions = [dict(role='assistant', content=[dict(type='text', text='думаю')]),
                         dict(role='user', content=[dict(type='text', text='дальше')])]
            self.assertTrue(live.capture_appended(tape, additions))
            tape.extend(additions)
            provider = live.prepare_provider_messages('system', tape, [])
            second = live.select('system', provider, [], 'run1', 'call2')
            self.assertIsNotNone(second, live.activation_reason())
        saved = dict(system='system', messages=provider, tools=[], keat=second)
        with live.bind_resume(saved, 'run1', 'call2') as reopened:
            self.assertIsNotNone(reopened)

    def test_wake_seed_has_no_gender(self):
        payload = live._scheduled_wake_payload({'kind': 'wake', 'goal': 'проверить почту'}, 'x')
        self.assertTrue(payload['content'].startswith('Твоя просьба разбудить себя вот с чем:'))
        payload = live._scheduled_wake_payload({'kind': 'wake', 'goal': ''}, 'x')
        self.assertTrue(payload['content'].startswith('Твоя просьба — разбудить себя'))


if __name__ == '__main__':
    unittest.main()
