"""Read-time captions never rewrite historical delivery or authored material."""
import ast
import copy
import datetime
from pathlib import Path
import unittest
import frame_language
from test_state_language_1511 import functions,RAW


def renderers():
    tree=ast.parse((Path(__file__).parent/'turns.py').read_text(encoding='utf-8'))
    def constant(name):
        node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id==name for x in n.targets))
        return ast.literal_eval(node.value)
    return functions('turns.py',['_no_addressee','_trace_sent_out','_owner_ru','_cut_phrase','format_line'],
        _dt=datetime,_DELIVERY_VERB=constant('_DELIVERY_VERB'),_OWNER_RU=constant('_OWNER_RU'),
        _SEND_MARKERS=constant('_SEND_MARKERS'),OUT_SHOW_CHARS=constant('OUT_SHOW_CHARS'),
        CUT_MAX=constant('CUT_MAX'),CUT_LINE_CHARS=constant('CUT_LINE_CHARS'))


class TurnLanguage(unittest.TestCase):
    def test_all_delivery_outcomes_and_authored_text_survive_each_language(self):
        built=renderers()
        for language in frame_language.LANGUAGES:
            for status in ('authored','accepted','partial','superseded','failed','blocked'):
                with self.subTest(language=language,status=status),frame_language.use(language):
                    row={'ts':1,'kind':'chat','scope':'owner','title':RAW,'in':RAW,'out':RAW,
                         'note':RAW,'delivery':status,'tools':['RAW_TOOL '+RAW],'cut':[RAW]}
                    before=copy.deepcopy(row);line=built.format_line(row)
                    self.assertIn(frame_language.text('turns.delivery.'+status),line)
                    self.assertEqual(line.count(RAW),6)
                    self.assertEqual(row,before)
                    if language!='ru':
                        self.assertNotIn('сказано',line.replace(RAW,''))
                        self.assertNotIn('вошло',line.replace(RAW,''))

    def test_self_note_silence_and_truncation_remain_different(self):
        built=renderers()
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                row={'ts':1,'kind':'task_window','chat_id':None,'out':RAW,'telegram':'connected'}
                before=copy.deepcopy(row)
                line=built.format_line(row)
                self.assertIn(RAW,line);self.assertNotIn(frame_language.text('turns.delivery.accepted')+':',line)
                self.assertEqual(row,before)
                for held in ('voice','privacy','evaluator','anti-repeat','drift','error','empty','unspoken'):
                    row={'ts':1,'kind':'chat','held':held,'why':RAW}
                    before=copy.deepcopy(row);line=built.format_line(row)
                    self.assertEqual(row,before)
                    self.assertNotIn(frame_language.text('turns.delivery.accepted')+':',line)
                long={'ts':1,'kind':'chat','out':RAW*100,'delivery':'authored'}
                before=copy.deepcopy(long);line=built.format_line(long)
                self.assertLess(len(line),len(long['out']));self.assertEqual(long,before)
                if language!='ru':self.assertNotIn('обрезано',line.replace(RAW,''))

    def test_new_truncation_phrases_keep_the_role_and_model(self):
        built=renderers()
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                mark={'model':'RAW-MODEL/日本語','chars':123}
                for role in ('voice','evaluator'):
                    result=built._cut_phrase(role,mark)
                    self.assertIn(frame_language.text('turns.role.'+role),result)
                    self.assertIn(mark['model'],result);self.assertIn('123',result)
                    if language!='ru':self.assertNotRegex(result,'[А-Яа-яЁё]')


if __name__=='__main__':unittest.main()
