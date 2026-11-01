# -*- coding: utf-8 -*-
"""Стенд «свёртка памяти чата по кнопке» (27.09).

Запуск:  python tests/t_fold_button_2709.py

Егор 27.09: «интересно, как происходит компактирование в мейн чате Элен. Неплохо бы
сделать его по нажатию». Свёртка — её (`memory_life.fold_now`, та же, что у её руки
memory_compact(fold)); кнопка только просит. Здесь стерегутся обещания цепочки:

* канал кладёт просьбу и говорит правду о памяти места (сколько горячего, её пороги,
  идёт ли свёртка) — и не путает места;
* раннер берёт просьбу один раз, сворачивает ЕЁ функцией и отвечает распиской; вторая
  просьба, пока идёт первая, не берётся; сбой свёртки — словами, а не молчанием.

Модели нет: вместо memory_life — подделка с тем же `fold_now`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
from deskd import control  # noqa: E402
import runner  # noqa: E402


def life_state(tree: Path, room: str, hot: int) -> None:
    path = tree / "memory" / ".state" / "life" / f"{room}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "chat_id": room, "hot": [{"id": i} for i in range(hot)]}),
                    encoding="utf-8")


class Channel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)

    def test_пороги_её_личка_и_комната_разные(self):
        self.assertEqual(control.fold_bounds("window"), (150, 250, 300))
        self.assertEqual(control.fold_bounds("-100999999999999999"), (250, 400, 500))

    def test_состояние_говорит_сколько_горячего(self):
        life_state(self.tree, "window", 212)
        st = control.fold_state(self.tree, "window")
        self.assertEqual(st["hot"], 212)
        self.assertEqual((st["keep"], st["offer_at"], st["hard_at"]), (150, 250, 300))
        self.assertIsNone(st["pending"])
        self.assertIsNone(st["receipt"])

    def test_нет_состояния_нет_числа(self):
        self.assertIsNone(control.fold_state(self.tree, "window")["hot"])

    def test_runtime_policy_overrides_defaults_and_legacy_has_honest_offer_mode(self):
        folder = self.tree / 'memory/.state'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'fold-policy.json').write_text(json.dumps({'offer_mode': False, 'backstop_hours': 7,
            'group': {'keep': 333, 'offer_at': 555, 'hard_at': 777, 'token_cap': 88888}}))
        state = control.fold_state(self.tree, '-123')
        self.assertEqual((state['keep'], state['offer_at'], state['hard_at']), (333, 555, 777))
        self.assertEqual(state['policy_source'], 'runtime')
        self.assertFalse(state['offer_mode'])
        self.assertEqual(state['backstop_hours'], 7)
        self.assertEqual(state['token_cap'], 88888)
        legacy = control.fold_state(Path(self.tmp.name) / 'other', 'window')
        self.assertTrue(legacy['offer_mode'])
        self.assertEqual(legacy['backstop_hours'], 26)

    def test_runner_publishes_loaded_core_policy(self):
        from unittest import mock
        import types
        life = types.SimpleNamespace(fold_offer_enabled=lambda: True, FOLD_OFFER_BACKSTOP_HOURS=9,
            hot_bounds=lambda place: (300, 600, 900, 70000) if place == '-1' else (100, 200, 400, 40000),
            tape_chars_for=lambda place: 0 if place == '-1' else 5500)
        with mock.patch.object(runner, '_life', life):
            runner._publish_fold_policy(self.tree)
        state = control.fold_state(self.tree, '-12')
        self.assertEqual(state['keep'], 300)
        self.assertEqual(state['backstop_hours'], 9)
        self.assertEqual(state['policy_source'], 'runtime')

    def test_old_sender_cannot_advertise_automatic_retry_after_restart(self):
        state = self.tree / 'memory/.state'; state.mkdir(parents=True)
        reader = self.tree / 'memory/.control/desk_inbox'; reader.mkdir(parents=True)
        (state / 'telegram-delivery.json').write_text(json.dumps({'-12': {'state':'waiting','pid':123,'retry_at':time.time()+90}}))
        path = reader / '.reader.json'
        path.write_text(json.dumps({'pid':123,'at':time.time()}))
        self.assertEqual(control.fold_state(self.tree,'-12')['delivery']['state'], 'waiting')
        path.write_text(json.dumps({'pid':124,'at':time.time()}))
        self.assertIsNone(control.fold_state(self.tree,'-12')['delivery'])
        path.write_text(json.dumps({'pid':123,'at':time.time()-60}))
        self.assertIsNone(control.fold_state(self.tree,'-12')['delivery'])

    def test_owner_request_is_refused_without_queue_or_execution(self):
        said=control.fold_request(self.tree,"window")
        self.assertFalse(said["ok"])
        self.assertFalse((self.tree/"memory/.control"/control.FOLD_REQUEST).exists())
        self.assertIsNone(control.fold_state(self.tree,"window")["pending"])
        self.assertIsNone(control.fold_state(self.tree,"chat-2")["pending"])


    def test_странное_имя_не_принимается(self):
        self.assertFalse(control.fold_request(self.tree, "../../etc")["ok"])
        self.assertFalse((self.tree / "memory" / ".control" / control.FOLD_REQUEST).exists())








class AuthoredButton(unittest.TestCase):
    def test_owner_control_cannot_start_an_author_turn_or_create_a_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for room in ('window-deadbeef','-123__topic__19'):
                response=control.fold_request(root,room)
                self.assertFalse(response['ok'])
                self.assertIn('текущий агент',response['note'])
            self.assertEqual(list(root.rglob('*')),[])

    def test_retired_tick_preserves_old_requests_and_never_calls_model_or_fold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);claim=root/'memory/.control'/control.FOLD_PROCESSING
            claim.parent.mkdir(parents=True);claim.write_text('{"id":"old","room":"window"}')
            before=claim.read_bytes();life=mock.Mock();agent=mock.Mock()
            with mock.patch.object(runner,'_life',life),mock.patch.object(runner,'_agent',agent),mock.patch.object(runner,'_turn_in_window') as turn:
                for _ in range(3):runner._fold_tick(root)
            self.assertEqual(claim.read_bytes(),before)
            self.assertFalse((root/'memory/.state'/control.FOLD_RECEIPT).exists())
            life.fold_now.assert_not_called();turn.assert_not_called();self.assertFalse(agent.mock_calls)




if __name__ == "__main__":
    unittest.main(verbosity=2)
