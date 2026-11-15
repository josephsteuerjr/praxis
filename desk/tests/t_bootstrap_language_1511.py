"""Source/catalog custody of the early launcher, including stale-build refusal."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

INSTALLER=Path(__file__).resolve().parents[1]/'installer';sys.path.insert(0,str(INSTALLER))
import windows_archive_entry as entry


class BootstrapClosure(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.desk=self.root/'desk';(self.desk/'installer').mkdir(parents=True)
        (self.desk/'installer/windows_launcher.rs').write_text('SOURCE')
        (self.desk/'python-launcher').mkdir()
        (self.desk/'python-launcher/Cargo.toml').write_text('[[bin]]\nname="helene-bootstrap"\npath="../installer/windows_launcher.rs"\n')
        for name in ('Cargo.lock','build.rs','lib.rs'):(self.desk/'python-launcher'/name).write_text(name)
        (self.desk/'lang').mkdir()
        for lang in ('ru','en','fr','zh'):(self.desk/'lang'/(lang+'.json')).write_text(json.dumps({'caption':lang}))
        (self.desk/'shell/icons').mkdir(parents=True);(self.desk/'shell/icons/icon.ico').write_bytes(b'ICON')

    def receipt(self):
        inputs=entry.bootstrap_inputs(self.desk)
        return {'kind':'helene-python-bootstrap.v2','source_sha256':inputs['installer/windows_launcher.rs'],'inputs_sha256':inputs}

    def test_current_closure_requires_library_build_script_lock_and_four_catalogs(self):
        receipt=self.receipt();self.assertEqual(len(receipt['inputs_sha256']),9)
        self.assertTrue(entry.bootstrap_matches(receipt,self.desk))
        for name in ('python-launcher/lib.rs','python-launcher/build.rs','python-launcher/Cargo.lock','lang/ru.json','lang/en.json','lang/fr.json','lang/zh.json'):
            path=self.desk/name;before=path.read_bytes();path.write_bytes(before+b'\n')
            self.assertFalse(entry.bootstrap_matches(receipt,self.desk),name)
            path.write_bytes(before);self.assertTrue(entry.bootstrap_matches(receipt,self.desk))

    def test_source_only_legacy_receipt_cannot_admit_new_multilingual_bootstrap(self):
        receipt=self.receipt();receipt.pop('inputs_sha256');receipt['kind']='helene-python-bootstrap.v1'
        self.assertFalse(entry.bootstrap_matches(receipt,self.desk))
        # Historical sources without a Cargo bootstrap retain their original gate.
        (self.desk/'python-launcher/Cargo.toml').write_text('[package]\nname="old-launcher"\n')
        self.assertTrue(entry.bootstrap_matches(receipt,self.desk))

    @unittest.skipIf(sys.platform=='win32','symbolic-link fixture runs on isolated POSIX')
    def test_linked_build_input_is_refused(self):
        path=self.desk/'python-launcher/lib.rs';path.unlink()
        target=self.root/'elsewhere.rs';target.write_text('OTHER')
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'linked'):entry.bootstrap_inputs(self.desk)

    def test_build_rechecks_source_before_issuing_receipt(self):
        output=self.root/'output';output.mkdir()
        pe=bytearray(512);pe[:2]=b'MZ';struct.pack_into('<I',pe,0x3c,0x80);pe[0x80:0x84]=b'PE\0\0'
        struct.pack_into('<H',pe,0x84,0x8664);struct.pack_into('<H',pe,0x80+92,2)
        def compile(argv,**kw):
            self.assertIn('--locked',argv);self.assertIn('--offline',argv);self.assertIn('windows-bootstrap',argv)
            build=Path(kw['env']['CARGO_TARGET_DIR'])/'release';build.mkdir()
            (build/'helene-bootstrap.exe').write_bytes(pe)
            (self.desk/'lang/fr.json').write_text('CHANGED DURING COMPILATION')
        with patch.object(entry,'__file__',str(self.desk/'installer/windows_archive_entry.py')), \
             patch.object(entry.sys,'platform','win32'),patch.object(entry.subprocess,'run',side_effect=compile), \
             patch.object(entry,'embed_icon'):
            with self.assertRaisesRegex(ValueError,'changed during'):entry.launcher(output)
        self.assertFalse((output/'helene-bootstrap.json').exists())
