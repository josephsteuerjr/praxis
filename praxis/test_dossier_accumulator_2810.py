import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frame_dossiers
import frame_epoch


def msg(text):
    return dict(role='user', content=text)


def card(source='nf', text='FULL NF DOSSIER'):
    return dict(source='memory/people/'+source+'.md',
                body_sha256=hashlib.sha256(text.encode()).hexdigest(),
                message=msg('<praxis_context_evidence>'+text+'</praxis_context_evidence>'))


class Accumulator(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=Path(tmp.name)
        p=patch.object(frame_epoch,'STORE',self.store);p.start();self.addCleanup(p.stop)
        self.ctx=SimpleNamespace(chat_id='-10042',is_dm=False)
        self.selection=dict(chat='-10042',n=1,boundary='fold1',scope={'room':42},
                            flip_id=None,summary_policy={},rows=[{'line':'one'}],
                            complete=True,capacity={'within':True})
        self.write()

    def write(self,**extra):
        old=frame_epoch.load(self.ctx.chat_id)or{}
        old.update(tape=copy.deepcopy(self.selection),**extra)
        frame_epoch._write_atomic(frame_epoch.path_for(self.ctx.chat_id),json.dumps(old))

    def project(self,history,additions=(),private=True):
        with patch.object(frame_epoch,'bound_tape',return_value=copy.deepcopy(self.selection)):
            return frame_dossiers.project(ctx=self.ctx,messages=[msg('FROZEN')]+history+[msg('CURRENT')],
                                          additions=additions,private_in_rooms=private)

    def advance(self):
        self.selection['rows'].append({'line':str(len(self.selection['rows']))});self.write()

    def test_admit_once_then_keep_after_roster_disappears_and_restart(self):
        first=self.project([msg('A')],[card()]);self.advance()
        # A new process has no in-memory dossier state: projection reads epoch JSON.
        later=self.project([msg('A'),msg('B')])
        self.assertEqual(first[:-1],later[:len(first)-1])
        self.assertEqual(later,[msg('FROZEN'),msg('A'),card()['message'],msg('B'),msg('CURRENT')])
        again=self.project([msg('A'),msg('B')],[card()])
        self.assertEqual(again,later)
        self.assertEqual(len(frame_epoch.load(self.ctx.chat_id)['dossier_accumulator']['entries']),1)

    def test_new_dossier_appends_after_old_history_not_before_old_material(self):
        first=self.project([msg('A')],[card()]);self.advance()
        second=self.project([msg('A'),msg('B')],[card('other','SECOND')])
        self.assertEqual(second[:len(first)-1],first[:-1])
        self.assertEqual(second[-3:],[msg('B'),card('other','SECOND')['message'],msg('CURRENT')])

    def test_changed_card_appends_revision_without_rewriting_prior_bytes(self):
        first=self.project([msg('A')],[card()]);self.advance()
        second=self.project([msg('A'),msg('B')],[card(text='NEW NF')])
        self.assertEqual(second[:len(first)-1],first[:-1])
        self.assertIn(card()['message'],second);self.assertIn(card(text='NEW NF')['message'],second)

    def test_fold_retires_accumulation(self):
        self.project([msg('A')],[card()]);self.selection['n']=2;self.selection['boundary']='fold2';self.write()
        self.assertEqual(self.project([msg('AFTER FOLD')]),[msg('FROZEN'),msg('AFTER FOLD'),msg('CURRENT')])

    def test_scope_and_privacy_changes_do_not_import_previous_audience(self):
        self.project([msg('A')],[card(text='PRIVATE')])
        self.assertNotIn(card(text='PRIVATE')['message'],self.project([msg('A')],private=False))
        self.selection['scope']={'room':43};self.write()
        self.assertNotIn(card(text='PRIVATE')['message'],self.project([msg('A')]))

    def test_old_wake_cannot_see_future_material_or_roll_back_store(self):
        old=copy.deepcopy(self.selection);self.advance();self.project([msg('A'),msg('B')],[card()])
        before=frame_epoch.path_for(self.ctx.chat_id).read_bytes();self.selection=old
        result=self.project([msg('A')],[card('old','OLD WAKE')])
        self.assertNotIn(card()['message'],result)
        self.assertEqual(frame_epoch.path_for(self.ctx.chat_id).read_bytes(),before)

    def test_previous_boundary_uses_its_detached_snapshot(self):
        self.project([msg('A')],[card()]);old=copy.deepcopy(self.selection)
        old['dossier_accumulator']=frame_epoch.load(self.ctx.chat_id)['dossier_accumulator']
        self.selection['n']=2;self.selection['boundary']='fold2';self.write()
        self.project([msg('X')],[card('new','FUTURE')]);before=frame_epoch.path_for(self.ctx.chat_id).read_bytes()
        self.selection=old;result=self.project([msg('A')])
        self.assertIn(card()['message'],result);self.assertNotIn(card('new','FUTURE')['message'],result)
        self.assertEqual(frame_epoch.path_for(self.ctx.chat_id).read_bytes(),before)

    def test_write_failure_is_not_silent_loss(self):
        with patch.object(frame_epoch,'_write_atomic',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.project([msg('A')],[card()])

    def test_no_mutation_of_source_rows_or_messages(self):
        before=copy.deepcopy(self.selection);history=[msg('A')];expected=copy.deepcopy(history)
        self.project(history,[card()]);self.assertEqual(history,expected);self.assertEqual(self.selection,before)

    def test_dm_is_unchanged(self):
        self.ctx.is_dm=True
        self.assertEqual(self.project([msg('A')],[card()]),[msg('FROZEN'),msg('A'),msg('CURRENT')])

    def test_ingress_advancing_tape_does_not_prevent_admission_by_current_model_turn(self):
        selected=copy.deepcopy(self.selection)
        self.advance()
        self.selection=selected
        self.project([msg('A')],[card()])
        self.assertEqual(len(frame_epoch.load(self.ctx.chat_id)['dossier_accumulator']['entries']),1)
        self.assertEqual(len(frame_epoch.load(self.ctx.chat_id)['tape']['rows']),2)

    def test_epoch_builder_routes_missing_dossier_into_accumulator_not_current_packet(self):
        import agent
        import frame_layout
        frame_layout.reset();self.addCleanup(frame_layout.reset)
        body='— nf · memory/people/nf.md —\nFULL NF DOSSIER'
        frame_layout.stash(epoch_dossier_cards={'nf':body},epoch_dossier_speaker='owner')
        receipt=dict(n=1,anchor=1,frozen_at='2026-10-28',chars=6,reason='reused',
                     frozen_reason='first',drift={})
        chosen=[]
        with frame_epoch.bind(self.ctx.chat_id,1,tape=self.selection), \
             patch.object(frame_epoch,'serve',return_value=([('BASE','json','FROZEN')],receipt)):
            agent._epoch_serve(self.ctx.chat_id,1,[],chosen)
            additions=frame_layout.snapshot()['epoch_dossier_additions']
        self.assertNotIn('FULL NF DOSSIER',''.join(chosen))
        self.assertEqual(len(additions),1)
        first=self.project([msg('A')],additions)
        self.advance();second=self.project([msg('A'),msg('B')])
        self.assertEqual(first[:-1],second[:len(first)-1])
        self.assertEqual(json.dumps(second).count('FULL NF DOSSIER'),1)

    def test_alternative_capture_keeps_dossier_visible_in_its_existing_packet(self):
        import agent
        import frame_layout
        frame_layout.reset();self.addCleanup(frame_layout.reset)
        frame_layout.stash(epoch_dossier_cards={'nf':'memory/people/nf.md —\nFULL NF'},
                           epoch_dossier_speaker='nf')
        receipt=dict(n=1,anchor=1,frozen_at='2026-10-28',chars=6,reason='reused',frozen_reason='first',drift={})
        chosen=[]
        with patch.dict(os.environ,PRAXIS_KEAT_CAPTURE='on'), \
             frame_epoch.bind(self.ctx.chat_id,1,tape=self.selection), \
             patch.object(frame_epoch,'serve',return_value=([('BASE','json','FROZEN')],receipt)):
            agent._epoch_serve(self.ctx.chat_id,1,[],chosen)
        self.assertIn('FULL NF',''.join(chosen))
        self.assertFalse(frame_layout.snapshot().get('epoch_dossier_additions'))

    def test_append_continuation_keeps_whole_prior_input_and_new_dossiers_once(self):
        import context_policy
        import dialogue_projection
        import frame_layout
        from test_frame_continuation import ContinuationTests,row
        fixture=ContinuationTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        nf=msg(frame_dossiers.OPEN+'NF SNAPSHOT\n</praxis_context_evidence>')
        other=msg(frame_dossiers.OPEN+'OTHER SNAPSHOT\n</praxis_context_evidence>')
        def fresh(selected,docs,label):
            history,current=dialogue_projection.epoch_dialogue(selected['rows'])
            return [msg('frozen epoch E')]+frame_layout.tape(history)+docs+[msg(label+'\n'+current)]
        with patch.object(context_policy,'read',return_value=dict(context_policy.LEGACY)):
            first=fixture.prepare(messages=fresh(fixture.selection,[nf],'STATE ONE'))
            fixture.commit(first)
            selected=fixture.next_selection()
            second=fixture.prepare(selected,messages=fresh(selected,[nf,other],'STATE TWO'))
            self.assertEqual(second.messages[:len(first.messages)],first.messages)
            self.assertEqual(second.messages.count(nf),1);self.assertEqual(second.messages.count(other),1)
            fixture.commit(second,selected)
            selected=copy.deepcopy(selected);selected['rows'] += [row(7,True,'second delivered'),row(8,False,'third question')]
            third=fixture.prepare(selected,messages=fresh(selected,[nf,other],'STATE THREE'))
            self.assertEqual(third.messages[:len(second.messages)],second.messages)
            self.assertEqual(third.messages.count(nf),1);self.assertEqual(third.messages.count(other),1)

    def test_user_quote_cannot_masquerade_as_document_admission(self):
        fake=msg('> '+frame_dossiers.OPEN+'invented')
        self.assertEqual(frame_dossiers.newly_admitted_messages([], [fake,msg('CURRENT')]),[])


if __name__=='__main__':unittest.main()
