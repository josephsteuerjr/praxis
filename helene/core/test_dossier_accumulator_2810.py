"""Dossier admission at Hélène's FIRST source boundary, rather than Praxis epoch storage.

The inherited epoch.bind/tape accumulator API is not the edition's source of
authority. These cases retain admission/restart/audience/revision/corruption
coverage at bind_source and the actual dossier cache, including supported DMs.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
import frame_continuation as continuation
import frame_dossiers as dossiers
import frame_layout


def msg(text):return {'role':'user','content':text}
def card(source='literal',text='FULL DOSSIER'):
    return {'source':'memory/people/'+source+'.md','body_sha256':continuation._sha(text),
            'message':msg(dossiers.OPEN+text+'\n</helene_context_evidence>')}


class Accumulator(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.source=self.root/'home';(self.source/'memory/people').mkdir(parents=True)
        self.enterContext(patch.dict(os.environ,PRAXIS_FRAME_CONTINUATION='on',PRAXIS_KEAT='off',PRAXIS_KEAT_CAPTURE='off'))
        self.ctx=agent.ChannelContext(chat_id='fixture',room_id='fixture',is_dm=False,principal_id='123')
        self.selection={'source':str(self.source),'generation':{'n':1,'boundary':'fold1'},
                        'admission':{'owner':'fixture-owner'},'chat':'fixture','cache_root':str(self.root/'cache')}
        frame_layout.begin();self.addCleanup(frame_layout.reset)

    def project(self,history=(),additions=(),private=True,selection=None):
        selected=selection or self.selection
        with continuation.bind_source(selected,lambda:copy.deepcopy(selected)):
            return dossiers.project(ctx=self.ctx,messages=[msg('FROZEN'),*history,msg('CURRENT')],
                                    additions=additions,private_in_rooms=private)

    def test_admission_survives_restart_and_roster_disappearance(self):
        first=self.project([msg('A')],[card()])
        later=self.project([msg('A'),msg('B')])
        self.assertEqual(first,[msg('FROZEN'),msg('A'),card()['message'],msg('CURRENT')])
        self.assertEqual(later,[msg('FROZEN'),msg('A'),msg('B'),card()['message'],msg('CURRENT')])
        self.assertEqual(self.project([msg('A'),msg('B')],[card()]),later)

    def test_new_versions_keep_prior_snapshot_bytes_and_admission_order(self):
        self.project(additions=[card()]);result=self.project(additions=[card(text='NEW'),card('other','SECOND')])
        self.assertEqual(result[1:-1],[card()['message'],card(text='NEW')['message'],card('other','SECOND')['message']])

    def test_generation_change_does_not_import_prior_versions(self):
        self.project(additions=[card()]);self.selection['generation']={'n':2,'boundary':'fold2'}
        self.assertEqual(self.project(),[msg('FROZEN'),msg('CURRENT')])

    def test_privacy_change_does_not_import_prior_audience(self):
        self.project(additions=[card(text='PRIVATE')])
        self.assertNotIn(card(text='PRIVATE')['message'],self.project(private=False))

    def test_principal_and_room_admissions_are_distinct(self):
        self.project(additions=[card()]);self.ctx=agent.ChannelContext(chat_id='fixture',room_id='fixture',is_dm=False,principal_id='999')
        self.assertNotIn(card()['message'],self.project())

    def test_owner_installation_change_does_not_import_prior_material(self):
        self.project(additions=[card()]);self.selection['admission']={'owner':'new-owner'}
        self.assertNotIn(card()['message'],self.project())

    def test_prior_boundary_can_read_its_own_snapshot_without_loading_future_material(self):
        self.project(additions=[card()]);old=copy.deepcopy(self.selection)
        self.selection['generation']={'n':2,'boundary':'fold2'};self.project(additions=[card('other','FUTURE')])
        result=self.project(selection=old)
        self.assertIn(card()['message'],result);self.assertNotIn(card('other','FUTURE')['message'],result)

    def test_write_failure_is_explicit(self):
        with patch.object(continuation,'_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.project(additions=[card()])

    def test_corruption_keeps_original_and_refuses_projection(self):
        self.project(additions=[card()]);path=next((self.root/'cache').glob('*.dossiers.*.json'))
        payload=json.loads(path.read_text());payload['entries'][0]['message']['content']='FORGED'
        path.write_text(json.dumps(payload));before=path.read_bytes()
        with self.assertRaises(ValueError):self.project()
        self.assertEqual(path.read_bytes(),before)

    def test_inputs_are_not_mutated(self):
        history=[msg('A')];addition=card();selected=copy.deepcopy(self.selection)
        before=copy.deepcopy((history,addition));self.project(history,[addition])
        self.assertEqual((history,addition),before);self.assertEqual(self.selection,selected)

    def test_unbound_input_is_unchanged_and_dm_uses_its_own_supported_boundary(self):
        messages=[msg('A'),msg('CURRENT')]
        with continuation.bind_source(None,lambda:None):
            self.assertIs(dossiers.project(ctx=self.ctx,messages=messages,additions=[card()],private_in_rooms=True),messages)
        self.ctx=agent.ChannelContext(chat_id='fixture',is_dm=True,owner=True,principal_id='123')
        self.assertIn(card()['message'],self.project(additions=[card()]))

    def test_deleted_source_is_revoked_without_rewriting_retired_bytes(self):
        path=self.source/'memory/people/literal.md';path.write_text('SOURCE')
        self.project(additions=[card()]);path.unlink();result=self.project()
        self.assertNotIn(card()['message'],result)
        saved=json.loads(next((self.root/'cache').glob('*.dossiers.*.json')).read_text())
        self.assertEqual(saved['retired_entries'][0]['message'],card()['message'])
        self.assertIn('memory/people/literal.md',saved['revoked_sources'])

    def test_collect_only_admitted_source_and_keeps_literal_body(self):
        body='Authored Русский 中文';self.assertFalse(dossiers.collect('memory/people/literal.md',body))
        with continuation.bind_source(self.selection,lambda:copy.deepcopy(self.selection)):
            self.assertTrue(dossiers.collect('memory/people/literal.md',body))
        entry=frame_layout.snapshot()['dossier_additions'][0]
        self.assertEqual(entry['message']['content'],dossiers.OPEN+body+'\n</helene_context_evidence>')

    def test_disabled_capture_does_not_claim_admission(self):
        with continuation.bind_source(self.selection,lambda:copy.deepcopy(self.selection)),patch.dict(os.environ,PRAXIS_KEAT_CAPTURE='on'):
            self.assertFalse(dossiers.collect('memory/people/literal.md','PRIVATE'))

    def test_user_quote_cannot_masquerade_as_admitted_document(self):
        self.assertEqual(dossiers.newly_admitted_messages([], [msg('> '+dossiers.OPEN+'invented'),msg('CURRENT')]),[])
