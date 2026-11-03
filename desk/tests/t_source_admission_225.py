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
from localharness import source_admission as admission, source_merge as merge, code_transition,authored_baseline,update_source
from localharness.package_notice import read,write
from localharness.app_migration import code_path


def view(program, baseline,*,sealed=False):
    """Packager fixture: keep real B ZIP, expose the explicitly declared A view."""
    program,baseline=Path(program),Path(baseline)
    if not sealed:code_transition.seal(program)
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

    def test_retry_with_recopied_view_and_previous_stock_recovers(self):
        original=Path.replace
        def replace(path,target):
            if path.name=='next':raise OSError('interrupted promotion')
            return original(path,target)
        with mock.patch.object(Path,'replace',replace):
            with self.assertRaises(OSError):admission.activate(self.program)
        shutil.copytree(self.base,self.program/'tree')
        self.assertTrue(admission.activate(self.program))
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)
        retained=list(self.root.glob('.helene-source-admission-*/retained-view-*'))
        self.assertEqual(retained,[])
        self.assertFalse(admission.activate(self.program))

    def test_duplicate_recovery_never_overwrites_authored_tree(self):
        bucket=self.program.parent/('.helene-source-admission-'+hashlib.sha256(str(self.program.resolve()).encode()).hexdigest()[:20])
        shutil.copytree(self.base,bucket/'previous')
        (self.program/'tree/module.py').write_text('authored=True\n')
        with self.assertRaisesRegex(ValueError,'authored'):admission.activate(self.program)
        self.assertEqual((self.program/'tree/module.py').read_text(),'authored=True\n')

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

    def test_materialized_passport_hashes_match_files_needed_by_recovery(self):
        manifest=read(self.program/'python-package-files.json')
        for name in ('edition-inventory.json','selected-source.json'):
            write(self.program/name,{'files':self.declaration['view_files']})
            manifest['files'][name]=hashlib.sha256((self.program/name).read_bytes()).hexdigest()
        write(self.program/'python-package-files.json',manifest)
        admission.activate(self.program)
        manifest=read(self.program/'python-package-files.json')
        for name in ('edition-inventory.json','selected-source.json'):
            self.assertEqual(manifest['files'][name],hashlib.sha256((self.program/name).read_bytes()).hexdigest())

    def test_migration_preparation_keeps_verified_upstream_archive_immutable(self):
        admission.activate(self.program)
        archive=(self.program/'code-upstream.zip').read_bytes()
        manifest=(self.program/'code-upstream.json').read_bytes()
        os.utime(self.program/'tree/module.py',(1577836800,1577836800))
        old=self.root/'old';home=self.root/'profile';home.mkdir()
        shutil.copytree(self.base,old/'tree')
        write(old/'helene.json',{'tree':str(home),'code':'tree','runner':''})
        update_source.select(old,self.program,'clean',by='window')
        code_transition.prepare(old,self.program,{'source_paths':[{'old':str(old/'tree')}]})
        self.assertEqual((self.program/'code-upstream.zip').read_bytes(),archive)
        self.assertEqual((self.program/'code-upstream.json').read_bytes(),manifest)

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
        self.assertEqual(code_path(new/'helene.json',read(new/'helene.json')),new/'tree')
        self.assertEqual((self.program/'tree/module.py').read_bytes(),(self.base/'module.py').read_bytes())

    def test_actual_211_preflight_admits_custody_but_requires_explicit_clean(self):
        old=self.root/'old';code=self.root/'authored';home=self.root/'home'
        shutil.copytree(self.base,old/'tree');shutil.copytree(self.base,code);home.mkdir()
        (home/'owner-history.txt').write_bytes(b'owner history remains exact\n')
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
        with self.assertRaisesRegex(ValueError,'чистая замена'):
            update_source.select(old,self.program,'preserve',by='window')
        update_source.select(old,self.program,'clean',by='window')
        note={'source_paths':[{'old':str(code)}]}
        code_transition.prepare(old,self.program,note)
        self.assertEqual(note['source_policy'],'clean')
        self.assertEqual(Path(note['source_paths'][0]['selected']),self.program/'tree')
        self.assertEqual(merge.inventory(self.program/'tree'),read(self.program/'code-upstream.json')['files'])
        self.assertNotEqual(merge.inventory(self.program/'tree'),self.declaration['view_files'])
        self.assertEqual((code/'module.py').read_text(),'old_stock = "authored"\n')
        self.assertEqual((home/'owner-history.txt').read_bytes(),b'owner history remains exact\n')

    def test_legacy_prepare_reseal_cannot_replace_protected_pristine_source(self):
        pristine=(self.program/'code-upstream.zip').read_bytes()
        manifest=(self.program/'code-upstream.json').read_bytes()
        (self.program/'source-admission-upstream.zip').write_bytes(pristine)
        (self.program/'source-admission-upstream.json').write_bytes(manifest)
        self.declaration['pristine']={'manifest_sha256':hashlib.sha256(manifest).hexdigest()}
        write(self.program/'source-admission.json',self.declaration)
        code_transition.seal(self.program)  # real behavior of installed 2.2.4
        self.assertNotEqual((self.program/'code-upstream.zip').read_bytes(),pristine)
        self.assertTrue(admission.activate(self.program))
        self.assertEqual((self.program/'code-upstream.zip').read_bytes(),pristine)
        self.assertEqual((self.program/'code-upstream.json').read_bytes(),manifest)
        self.assertEqual((self.program/'tree/module.py').read_bytes(),self.real)

    def test_protected_pristine_does_not_authorize_unknown_resealed_contents(self):
        manifest=(self.program/'code-upstream.json').read_bytes()
        shutil.copy2(self.program/'code-upstream.zip',self.program/'source-admission-upstream.zip')
        (self.program/'source-admission-upstream.json').write_bytes(manifest)
        self.declaration['pristine']={'manifest_sha256':hashlib.sha256(manifest).hexdigest()}
        write(self.program/'source-admission.json',self.declaration)
        (self.program/'tree/module.py').write_text('unknown_change=True\n')
        code_transition.seal(self.program)
        changed=(self.program/'code-upstream.zip').read_bytes()
        shutil.copy2(self.base/'module.py',self.program/'tree/module.py')
        with self.assertRaisesRegex(ValueError,'reseal differs'):admission.activate(self.program)
        self.assertEqual((self.program/'code-upstream.zip').read_bytes(),changed)
        self.assertEqual((self.program/'tree/module.py').read_bytes(),(self.base/'module.py').read_bytes())

    def test_recovery_stage_retains_verified_pristine_admission_identity(self):
        from localharness.upstream_recovery import stage
        admission.activate(self.program)
        for name in ('app/python_app.py','app/helene_setup.py',
                     'runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3'):
            path=self.program/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# fixture')
        files={p.relative_to(self.program).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
               for p in self.program.rglob('*') if p.is_file() and 'tree' not in p.relative_to(self.program).parts
               and p.name not in ('source-admission-state.json','python-package-files.json')}
        write(self.program/'python-package-files.json',{'files':files})
        new=stage(self.program,'d'*32)
        self.assertFalse(admission.activate(new))
        self.assertEqual((new/'tree/module.py').read_bytes(),self.real)

    def test_new_release_has_fresh_identity_without_reviving_stopped_attempt(self):
        sys.path.insert(0,str(Path(__file__).parent))
        import t_source_update_pipeline_220 as fixtures
        from localharness import source_update
        f=fixtures.PipelineTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        previous=f.directory
        f.tick(stopped=True)
        self.assertEqual(read(previous/'pipeline.json')['state'],'cancelled')
        # A new delivery has complete, pristine B before its first admission.
        # Never erase the protocol marker or reseal the previously admitted B.
        incoming=f.root/'next-delivery'
        for name in ('tree','app','runtime'):shutil.copytree(f.new/name,incoming/name)
        for name in ('source-inputs.json','python-package-files.json'):shutil.copy2(f.new/name,incoming/name)
        write(incoming/'python-app.json',{'version':'2.2.6'})
        code_transition.seal(incoming);authored_baseline.seal(incoming)
        f.new=incoming
        f.preflight()
        request=read(source_update.path(f.tree))
        self.assertNotEqual(Path(request['directory']),previous)
        self.assertEqual(read(previous/'pipeline.json')['state'],'cancelled')
        self.assertEqual(read(Path(request['directory'])/'pipeline.json')['state'],'queued')


if __name__=='__main__':unittest.main()
