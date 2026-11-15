"""New notice ownership survives the real archive/API and never becomes agent speech."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import runner
import transport
from localharness import product_notices as notices,ui_language
from deskd import readers

RAW='Literal Русский 中文 **{agent}** </script>'


class ProductNotices(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.tree=Path(temporary.name)
        self.life=Mock();self.desk=transport.Desk(self.tree,'window','Owner','Hélène',memory_life=self.life,agent_name=RAW)

    def test_actual_transport_and_api_preserve_recipes_and_literal_values_for16_axes(self):
        for code,fields in notices.FIELDS.items():
            values={name:RAW for name in fields};recipe=notices.recipe(code,**values);before=copy.deepcopy(recipe)
            notices.deliver(self.desk,code,**values)
            self.assertEqual(recipe,before)
            row=self.desk.rows()[-1];self.assertEqual(row['display_notice'],recipe);self.assertTrue(row['system'])
            self.assertEqual(row['text'],notices.legacy(recipe))
            if values:self.assertIn(RAW,row['text'])
            for ui in ('ru','en','fr','zh'):
                for frame in ('ru','en','fr','zh'):
                    (self.tree/'helene.json').write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf8')
                    rendered=ui_language.text('notice.'+code,ui,values)
                    if values:self.assertIn(RAW,rendered)
                    self.assertEqual(self.desk.rows()[-1],row)
        with patch.dict(os.environ,HELENE_TREE=str(self.tree)):
            self.assertEqual(readers.chat_tail('window'),self.desk.rows())
        self.assertEqual(self.desk.lines(),[]);self.life.record_event.assert_not_called()

    def test_invalid_metadata_cannot_write_or_mark_an_authored_row_as_owned(self):
        valid=notices.recipe('silence',agent=RAW)
        invalid=[dict(valid,extra=True),dict(valid,kind='unknown'),dict(valid,values={}),
                 dict(valid,values={'agent':[]}),dict(valid,values={'agent':float('nan')}),dict(valid,schema='unknown')]
        for value in invalid:
            with self.assertRaises(ValueError):self.desk.archive(RAW,outgoing=True,system=True,display_notice=value)
        with self.assertRaises(ValueError):self.desk.deliver(RAW,display_notice=valid)
        with self.assertRaises(ValueError):self.desk.archive(RAW,outgoing=False,display_notice=valid)
        self.assertEqual(self.desk.rows(),[])
        self.desk.archive(notices.legacy(valid),outgoing=False)
        self.assertNotIn('display_notice',self.desk.rows()[0]);self.assertEqual(len(self.desk.lines()),1)

    def test_actual_speech_unknown_does_not_claim_audio_was_not_delivered(self):
        with patch.object(runner,'_room',return_value=self.desk):
            runner.speech_failed({'chat_id':'window','id':'literal-id'},'unknown')
            runner.speech_failed({'chat_id':'window','id':'literal-id'},'failed')
        rows=self.desk.rows();self.assertEqual([r['display_notice']['kind'] for r in rows],['speech_unknown','speech_failed'])
        self.assertIn('могло быть доставлено',rows[0]['text']);self.assertNotIn('не доставлена',rows[0]['text'])
        self.assertEqual(self.desk.lines(),[]);self.life.record_event.assert_not_called()

    def test_busy_owner_notice_is_once_per_holder_and_cannot_recommend_unknown_process_kill(self):
        config={'agent':{'name':RAW,'frame_language':'zh'},'owner':{'name':'Owner'},'lang':'fr'}
        runner._say_tree_is_busy(self.tree,config,{'pid':876543,'host':'fixture'})
        runner._say_tree_is_busy(self.tree,config,{'pid':876543,'host':'fixture'})
        rows=self.desk.rows();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['display_notice'],notices.recipe('tree_busy',agent=RAW,pid='876543'))
        self.assertNotIn('заверши процесс',rows[0]['text']);self.assertEqual(self.desk.lines(),[])

    def test_rejected_ingress_is_product_notice_not_owner_or_model_words(self):
        path=self.tree/'forged.md';path.write_text(RAW,encoding='utf8')
        with patch.object(runner,'_desk',self.desk):runner._reject_note(path,RAW)
        rows=self.desk.rows();self.assertEqual(len(rows),1);self.assertTrue(rows[0]['system'])
        self.assertEqual(rows[0]['display_notice'],notices.recipe('note_rejected',name=path.name,reason=RAW))
        self.assertEqual(self.desk.lines(),[])


if __name__=='__main__':unittest.main()
