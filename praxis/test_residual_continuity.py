"""State-only bytes survive authored fold preparation and epoch rollover."""
import unittest
from unittest import mock
from test_coverage_vs_current import Base, ROOM
import memory_life as ml
import frame_epoch as fe
import group_context as gc

class ResidualAcceptance(Base):
    def test_unrelated_fold_retains_state_only(self):
        with mock.patch.object(fe, 'STORE', self.tmp / 'epochs'), mock.patch.object(gc, 'GROUPS_DIR', self.tmp / 'groups'):
            for i in range(1, 6):
                self._msg(i, f'canonical {i}')
            state = ml._load_state(ROOM, rebuild=False)
            state['hot'].append(dict(state['hot'][0], id='ghost', source_id='999', line='EXACT STATE ONLY BYTES'))
            ml._save_state(state)
            before = fe.prepare_tape(ROOM, peer_id=ROOM, whole_room=True)
            self.assertIn('EXACT STATE ONLY BYTES', str(before))
            selected = ml.prepare_authored_fold(ROOM, limit=1)
            self.assertIn('EXACT STATE ONLY BYTES', str(ml._load_state(ROOM)))
            self.assertNotIn('ghost', [r['id'] for r in selected['sources']])
            applied = ml.apply_authored_fold(ROOM, selected['selection'], 'I retain the selected canonical source.')
            self.assertTrue(applied.get('ok'), applied)
            after = fe.prepare_tape(ROOM, peer_id=ROOM, whole_room=True)
            self.assertNotEqual(before['boundary'], after['boundary'])
            self.assertIn('EXACT STATE ONLY BYTES', str(after), 'state-only residual lost after unrelated fold')

    def test_changed_bytes_with_same_event_id_survive_repeated_rebuild(self):
        for i in range(1, 4):
            self._msg(i, 'canonical')
        state = ml._load_state(ROOM)
        state['hot'][0]['line'] = 'CHANGED EXACT BYTES \u00a0'
        ml._save_state(state)
        for _ in range(3):
            rebuilt = ml.rebuild_state(ROOM)
            residuals = [r for r in rebuilt['hot'] if r.get('state_residual')]
            self.assertEqual(len(residuals), 1)
            self.assertEqual(residuals[0]['line'], 'CHANGED EXACT BYTES \u00a0')
            self.assertTrue(residuals[0]['unprovable'])
            selected = ml.prepare_authored_fold(ROOM, limit=1)
            self.assertEqual(selected['sources'][0]['line'], 'Николай: canonical')

    def test_exact_covered_row_is_not_retained_as_residual(self):
        for i in range(1, 4):
            self._msg(i, f'canonical {i}')
        original = ml._load_state(ROOM)['hot'][0]
        selected = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertTrue(ml.apply_authored_fold(
            ROOM, selected['selection'], 'The first canonical source.')['ok'])
        state = ml._load_state(ROOM)
        state['hot'].append(original)
        ml._save_state(state)
        rebuilt = ml.rebuild_state(ROOM)
        self.assertNotIn(original['id'], [r['id'] for r in rebuilt['hot']])

if __name__ == '__main__':
    unittest.main()
