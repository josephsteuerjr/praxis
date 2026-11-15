"""Actual byte publication, partial batch receipts and raw backend evidence across locales."""
import hashlib,json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import edit_batch as batch
import editor
import frame_language as fl
import hands

RAW='literal Русский 中文 {v1} <script>inert</script>'
sha=lambda raw:hashlib.sha256(raw).hexdigest()

class SharedEditLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})
    def item(self,name,old,new):return {'path':name,'expected_sha256':sha((self.root/name).read_bytes()),'edits':[{'old':old,'new':new}]}
    def resolve(self,name):return self.root/name,''

    def test_exact_replacements_and_admission_captions16independent_axes(self):
        raw=('old\r\n'+RAW+'\r\nold\r\n').encode()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    result,refusal=hands.replace_once(raw,'old','new',all=True);self.assertEqual(result,raw.replace(b'old',b'new'));self.assertEqual(refusal,'')
                    result,refusal=hands.replace_once(raw,'old','new');self.assertIsNone(result);self.assertIn(fl.text('edit.shared.9',v1=1),refusal);self.assertIn(fl.text('edit.shared.9',v1=3),refusal)
                    result,refusal=hands.replace_once(b'caf\xe9 old','old','new');self.assertIsNone(result);self.assertEqual(refusal,fl.text('edit.shared.5',v1=3))
                    self.assertEqual(hands.version_conflict('0'*64,sha(raw)),fl.text('edit.shared.1',v1='0'*64,v3=sha(raw)))
                    self.assertEqual(hands.version_conflict(sha(raw)[:16],sha(raw)),'')

    def test_actual_publication_is_confirmed_or_unknown_by_readback_not_caption16axes(self):
        path=self.root/'literal {v1} 中文.txt';new=RAW.encode()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                path.write_bytes(b'old')
                with self.settings(ui,language):
                    result=hands.publish(path,new,sha(b'old'));self.assertEqual(result['status'],'confirmed');self.assertEqual(result['message'],fl.text('edit.shared.16'));self.assertEqual(result['before_sha256'],sha(b'old'));self.assertEqual(result['after_sha256'],sha(new));self.assertEqual(path.read_bytes(),new)
                    with patch.object(hands,'_digest_of',side_effect=OSError('literal fixture')):
                        result=hands.publish(path,b'next',sha(new))
                    self.assertEqual(result['status'],'in_doubt');self.assertEqual(result['message'],fl.text('edit.shared.17',v1='OSError'));self.assertEqual(path.read_bytes(),b'next');self.assertIsNone(result['after_sha256'])
        self.assertFalse(list(self.root.glob('.praxis-write-*')))

    def test_actual_batch_stops_after_uncertain_second_publication_and_keeps_first_receipt(self):
        actual=hands.publish
        for language in fl.LANGUAGES:
            for name in ('a','b','c'):(self.root/name).write_bytes(b'old')
            files=[self.item(name,'old',RAW) for name in ('a','b','c')]
            def publish(target,*args,**kwargs):
                value=actual(target,*args,**kwargs)
                if target.name=='b':raise OSError('after real write')
                return value
            with fl.use(language),patch.object(hands,'publish',side_effect=publish) as writes:
                result=batch.apply(files,self.resolve);self.assertEqual(writes.call_count,2)
                self.assertEqual(result['status'],'in_doubt');self.assertEqual([r['status'] for r in result['files']],['confirmed','in_doubt','refused'])
                self.assertEqual(result['files'][0]['after_sha256'],sha(RAW.encode()));self.assertEqual(result['files'][1]['message'],fl.text('edit.shared.38',v1='OSError'));self.assertEqual(result['files'][2]['message'],fl.text('edit.shared.39'));self.assertEqual(result['message'],fl.text('edit.shared.40'))
                for name in ('a','b'):self.assertEqual((self.root/name).read_bytes(),RAW.encode())
                self.assertEqual((self.root/'c').read_bytes(),b'old')
                decoded=json.loads(batch.render(result)[7:]);self.assertEqual(decoded,result)

    def test_all_file_preflight_refuses_without_any_publication_or_caption_inference(self):
        for name in ('a','b'):(self.root/name).write_bytes(b'old')
        files=[self.item(name,'old',RAW) for name in ('a','b')];files[1]['expected_sha256']='0'*64
        for language in fl.LANGUAGES:
            with fl.use(language),patch.object(hands,'publish') as publish:
                result=batch.apply(files,self.resolve);publish.assert_not_called();self.assertEqual(result['status'],'refused');self.assertEqual(result['message'],fl.text('edit.shared.36'));self.assertEqual((self.root/'a').read_bytes(),b'old');self.assertEqual((self.root/'b').read_bytes(),b'old')

    def test_backend_raw_text_is_literal_and_only_owned_fallbacks_are_translated(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    for code,expected in [('unsupported','refused'),('unknown','in_doubt')]:
                        value={'ok':False,'code':code,'error':RAW};result=editor.remote_result(value);self.assertEqual(result['status'],expected);self.assertEqual(result['message'],RAW);self.assertEqual(result['backend_result'],value)
                    result=editor.remote_result({'ok':True,'type':'result'});self.assertEqual(result['status'],'ok');self.assertEqual(result['message'],fl.text('edit.shared.42'));self.assertIsNone(result['after_sha256'])
                    self.assertEqual(editor.remote_result(None)['message'],fl.text('edit.shared.41'))

    def test_pin_keeps_post_write_readback_message_and_batch_summary_in_one_language(self):
        choice={'language':'en'};actual=hands.publish
        def settings():return {'lang':'zh','agent':{'frame_language':choice['language']}}
        def publish(*args,**kwargs):value=actual(*args,**kwargs);choice['language']='fr';return value
        (self.root/'a').write_bytes(b'old');files=[self.item('a','old',RAW)]
        with patch.object(fl,'settings',side_effect=settings),patch.object(hands,'publish',side_effect=publish) as writes:
            result=batch.apply(files,self.resolve);writes.assert_called_once()
        with fl.use('en'):self.assertEqual(result['message'],fl.text('edit.shared.40'));self.assertEqual(result['files'][0]['message'],fl.text('edit.shared.16'))

if __name__=='__main__':unittest.main()
