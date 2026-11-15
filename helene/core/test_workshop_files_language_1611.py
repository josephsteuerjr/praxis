"""Actual home read/edit receipts preserve bytes, path policy and authored text."""
import hashlib,json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import editor
import forge
import frame_language as fl
import hands
import workshop

RAW='literal Русский 中文 {v1} <script>inert</script>'
sha=lambda raw:hashlib.sha256(raw).hexdigest()

class WorkshopFilesLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);(self.root/'workspace').mkdir()
        for obj,name,value in [(workshop,'BASE',self.root),(workshop,'REPO',self.root/'code'),(forge,'BASE',self.root),(forge,'STATE_DIR',self.root/'memory/.forge'),(forge,'TASKS_DIR',self.root/'memory/.forge/tasks')]:self.enterContext(patch.object(obj,name,value))
    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})
    def snapshot(self):return {str(p.relative_to(self.root)):sha(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}

    def test_actual_numbered_read_keeps_raw_lines_and_canonical_digest16axes(self):
        raw=(RAW+'\r\nsecond\r\nthird\r\n').encode();path=self.root/'workspace/literal {v1} 中文.txt';path.write_bytes(raw);before=self.snapshot()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    answer=workshop.fs_read('workspace/'+path.name,start=1,end=1)
                    self.assertTrue(answer.startswith('    1\t'+RAW));self.assertIn(fl.text('workshop.file.35',v1=3,v3=2),answer)
                    self.assertTrue(answer.endswith('[fs_read whole-file sha256='+sha(raw)+'; bytes='+str(len(raw))+'; utf-8 exact]'));self.assertEqual(self.snapshot(),before)

    def test_binary_empty_and_missing_read_labels_do_not_rewrite_data(self):
        raw=b'\x00literal\xff';(self.root/'workspace/binary').write_bytes(raw);(self.root/'workspace/empty').write_bytes(b'');before=self.snapshot()
        for language in fl.LANGUAGES:
            with fl.use(language):
                self.assertEqual(workshop.fs_read('workspace/binary'),fl.text('workshop.file.33',v1='workspace/binary',v3=len(raw),v5=sha(raw)))
                self.assertTrue(workshop.fs_read('workspace/empty').startswith(fl.text('workshop.file.36')))
                self.assertEqual(workshop.fs_read('workspace/absent'),fl.text('workshop.file.32',v1='workspace/absent'));self.assertEqual(self.snapshot(),before)

    def test_actual_home_editor_supplies_full_receipts_and_independent_captions16axes(self):
        original=('old\r\n'+RAW+' old\r\n').encode();path=self.root/'workspace/a'
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                path.write_bytes(original)
                with self.settings(ui,language):
                    result=editor.edit_result(action='replace',path='workspace/a',old='old',new='new',all=True,expected_sha256=sha(original))
                    expected=original.replace(b'old',b'new');self.assertEqual(result['status'],'confirmed');self.assertEqual(result['before_sha256'],sha(original));self.assertEqual(result['after_sha256'],sha(expected));self.assertEqual(result['replacements'],2);self.assertEqual(path.read_bytes(),expected)
                    self.assertEqual(result['message'],fl.text('workshop.file.52',v1='workspace/a',v3=fl.text('workshop.file.51',v0=2)))
                    stale=editor.edit_result(action='write',path='workspace/a',content='wrong',expected_sha256=sha(original),overwrite=True,force=True);self.assertEqual(stale['status'],'refused');self.assertEqual(path.read_bytes(),expected)

    def test_kit_and_core_write_policies_preserve_paths_without_admitting_publication(self):
        for language in fl.LANGUAGES:
            with fl.use(language),patch.object(hands,'publish') as publish:
                self.assertEqual(workshop._resolve_write('kit://soul/skills/sample.md'),(None,fl.text('workshop.file.1')))
                self.assertEqual(workshop._resolve_write('core.py'),(None,fl.text('workshop.file.5')))
                result=editor.edit_result(action='write',path='core.py',content=RAW);self.assertEqual(result['status'],'refused');publish.assert_not_called();self.assertFalse((self.root/'core.py').exists())

    def test_accepted_text_is_literal_even_when_catalog_contains_similar_placeholders(self):
        for language in fl.LANGUAGES:
            path=self.root/'workspace/new';path.unlink(missing_ok=True)
            with fl.use(language):
                result=editor.edit_result(action='write',path='workspace/new',content=RAW)
                self.assertEqual(result['status'],'confirmed');self.assertEqual(result['message'],fl.text('workshop.file.46',v1='workspace/new',v3=len(RAW)));self.assertEqual(path.read_text(encoding='utf8'),RAW)
                self.assertEqual(result['after_sha256'],sha(RAW.encode()))

    def test_pin_retains_one_language_when_mutation_boundary_updates_settings(self):
        choice={'language':'en'};actual=hands.snapshot
        def settings():return {'lang':'zh','agent':{'frame_language':choice['language']}}
        def snapshot(*args):value=actual(*args);choice['language']='fr';return value
        with patch.object(fl,'settings',side_effect=settings),patch.object(hands,'snapshot',side_effect=snapshot) as read:
            result=workshop.fs_write_result('workspace/new',RAW);self.assertGreaterEqual(read.call_count,1)
        with fl.use('en'):self.assertEqual(result['message'],fl.text('workshop.file.46',v1='workspace/new',v3=len(RAW)))

if __name__=='__main__':unittest.main()
