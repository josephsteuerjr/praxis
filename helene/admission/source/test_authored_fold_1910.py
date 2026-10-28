"""Acting-turn episodic recap: exact selected sources, no auxiliary generation."""
import unittest
from unittest import mock
import memory_life as ml
from test_coverage_vs_current import Base, ROOM

class AuthoredFold(Base):
    def seed(self):
        for i in range(1, 12): self._msg(i, f'original {i}')
        return ml.prepare_authored_fold(ROOM, limit=3)

    def test_selected_recap_survives_rebuild_and_appended_tail(self):
        selected = self.seed()
        self.assertTrue(selected['shown_complete'])
        self._msg(100, 'later unsummarized tail')
        with mock.patch.object(ml, '_model_compact', side_effect=AssertionError('helper forbidden')):
            out = ml.apply_authored_fold(ROOM, selected['selection'], 'I retain what happened, with limits.')
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['source_ids'], [r['id'] for r in selected['sources']])
        self.assertEqual(ml.compact_text(out['compact_id'], ROOM), out['recap'])
        state = ml.rebuild_state(ROOM)
        self.assertTrue(any('later unsummarized' in r.get('line','') for r in state['hot']))
        self.assertTrue(list(ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})))
        again = ml.apply_authored_fold(ROOM, selected['selection'], 'do not duplicate')
        self.assertFalse(again['ok'])

    def test_changed_selection_and_empty_author_do_not_advance(self):
        selected = self.seed()
        out = ml.apply_authored_fold(ROOM, selected['selection'], '')
        self.assertFalse(out['ok'])
        self._edit(1, 'changed original')
        out = ml.apply_authored_fold(ROOM, selected['selection'], 'old recap')
        self.assertFalse(out['ok'])
        self.assertEqual(out['folded'], 0)
        self.assertEqual(out['retained_text'], 'old recap')

    def test_h2_refusal_never_becomes_canonical_after_rebuild(self):
        selected = self.seed()
        before = [r['id'] for r in ml.rebuild_state(ROOM)['hot']]
        out = ml.apply_authored_fold(ROOM, selected['selection'], 'Intro\n## Decisions\nImportant')
        self.assertFalse(out['ok'])
        self.assertEqual([r['id'] for r in ml.rebuild_state(ROOM)['hot']], before)
        self.assertFalse(list(ml.COMPACTS_DIR.rglob('*.md')))

    def test_force_hard_and_refresh_never_use_helper(self):
        self.seed()
        with mock.patch.object(ml, '_model_compact', side_effect=AssertionError('helper forbidden')):
            self.assertTrue(ml.fold_now(ROOM)['author_required'])
            self.assertTrue(ml.compact_if_due(ROOM, force=True)['author_required'])
            self.assertEqual(ml.refresh_compacts(ROOM)['reason'], 'acting_author_required')
            self.assertEqual(ml._fold_tiers_transactional(ROOM), ([], []))

    def test_rewrite_rejects_h2_and_rolls_back_nonempty_partial_readback(self):
        selected = self.seed()
        out = ml.apply_authored_fold(ROOM, selected['selection'], 'Original complete recap.')
        self.assertTrue(out['ok'], out)
        cid = out['compact_id']
        rejected = ml.rewrite_compact_text(cid, 'Intro\n## Decisions\nImportant', chat_id=ROOM)
        self.assertFalse(rejected['ok'])
        self.assertEqual(ml.compact_text(cid, ROOM), 'Original complete recap.')
        read = ml._read_compact_candidate
        def partial(path, chat):
            meta, recap = read(path, chat)
            return meta, 'partial' if recap == 'Replacement complete recap.' else recap
        with mock.patch.object(ml, '_read_compact_candidate', side_effect=partial):
            rejected = ml.rewrite_compact_text(cid, 'Replacement complete recap.', chat_id=ROOM)
        self.assertFalse(rejected['ok'])
        ml.rebuild_state(ROOM)
        self.assertEqual(ml.compact_text(cid, ROOM), 'Original complete recap.')

if __name__ == '__main__': unittest.main()
