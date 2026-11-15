"""Empty window receipts retain canonical identity without poisoning fold inputs."""
import copy
from unittest.mock import patch

import memory_life as ml
from test_life_places import Base


class EmptyWindowDeliveries(Base):
    def say(self, text, source_id, **changes):
        return ml.record_message('window', text, actor='Agent', direction='out',
                                 source='window', source_id=source_id,
                                 dedupe_key='window:' + source_id, **changes)

    def test_rebuild_preserves_raw_events_and_dedupe_without_empty_hot_rows(self):
        empty = [self.say('', 'deliver-1'), self.say('', 'speech:' + 'a' * 32)]
        real = self.say(' exact text\n', 'speech:' + 'b' * 32)
        before = {p.name: p.read_bytes() for p in ml.EVENTS_DIR.glob('*.jsonl')}
        for _ in range(2):
            state = ml.rebuild_state('window')
            self.assertEqual([r['id'] for r in state['hot']], [real['id']])
            self.assertEqual(state['hot'][0]['line'], ' exact text\n')
            self.assertEqual(set(state['empty_delivery_receipts']), {r['id'] for r in empty})
            self.assertTrue({r['id'] for r in empty} <= {r['id'] for r in state['dedupe']})
            self.assertEqual({p.name: p.read_bytes() for p in ml.EVENTS_DIR.glob('*.jsonl')}, before)
            self.assertFalse(state['frontier'])
        self.assertEqual(self.say('', 'deliver-1')['id'], empty[0]['id'])

    def test_whitespace_native_chunks_unknown_and_state_only_content_remain(self):
        space = self.say(' ', 'speech:' + 'c' * 32)
        unknown = self.say('', 'unrecognized-id')
        native = ml.record_message('window', '\n', direction='out', source='telegram', source_id='17:chunk:1')
        state = ml._load_state('window', rebuild=False)
        residual = copy.deepcopy(state['hot'][0])
        residual.update(id='state-only', line='unproved words')
        state['hot'].append(residual)
        ml._save_state(state)
        state = ml.rebuild_state('window')
        by_id = {r['id']: r for r in state['hot']}
        self.assertEqual(set(by_id), {space['id'], unknown['id'], native['id'], 'state-only'})
        self.assertEqual(by_id[space['id']]['line'], ' ')
        self.assertEqual(by_id[native['id']]['line'], '\n')
        self.assertTrue(by_id['state-only']['state_residual'])
        self.assertFalse(state['empty_delivery_receipts'])

    def test_authored_fold_sources_exclude_receipts_without_false_coverage(self):
        empty = self.say('', 'deliver-42')
        real = self.say('Words to retain.', 'speech:' + 'd' * 32)
        tail = self.say('Keep a current tail.', 'speech:' + 'e' * 32)
        ml.rebuild_state('window')
        with patch.object(ml, '_model_compact', side_effect=AssertionError('no model call')):
            selected = ml.prepare_authored_fold('window', limit=100)
            self.assertTrue(selected['ok'], selected)
            self.assertEqual([r['id'] for r in selected['sources']], [real['id']])
            folded = ml.apply_authored_fold('window', selected['selection'], 'The agent retained these words.')
            self.assertTrue(folded['ok'], folded)
            self.assertEqual(folded['source_ids'], [real['id']])
            self.assertNotIn(empty['id'], folded['source_ids'])
        self.assertEqual([r['id'] for r in ml.rebuild_state('window')['hot']], [tail['id']])
