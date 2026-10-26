"""Legacy custody admission is separate from actual upstream merge/review."""
from pathlib import Path
import hashlib
import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
import os
import subprocess
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_admission as admission, source_merge as merge, code_transition
from localharness.package_notice import read,write


def view(program, baseline):
    """Packager fixture: keep real B ZIP, expose the explicitly declared A view."""
    program,baseline=Path(program),Path(baseline)
    code_transition.seal(program)
    declaration={'format':admission.FORMAT,'baseline_version':'2.1.1',
        'target_version':read(program/'python-app.json')['version'],
        'view_files':merge.inventory(baseline),
        'upstream_sha256':read(program/'code-upstream.json')['sha256']}
    write(program/'source-admission.json',declaration)
    shutil.rmtree(program/'tree');shutil.copytree(baseline,program/'tree')
    return declaration


def legacy_modules():
    root=Path(__file__).parent/'fixtures/update211'
    package=types.ModuleType('legacy211');package.__path__=[str(Path(__file__).resolve().parents[1]/'localharness')]
    modules={'legacy211':package}
    with mock.patch.dict(sys.modules,modules):
        for name in ('source_merge','code_transition','python_updates'):
            spec=importlib.util.spec_from_file_location('legacy211.'+name,root/(name+'.py'))
            module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;modules[spec.name]=module
            spec.loader.exec_module(module);setattr(package,name,module)
    return modules


class SourceAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.program=self.root/'new';self.base=self.root/'base'
        for p in (self.program/'tree',self.base):p.mkdir(parents=True)
        (self.base/'module.py').write_text('old_stock = True\n')
        (self.program/'tree/module.py').write_text('new_stock = True\n')
        write(self.program/'python-app.json',{'version':'2.2.5'})
        write(self.program/'python-package-files.json',{'files':{'tree/module.py':'old'}})
        self.real=(self.program/'tree/module.py').read_bytes()
        self.declaration=view(self.program,self.base)

    def test_verified_pristine_b_materializes_once_and_does_not_erase_later_authored_edits(self):
        self.assertTrue(admission.activate(self.program))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)
        stock=read(self.program/'code-upstream.json')
        self.assertEqual(read(self.program/'python-package-files.json')['files']['tree/module.py'],stock['files']['module.py']['sha256'])
        (self.program/'tree/module.py').write_text('later_authored = True\n')
        self.assertFalse(admission.activate(self.program))
        self.assertEqual((self.program/'tree/module.py').read_text(),'later_authored = True\n')

    def test_tampered_view_or_upstream_is_refused_before_replacement(self):
        path=self.program/'tree/module.py';path.write_text('authored = True\n')
        with self.assertRaisesRegex(ValueError,'authored'):admission.activate(self.program)
        self.assertEqual(path.read_text(),'authored = True\n')
        shutil.copy2(self.base/'module.py',path)
        (self.program/'code-upstream.zip').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'identity changed'):admission.activate(self.program)
        self.assertEqual(path.read_bytes(),(self.base/'module.py').read_bytes())

    def test_undeclared_profile_data_is_never_discarded_with_the_view(self):
        private=self.program/'tree/memory/private.txt';private.parent.mkdir();private.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError,'undeclared data'):admission.activate(self.program)
        self.assertEqual(private.read_bytes(),b'preserve')
        self.assertEqual((self.program/'tree/module.py').read_bytes(),(self.base/'module.py').read_bytes())

    def test_fresh_retry_preserves_materialized_source_and_later_authored_edits(self):
        from python_migrate import fresh,installation_lock
        new=self.root/'retry';data=self.root/'retry-data'
        with installation_lock(new):fresh(self.program,new,data,launch=False)
        authored=self.real+b'\n# later authored change\n'
        (new/'tree/module.py').write_bytes(authored)
        passport=(new/'python-package-files.json').read_bytes()
        with installation_lock(new):fresh(self.program,new,data,launch=False)
        self.assertEqual((new/'tree/module.py').read_bytes(),authored)
        self.assertEqual((new/'python-package-files.json').read_bytes(),passport)
        self.assertEqual(read(new/'source-admission-state.json')['state'],'ready')

    def test_crash_between_directory_renames_recovers_exact_stock(self):
        original=Path.replace
        def replace(path,target):
            if path.name=='next':raise OSError('fixture interrupted before promotion')
            return original(path,target)
        with mock.patch.object(Path,'replace',replace):
            with self.assertRaises(OSError):admission.activate(self.program)
        self.assertFalse((self.program/'tree').exists())
        self.assertTrue(admission.activate(self.program))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)

    def test_extraction_receipts_for_derived_bytes_are_invalidated(self):
        proofs={str(self.program/'tree/module.py'):{'old':True},
                str(self.program/'python-package-files.json'):{'old':True},
                str(self.program/'runtime/python.exe'):{'keep':True}}
        self.assertEqual(admission.current_proofs(proofs,self.program),{str(self.program/'runtime/python.exe'):{'keep':True}})

    def test_actual_fresh_entry_materializes_before_controller_start(self):
        desk=Path(__file__).resolve().parents[1]
        shutil.copytree(desk/'localharness',self.program/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('python_app.py','python_migrate.py'):
            shutil.copy2(desk/name,self.program/'app'/name)
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        result=subprocess.run([sys.executable,'-B',str(self.program/'app/python_app.py'),
            '--root',str(self.program),'--help'],env=env,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)
        self.assertEqual(read(self.program/'source-admission-state.json')['state'],'ready')
        self.assertFalse((self.program/'helene.json').exists())

    def test_fresh_copy_materializes_before_profile_seeding_under_existing_lease(self):
        from python_migrate import fresh,installation_lock
        new=self.root/'fresh-copy';data=self.root/'fresh-data'
        with installation_lock(new):
            note=fresh(self.program,new,data,launch=False)
        self.assertEqual(note['state'],'prepared')
        self.assertEqual((new/'tree/module.py').read_bytes(),self.real)
        self.assertEqual(read(new/'helene.json')['code'],str(new/'tree'))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),(self.base/'module.py').read_bytes())

    def test_actual_211_preflight_admits_custody_but_not_real_conflicted_merge(self):
        old=self.root/'old';code=self.root/'authored';home=self.root/'home'
        shutil.copytree(self.base,old/'tree');shutil.copytree(self.base,code);home.mkdir()
        (code/'module.py').write_text('old_stock = "authored"\n')
        write(old/'helene.json',{'tree':str(home),'code':str(code),'runner':''})
        write(old/'python-app.json',{'version':'2.1.1'})
        code_transition.seal(old)
        modules=legacy_modules()
        with mock.patch.dict(sys.modules,modules):
            gate=modules['legacy211.code_transition'].preflight(old,self.program)
        self.assertEqual(gate['state'],'ready')
        self.assertEqual((code/'module.py').read_text(),'old_stock = "authored"\n')
        admission.activate(self.program)
        real=code_transition.preflight(old,self.program,notify=False)
        self.assertEqual(real['state'],'needs_resolution')
        plan=read(Path(real['plans'][0]['directory'])/'plan.json')
        self.assertNotEqual(plan['files']['incoming'],self.declaration['view_files'])
        self.assertEqual(plan['files']['incoming'],read(self.program/'code-upstream.json')['files'])

    def test_new_release_has_fresh_identity_without_reviving_stopped_attempt(self):
        sys.path.insert(0,str(Path(__file__).parent))
        import t_source_update_pipeline_220 as fixtures
        from localharness import source_update
        f=fixtures.PipelineTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        previous=f.directory
        f.tick(stopped=True)
        self.assertEqual(read(previous/'pipeline.json')['state'],'cancelled')
        write(f.new/'python-app.json',{'version':'2.2.6'})
        f.preflight()
        request=read(source_update.path(f.tree))
        self.assertNotEqual(Path(request['directory']),previous)
        self.assertEqual(read(previous/'pipeline.json')['state'],'cancelled')
        self.assertEqual(read(Path(request['directory'])/'pipeline.json')['state'],'queued')


if __name__=='__main__':unittest.main()
