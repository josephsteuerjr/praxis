"""Real startup writes bytecode before the admission helper can run."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import t_source_admission_225 as fixtures
from localharness import source_admission as admission


class BytecodeAdmission(unittest.TestCase):
    setUp=fixtures.SourceAdmission.setUp
    def startup_cache(self):
        # No -B and no inherited bytecode suppression: site imports this before
        # the command entry, exactly as bundled python314._pth does in production.
        site=self.program/'tree/sitecustomize.py'
        site.write_text('startup_import_executed = True\n')
        (self.base/'sitecustomize.py').write_bytes(site.read_bytes())
        self.declaration['view_files']=admission.merge.inventory(self.base)
        admission.write(self.program/'source-admission.json',self.declaration)
        env=dict(os.environ,PYTHONPATH=str(self.program/'tree'))
        for name in ('PYTHONDONTWRITEBYTECODE','PYTHONPYCACHEPREFIX'):env.pop(name,None)
        result=subprocess.run([sys.executable,'-c','import sitecustomize; assert sitecustomize.startup_import_executed'],
                              cwd=self.root,env=env,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        path=Path(importlib.util.cache_from_source(str(site)))
        self.assertTrue(path.is_file())
        return path,path.read_bytes()

    def test_real_startup_bytecode_is_retired_after_real_B_is_promoted(self):
        cache,original=self.startup_cache()
        self.assertTrue(admission.activate(self.program))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)
        self.assertFalse((self.program/'tree/__pycache__').exists())
        recovery=next(self.root.glob('.helene-source-admission-*'))/'previous'
        self.assertFalse(recovery.exists())

    def test_private_file_in_cache_is_refused_without_losing_any_bytes(self):
        cache,original=self.startup_cache()
        private=cache.parent/'private.txt';private.write_bytes(b'keep private data')
        with self.assertRaisesRegex(ValueError,'undeclared data'):admission.activate(self.program)
        self.assertEqual(private.read_bytes(),b'keep private data')
        self.assertEqual(cache.read_bytes(),original)

    def test_cache_link_is_refused(self):
        cache,_original=self.startup_cache()
        target=self.root/'external.pyc';target.write_bytes(cache.read_bytes())
        cache.unlink()
        try:cache.symlink_to(target)
        except OSError:self.skipTest('Symlink capability unavailable')
        with self.assertRaisesRegex(ValueError,'Linked'):admission.activate(self.program)

    def test_orphan_cache_is_refused_and_retained(self):
        cache,original=self.startup_cache()
        orphan=cache.with_name('orphan.'+sys.implementation.cache_tag+'.pyc')
        orphan.write_bytes(original)
        with self.assertRaisesRegex(ValueError,'undeclared data'):admission.activate(self.program)
        self.assertEqual(orphan.read_bytes(),original)

    def test_cache_survives_interrupted_promotion_and_recovery(self):
        from unittest import mock
        cache,original=self.startup_cache();rename=Path.replace
        def replace(path,target):
            if path.name=='next':raise OSError('fixture cache recovery interruption')
            return rename(path,target)
        with mock.patch.object(Path,'replace',replace):
            with self.assertRaises(OSError):admission.activate(self.program)
        self.assertTrue(admission.activate(self.program))
        recovery=next(self.root.glob('.helene-source-admission-*'))/'previous'
        self.assertFalse(recovery.exists())

    def test_ordinary_fresh_entry_runs_after_automatic_site_cache(self):
        self.startup_cache()
        desk=Path(__file__).resolve().parents[1]
        shutil.copytree(desk/'localharness',self.program/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('python_app.py','python_migrate.py'):shutil.copy2(desk/name,self.program/'app'/name)
        env=dict(os.environ,PYTHONPATH=str(self.program/'tree'))
        for name in ('PYTHONDONTWRITEBYTECODE','PYTHONPYCACHEPREFIX'):env.pop(name,None)
        result=subprocess.run([sys.executable,str(self.program/'app/python_app.py'),'--root',str(self.program),'--help'],
                              env=env,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)
        self.assertFalse((self.program/'helene.json').exists())


if __name__=='__main__':unittest.main()
