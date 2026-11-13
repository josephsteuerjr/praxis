import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.insert(1,str(HERE.parent))
import brokerops as b


class EditContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'project';self.root.mkdir()
        previous=b.STATE;self.addCleanup(lambda:b.configure(previous));b.configure(self.base/'state')
        self.file=self.root/'a';self.file.write_bytes(b'one\r\none\r\n')

    def call(self,**kwargs):
        args=dict(root_value=str(self.root),action='replace',path='a',old='one',new='two')
        args.update(kwargs);return b.edit(**args)

    def test_zero_multiple_and_overlapping_matches_refuse_before_write(self):
        for raw,old,all_value in [(b'a','absent',False),(b'aa','a',False),(b'aaa','aa',False),(b'aaa','aa',True)]:
            self.file.write_bytes(raw)
            result=self.call(old=old,all=all_value)
            self.assertFalse(result['ok']);self.assertEqual(result['stage'],'preflight',result)
            self.assertEqual(self.file.read_bytes(),raw)

    def test_non_utf8_is_preserved_without_a_replacement_backup(self):
        raw=b'one\xffone\r\n';self.file.write_bytes(raw)
        for value in (True,False):
            result=self.call(all=value)
            self.assertFalse(result['ok']);self.assertEqual(result['stage'],'preflight',result)
            self.assertEqual(self.file.read_bytes(),raw)
        self.assertEqual(list(b.BACKUPS.rglob('*')), [])

    def test_all_unicode_crlf_count_and_exact_old_backup(self):
        raw='я\r\nя\r\n'.encode();self.file.write_bytes(raw)
        result=self.call(old='я',new='猫',all=True,expected_sha256=hashlib.sha256(raw).hexdigest())
        self.assertTrue(result['ok'],result);self.assertEqual(result['replacements'],2)
        self.assertEqual(self.file.read_bytes(),'猫\r\n猫\r\n'.encode())
        self.assertEqual(Path(result['backup']).read_bytes(),raw)
        self.assertNotIn('after_error',result)
        final=self.call(old='猫',new='ok',all=True,expected_sha256=result['after'])
        self.assertTrue(final['ok'],final)
        self.assertEqual(self.file.read_bytes(),b'ok\r\nok\r\n')

    def test_missing_path_invalid_all_and_hash_are_preflight(self):
        raw=self.file.read_bytes()
        cases=[dict(path='../outside'),dict(path='missing'),dict(old=''),dict(all=1),
               dict(action='write',all=True,content='bad'),dict(expected_sha256='f'*64)]
        with patch.object(b,'_atomic_write',side_effect=AssertionError('must not publish')) as write:
            for args in cases:
                result=self.call(**args)
                self.assertFalse(result['ok']);self.assertEqual(result['stage'],'preflight',result)
            write.assert_not_called()
        self.assertEqual(self.file.read_bytes(),raw)

    def test_publication_exception_is_never_a_preflight_refusal(self):
        def fail(path,content):
            path.write_text(content,encoding='utf8')
            raise OSError('response lost after publication')
        with patch.object(b,'_atomic_write',side_effect=fail):
            result=self.call(all=True)
        self.assertFalse(result['ok']);self.assertEqual(result['stage'],'apply',result)
        self.assertEqual(self.file.read_bytes(),b'two\r\ntwo\r\n')

    def test_failed_readback_is_disclosed(self):
        original=b._hash_probe;calls=[]
        def probe(path):
            calls.append(path)
            return original(path) if len(calls)==1 else ('','readback unavailable')
        with patch.object(b,'_hash_probe',side_effect=probe):
            result=self.call(all=True)
        self.assertTrue(result['ok']);self.assertIn('after_error',result)

    def test_patch_check_refusal_and_apply_failure_have_different_stages(self):
        diff='--- a/a\n+++ b/a\n@@ -1 +1 @@\n-one\n+two\n'
        with patch.object(b,'_sh',return_value=(1,'check refused')):
            result=self.call(action='patch',patch=diff)
        self.assertEqual(result['stage'],'preflight',result)
        with patch.object(b,'_git',return_value=''),patch.object(b,'_sh',side_effect=[(0,''),(1,'apply failed')]):
            result=self.call(action='patch',patch=diff)
        self.assertEqual(result['stage'],'apply',result)

    def test_file_changed_between_planning_and_publication_refuses(self):
        original=b._inside;calls=[]
        def inside(root,path):
            calls.append(path)
            if len(calls)==2:self.file.write_bytes(b'foreign bytes')
            return original(root,path)
        with patch.object(b,'_inside',side_effect=inside):result=self.call(all=True)
        self.assertEqual(result['stage'],'preflight',result)
        self.assertEqual(self.file.read_bytes(),b'foreign bytes')


if __name__=='__main__':unittest.main()
