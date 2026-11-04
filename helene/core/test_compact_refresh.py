"""Former skipped V1 auto-refresh scenarios, ported to acting-author contracts.

The automatic model planner/dry_run/price/quiet-timer API is retired, not made
compatible. Exact debt, bounded source selection, immutable audit, edited/deleted
sources, progressive successors and refusal/idempotence remain executable here.
See EDITION-CONTRACTS-230.md for each removed oracle and its replacement.
"""
import unittest
from unittest import mock
import memory_life as ml
import memory_provenance
import test_coverage_vs_current as coverage

ROOM = coverage.ROOM


class AuthoredRefreshContracts(coverage._AuthoredRefreshBase):
    def scene(self, count=8, edit_at=3):
        rows = [self._msg(i, 'original ' + str(i)) for i in range(1, count+1)]
        old = self._compact(rows, summary='STALE original ' + str(edit_at))
        if edit_at: self._edit(edit_at, 'current edited source', ts=70000)
        return rows, old

    def refresh(self, old, text='authored current recap', limit=50):
        prepared = self._prepare(old['id'], limit=limit)
        return prepared, self._apply(prepared, text)

    def test_stale_debt_closes_only_after_authored_text(self):
        _, old=self.scene()
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],1)
        prepared=self._prepare(old['id'])
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],1)
        self._apply(prepared,'current authored recap')
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)
        self.assertEqual(self.helper_calls,[])

    def test_old_bytes_survive_and_successor_is_distinct(self):
        _,old=self.scene(); before=self._files()
        _,result=self.refresh(old)
        self.assertNotEqual(result['compact_id'],old['id'])
        self.assertEqual(self._files()[old['id']+'.md'],before[old['id']+'.md'])
        self.assertIn(result['compact_id']+'.md',self._files())

    def test_only_new_recap_reaches_frame(self):
        _,old=self.scene()
        self.assertNotIn('authored current recap',ml.context_summary(ROOM))
        self.refresh(old)
        self.assertIn('authored current recap',ml.context_summary(ROOM))
        self.assertNotIn('STALE',ml.context_summary(ROOM))

    def test_current_sources_never_reintroduce_superseded_body(self):
        rows,old=self.scene()
        prepared=self._prepare(old['id'])
        shown='\n'.join(r['text'] for r in prepared['sources'])
        self.assertIn('current edited source',shown)
        self.assertNotIn('original 3',shown)
        self.assertNotIn(rows[2]['id'],[r['id'] for r in prepared['sources']])
        self._apply(prepared,'authored current recap')
        self.assertEqual(self.helper_calls,[])

    def test_no_current_event_disappears_or_becomes_hot_twice(self):
        _,old=self.scene(); self.refresh(old)
        state=ml.rebuild_state(ROOM)
        _,coverage_graph,_=ml._both_graphs(ROOM)
        covered={str(e) for metadata,_ in coverage_graph.values() for e in metadata.get('source_event_ids',[])}
        hot={str(r['id']) for r in state['hot']}
        current={str(r['id']) for r in memory_provenance.current_conversation_events(
            ml.iter_events(chat_id=ROOM,kinds={'conversation_message'}))}
        self.assertFalse(covered & hot)
        self.assertFalse(current-covered-hot)

    def test_deleted_body_is_absent_from_prepare_and_frame_but_audit_remains(self):
        rows=[self._msg(1,'private deleted original'),self._msg(2,'survivor')]
        old=self._compact(rows,summary='STALE private deleted original'); before=self._files()
        self._delete(1,ts=71000)
        prepared=self._prepare(old['id'])
        self.assertNotIn('private deleted original',str(prepared['sources']))
        self._apply(prepared,'after deletion')
        self.assertNotIn('private deleted original',ml.context_summary(ROOM))
        self.assertEqual(self._files()[old['id']+'.md'],before[old['id']+'.md'])
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)

    def test_legacy_refresh_explicitly_refuses_without_generation_or_writes(self):
        _,old=self.scene(); files,receipts=self._files(),self._receipts()
        result=ml.refresh_compacts(ROOM,old['id'])
        self.assertEqual(result,{'ok':False,'reason':'acting_author_required','chunks_written':0})
        self.assertEqual(self._files(),files); self.assertEqual(self._receipts(),receipts)
        self.assertEqual(self.helper_calls,[])
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],1)

    def test_preparation_persists_selection_without_new_compact_or_helper_price(self):
        _,old=self.scene(); files,receipts=self._files(),self._receipts()
        prepared=self._prepare(old['id'],limit=2)
        self.assertEqual(len(prepared['sources']),2)
        self.assertGreater(prepared['remaining_unselected'],0)
        self.assertTrue(prepared['shown_complete'])
        self.assertTrue((ml.STATE_DIR/'authored'/(prepared['selection']+'.json')).is_file())
        self.assertEqual(self._files(),files); self.assertEqual(self._receipts(),receipts)
        self.assertEqual(self.helper_calls,[])

    def test_empty_author_never_pays_or_closes_debt_and_can_retry_explicitly(self):
        _,old=self.scene(); files,receipts=self._files(),self._receipts()
        prepared=self._prepare(old['id'])
        denied=ml.apply_authored_fold(ROOM,prepared['selection'],'')
        self.assertFalse(denied['ok']); self.assertEqual(denied['folded'],0)
        self.assertEqual(self._files(),files); self.assertEqual(self._receipts(),receipts)
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],1)
        self._apply(prepared,'explicitly authored retry')
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)
        self.assertEqual(self.helper_calls,[])

    def test_row_quota_progresses_in_bounded_authored_chunks(self):
        rows=[self._msg(i,'large source '+str(i)+' '+('я'*900)) for i in range(1,7)]
        old=self._compact(rows); self._edit(3,'changed large source',ts=72000)
        remaining=[]; new=[]
        for i in range(3):
            prepared,result=self.refresh(old,'chunk '+str(i),limit=2)
            self.assertEqual(len(prepared['sources']),2)
            remaining.append(result['remaining_count']); new.append(result['compact_id'])
        self.assertEqual(remaining,[4,2,0]); self.assertEqual(len(set(new)),3)
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)
        self.assertEqual(self.helper_calls,[])

    def test_above_character_budget_leaf_retains_all_sources_across_actual_chunks(self):
        # Text alone is 54,000 chars; full source rows exceed the actual 48,000
        # serialization budget even with a permissive 100-row quota.
        rows=[self._msg(i,'sized source '+str(i)+' '+('я'*900)) for i in range(1,61)]
        old=self._compact(rows); self._edit(3,'edited source '+('я'*900),ts=72500)
        current={str(r['id']) for r in memory_provenance.current_conversation_events(
            ml.iter_events(chat_id=ROOM,kinds={'conversation_message'}))}
        selected=[]; successors=[]
        for step in range(60):
            prepared=self._prepare(old['id'],limit=100)
            if step==0:
                self.assertLess(len(prepared['sources']),60)
                self.assertGreater(prepared['remaining_unselected'],0)
            selected.extend(str(r['id']) for r in prepared['sources'])
            result=self._apply(prepared,'size-bounded authored chunk '+str(step))
            successors.append(result['compact_id'])
            if result['remaining_count']==0: break
        self.assertGreaterEqual(len(successors),2)
        self.assertEqual(len(set(successors)),len(successors))
        self.assertEqual(len(selected),len(set(selected)))
        self.assertEqual(set(selected),current)
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)
        self.assertEqual(self.helper_calls,[])

    def test_parent_is_repaired_from_current_sources_not_generated_from_old_recap(self):
        rows=[self._msg(i,'source '+str(i)) for i in range(1,5)]
        kids=[self._compact(rows[:2],summary='old child one'), self._compact(rows[2:],summary='old child two')]
        parent=ml._write_compact(ROOM,{'summary':'old parent','open_threads':[],'claims':[],'episodes':[]},
            tier=2,depth=2,source_events=[],source_compacts=[k['id'] for k in kids],
            event_count=4,continued=False,first_ts=str(rows[0]['ts']),last_ts=str(rows[-1]['ts']))
        self._edit(1,'new first source',ts=73000)
        prepared=self._prepare(parent['id'])
        self.assertNotIn('old parent',str(prepared['sources']))
        self.assertNotIn('old child',str(prepared['sources']))
        self._apply(prepared,'new authored parent')
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)
        self.assertEqual(self.helper_calls,[])

    def test_consumed_selection_cannot_rewrite_or_duplicate_work(self):
        _,old=self.scene(); prepared,_=self.refresh(old)
        files,receipts=self._files(),self._receipts()
        result=ml.apply_authored_fold(ROOM,prepared['selection'],'duplicate')
        self.assertFalse(result['ok']); self.assertEqual(result['folded'],0)
        self.assertEqual(self._files(),files); self.assertEqual(self._receipts(),receipts)

    def test_debt_distinguishes_known_clean_unknown_and_exact_stale_counts(self):
        self.scene(edit_at=0)
        clean=ml.refresh_debt(ROOM); unknown=ml.refresh_debt('-1009999999999')
        # Current API reports evidence counts, not the retired V1 known/tree
        # certificate. Absence of compact evidence must not certify known-clean.
        self.assertGreater(clean['covered'],0); self.assertEqual(clean['needs_refresh'],0)
        self.assertEqual(unknown['covered'],0); self.assertEqual(unknown['presentable'],0)
        self.assertNotEqual(unknown.get('known'),True)
        self._edit(3,'newest',ts=74000)
        debt=ml.refresh_debt(ROOM)
        self.assertEqual(debt['needs_refresh'],1); self.assertEqual(debt['unresolved_group_count'],1)
        self.assertEqual(len(debt['sample']),1)
        self.assertEqual(debt['sample'][0]['superseded_total'],1)
        self.assertEqual(debt['sample'][0]['target_count'],8)


if __name__=='__main__': unittest.main()
