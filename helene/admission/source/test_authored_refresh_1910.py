"""Current-source authored repair preserves immutable stale originals and debt receipts."""
import unittest
from unittest import mock
import memory_life as ml
from test_coverage_vs_current import Base, ROOM

class Refresh(Base):
    def test_edit_delete_nested_and_bounded_restart(self):
        rows=[self._msg(i, f'original {i}') for i in range(1,7)]
        old=self._compact(rows, summary='old immutable recap')
        path=ml.BASE/old['path']; before=path.read_bytes()
        latest=self._edit(3,'LATEST replacement',ts=60001)
        with mock.patch.object(ml,'_model_compact',side_effect=AssertionError('hidden helper')):
            selection=ml.prepare_authored_fold(ROOM,compact_id=old['id'],limit=2)
            self.assertEqual(selection['kind'],'refresh')
            out=ml.apply_authored_fold(ROOM,selection['selection'],'First authored current portion.')
            self.assertTrue(out['ok'],out); self.assertGreater(out['remaining_count'],0)
            ml.rebuild_state(ROOM)
            for _ in range(5):
                selection=ml.prepare_authored_fold(ROOM,compact_id=old['id'],limit=2)
                if not selection.get('sources'): break
                out=ml.apply_authored_fold(ROOM,selection['selection'],'Remaining authored current portion.')
                self.assertTrue(out['ok'],out)
                if out['remaining_count']==0: break
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(ml.refresh_debt(ROOM)['needs_refresh'],0)
        self.assertNotIn('old immutable recap',ml.context_summary(ROOM))

    def test_changed_refresh_selection_refuses_without_rewriting_old(self):
        rows=[self._msg(i,f'old {i}') for i in range(1,4)]
        old=self._compact(rows,summary='immutable')
        self._edit(1,'first edit',ts=60001)
        selected=ml.prepare_authored_fold(ROOM,compact_id=old['id'])
        ml.record_message(ROOM, 'Николай: second edit', actor='Николай', direction='in',
                          source_id='1:edit:2', ts=60002, dedupe_key='second-edit')
        ml.note_message_revision(ROOM, 1, 'Николай: second edit')
        result=ml.apply_authored_fold(ROOM,selected['selection'],'stale authored text')
        self.assertFalse(result['ok']); self.assertEqual(result['folded'],0)
        self.assertEqual(result['retained_text'],'stale authored text')

if __name__=='__main__': unittest.main()
