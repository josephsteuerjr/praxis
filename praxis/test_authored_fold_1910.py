"""Acting-turn episodic recap: exact selected sources, no auxiliary generation."""
import unittest
from unittest import mock
import memory_life as ml
from test_coverage_vs_current import Base, ROOM

class AuthoredFold(Base):
    def test_two_large_tier_sources_are_complete_not_permanently_stalled(self):
        self.seed()
        rows = [{'id': f'compact-{n}', 'text': 'x' * 30000} for n in range(2)]
        candidate = {'inputs': rows, 'sources': [{'id': r['id'], 'depth': 1} for r in rows], 'tier': 1}
        with mock.patch.object(ml, 'plan_hot_fold', return_value={'fold': 0}), \
             mock.patch.object(ml, '_refresh_snapshot', return_value={'unresolved_groups': []}), \
             mock.patch.object(ml, '_tier_fold_candidate', return_value=candidate):
            out = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['sources'], rows)
        self.assertTrue(out['shown_complete'])
        self.assertTrue(out['over_size_hint'])

    def test_completed_refresh_target_never_selects_unrelated_hot(self):
        self.seed()
        group = {'target_rows': [], 'covered_positions': set(), 'receipt_complete': True}
        with mock.patch.object(ml, '_refresh_snapshot', return_value={'artifact_group': {'old': 'g'}}), \
             mock.patch.object(ml, '_refresh_group', return_value=group):
            out = ml.prepare_authored_fold(ROOM, compact_id='old')
        self.assertEqual(out['reason'], 'already_refreshed')
        self.assertEqual(out['sources'], [])

    def test_explicit_unknown_refresh_target_never_falls_back_to_hot(self):
        self.seed()
        out = ml.prepare_authored_fold(ROOM, compact_id='missing-compact')
        self.assertFalse(out['ok'])
        self.assertEqual(out['reason'], 'no_refresh_group_for_target')
        self.assertEqual(out['sources'], [])

    def test_soft_pressure_offers_even_when_episode_is_open(self):
        self.seed()
        with mock.patch.object(ml, 'hot_bounds', return_value=(2, 5, 20, 1)), \
             mock.patch.object(ml, 'plan_hot_fold', return_value={'due': False, 'reason': 'open_episode'}), \
             mock.patch.object(ml, '_note_fold_offer') as offer:
            out = ml.compact_if_due(ROOM)
        self.assertTrue(out['offered'])
        self.assertEqual(out['folded'], 0)
        self.assertFalse(offer.call_args.kwargs['hard'])

    def test_night_preserves_offers_without_an_auxiliary_writer(self):
        import night_memory
        with mock.patch.object(ml, '_model_compact', side_effect=AssertionError('helper forbidden')):
            out = night_memory.resolve_fold_offers({ROOM: {'place': ROOM}})
        self.assertTrue(out['author_required'])
        self.assertEqual(out['accepted'], 0)

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

    def test_only_successful_author_commit_clears_offer(self):
        selected = self.seed()
        ml._fold_offers_write({ROOM: {'place': ROOM}})
        self._edit(1, 'changed before commit')
        refused = ml.apply_authored_fold(ROOM, selected['selection'], 'retained proposal')
        self.assertFalse(refused['ok'], refused)
        self.assertIn(ROOM, ml.fold_offers())
        current = ml.prepare_authored_fold(ROOM, limit=3)
        accepted = ml.apply_authored_fold(ROOM, current['selection'], 'Current authored recap.')
        self.assertTrue(accepted['ok'], accepted)
        self.assertNotIn(ROOM, ml.fold_offers())

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
