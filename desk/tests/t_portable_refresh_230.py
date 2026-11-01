"""Small real-ZIP packaging consumers, not native GUI/runtime qualification."""
from __future__ import annotations

from contextlib import nullcontext
import io
import json
import shutil
import struct
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

INSTALLER = Path(__file__).resolve().parents[1] / 'installer'
sys.path.insert(0, str(INSTALLER))
import portable_refresh as refresh
import portable_sources as sources
from portable_zip import Entry, PREFIX, digest, encoded, info_for, mode, record_hashes, sha

VERSION = '2.3.0-rc.1'


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


class Fixture:
    def __init__(self, root, *, legacy=False):
        self.root, self.source = root, root / 'source'
        for n in ('agent.py', 'llm.py', 'turns.py', 'forge.py', 'forge_worker.py'):
            put(self.source, 'praxis/' + n, 'VALUE = "base"\n')
        put(self.source, 'praxis/requirements.txt', 'psutil==7.0.0\n')
        put(self.source, 'praxis/xtts_tts_worker.py', 'WORKER = "canonical"\n')
        put(self.source, 'praxis/test_agent.py', '# fixture test\n')
        put(self.source, 'praxis/memory/private.md', 'DO NOT SHIP')
        put(self.source, 'praxis/AGENTS.md', 'DO NOT SHIP')
        put(self.source, 'helene/core/agent.py', 'VALUE = "new edition"\r\n')
        put(self.source, 'helene/core/new.py', 'NEW = True\n')
        put(self.source, 'helene/core/start.sh', '#!/bin/sh\necho new\n')
        # Packaging consumes the complete current entry surface. Keep real
        # entry/launcher source bytes here; these fixture ZIPs never execute it.
        for n in ('python_app.py', 'python_migrate.py', 'deskapp.py', 'helene_setup.py',
                  'windows_start.py', 'first_start_window.py'):
            put(self.source, 'desk/' + n, (INSTALLER.parent / n).read_bytes())
        for n in ('windows_archive_entry.py', 'windows_launcher.rs'):
            put(self.source, 'desk/installer/' + n, (INSTALLER / n).read_bytes())
        put(self.source, 'desk/shell/icons/icon.ico', (INSTALLER.parent / 'shell/icons/icon.ico').read_bytes())
        put(self.source, 'desk/resources/first-start.html',
            (INSTALLER.parent / 'resources/first-start.html').read_bytes())
        put(self.source, 'desk/deskpkg.py', 'def requirements(flavor):\n return ["aiohttp", "pywebview==6.2.1"]\n')
        put(self.source, 'desk/localharness/new.py', 'CHANGED = True\n')
        put(self.source, 'desk/deskd/reader.py', 'READER = True\n')
        put(self.source, 'desk/resources/version.txt', 'current')
        put(self.source, 'desk/resources/soul/skills/review.md', 'Optional critic')
        put(self.source, 'desk/ui-kit/shared.ts', 'export const value = 1\n')
        for frontend in sources.FRONTS:
            put(self.source, 'desk/' + frontend + '/package.json', '{"scripts":{"build":"vite build"}}')
            put(self.source, 'desk/' + frontend + '/src/main.ts', 'import "../../ui-kit/shared"')
            put(self.source, 'desk/' + frontend + '/dist/index.html', '<html>' + frontend + '</html>')
            put(self.source, 'desk/' + frontend + '/dist/assets/current.js', 'const current = true;')
        put(self.source, 'HELENE-SOURCE.json', '{"frozen_tree_files":{"agent.py":"deliberately-stale"}}')
        self.front = root / 'front.json'
        commands = [{'cwd': 'desk/' + n, 'argv': ['npm', 'run', 'build'], 'returncode': 0} for n in sources.FRONTS]
        sources.write_receipt(self.source, self.front, commands)
        self.bundle = root / 'verification'
        material = {'runner/source_verification_runner.py': b'# runner\n'}
        for tree in ('praxis', 'helene/core', 'desk'):
            for name, entry in sources.scan(self.source/tree).items():
                material['repository/' + tree + '/' + name] = entry.data
        # Application-owned seed soul is valid bundle material, not private state.
        material['repository/desk/resources/soul/skills/review.md'] = b'Optional critic'
        selected, unused = sources.compose_core(self.source)
        material.update({'repository/live/' + n: e.data for n, e in selected.items()})
        for n, data in material.items():
            put(self.bundle, n, data)
        def rows(prefix):
            return {n[len(prefix):]: Entry(v).identity() for n, v in material.items() if n.startswith(prefix)}
        put(self.bundle, 'manifest.json', encoded({'format': 'helene-verification-bundle.v1', 'version': VERSION,
             'files': rows('repository/'), 'runners': rows('runner/'), 'lanes': ['fixture-only']}))
        self.donor = root / 'old.zip'
        # Structural PE fixture only: the real consumer validates its x64 GUI
        # header and exact source/icon/binary binding, without running native code.
        bootstrap = bytearray(512)
        bootstrap[:2] = b'MZ'
        struct.pack_into('<I', bootstrap, 0x3c, 0x80)
        bootstrap[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<HH', bootstrap, 0x84, 0x8664, 1)
        struct.pack_into('<H', bootstrap, 0x94, 0xf0)
        struct.pack_into('<H', bootstrap, 0x98, 0x20b)
        struct.pack_into('<H', bootstrap, 0x80 + 92, 2)
        bootstrap = bytes(bootstrap)
        bootstrap_receipt = {'kind':'helene-python-bootstrap.v1',
            'source_sha256':digest(self.source/'desk/installer/windows_launcher.rs'),
            'icon_sha256':digest(self.source/'desk/shell/icons/icon.ico'),
            'binary_sha256':sha(bootstrap)}
        entries = {
            'helene.exe': Entry(bootstrap),
            'helene-bootstrap.json': Entry(encoded(bootstrap_receipt)),
            'runtime/bin/python3': Entry(b'not-executed-python-runtime\0' * 1024, '100755'),
            'runtime/lib/library.bin': Entry(bytes(range(255)) * 1024),
            'Helene.app/Contents/MacOS/Helene': Entry(b'actual-preserved-wrapper', '100755'),
            'Helene.app/Contents/_CodeSignature/CodeResources': Entry(b'actual-signature'),
            'helene': Entry(b'#!/bin/sh\nexec runtime/bin/python3 app/python_app.py\n', '100755'),
            'app/python_app.py': Entry(b'OLD\n', '100755'),
            'app/localharness/deleted.py': Entry(b'STALE\n'),
            'app/static/assets/old.js': Entry(b'old'),
            'app/mobile/index.html': Entry(b'old mobile'),
            'app/miniapp/index.html': Entry(b'old miniapp'),
            'app/component-downloads.json': Entry(encoded({'app_version':'2.2.17','artifacts': {'voice': {'sha256':'pinned','url':'https://example.invalid/immutable'}}})),
            'python-app.json': Entry(encoded({'format':'helene-python-app.v1','version':'2.2.17'})),
            'helene-build.json': Entry(encoded({'version':'2.2.17'})),
            'tree/agent.py': Entry(b'LEGACY\n'),
            'helene.json': Entry(b'{"setup_complete":false}'),
        }
        if legacy:
            entries['source-admission.json'] = Entry(encoded({'format':'helene-source-admission.v1',
                'target_version':'2.2.17','baseline_version':'2.1.1',
                'view_files': {'agent.py': {'sha256':sha(b'LEGACY\n'),'mode':'100644'}}}))
        with zipfile.ZipFile(self.donor, 'x', zipfile.ZIP_DEFLATED) as z:
            for n, e in entries.items():
                z.writestr(info_for(PREFIX + n, e.mode), e.data)
        self.kw = dict(expected_sha256=digest(self.donor), frontend_receipt=self.front,
                       verification_bundle=self.bundle, verification_sha256=digest(self.bundle/'manifest.json'))

    def plan(self, platform='windows', **kwargs):
        return refresh.plan(self.donor, self.source, VERSION, platform, **{**self.kw, **kwargs})


class PortableRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='helene-zip-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_windows_refresh_removes_donor_vbs_and_refreshes_the_single_entry_instructions(self):
        from windows_archive_entry import OBSOLETE_ENTRIES,INSTRUCTION_FILES,instructions
        f=Fixture(self.root)
        with zipfile.ZipFile(f.donor,'a') as z:
            for name in OBSOLETE_ENTRIES:z.writestr(PREFIX+name,b'old script')
            for name in INSTRUCTION_FILES:z.writestr(PREFIX+name,b'old instructions')
        f.kw['expected_sha256']=digest(f.donor)
        plan,replacements,removed=f.plan('windows')
        target=self.root/'single-entry.zip'
        refresh.build(f.donor,target,plan,replacements,removed,expected_plan=plan['sha256'])
        with zipfile.ZipFile(target) as z:
            self.assertTrue(all(PREFIX+name not in z.namelist() for name in OBSOLETE_ENTRIES))
            self.assertTrue(all(z.read(PREFIX+name)==instructions().encode('utf8') for name in INSTRUCTION_FILES))

    def test_four_platform_zip_consumers_preserve_compressed_native_records(self):
        f = Fixture(self.root)
        preserved = {PREFIX + n for n in ('runtime/bin/python3', 'runtime/lib/library.bin', 'helene.exe',
                     'Helene.app/Contents/MacOS/Helene', 'Helene.app/Contents/_CodeSignature/CodeResources', 'helene')}
        old_records = record_hashes(f.donor, preserved)
        for platform in refresh.PLATFORMS:
            with self.subTest(platform=platform):
                plan, replacements, removed = f.plan(platform)
                target = self.root / (platform + '.zip')
                receipt = refresh.build(f.donor, target, plan, replacements, removed, expected_plan=plan['sha256'])
                self.assertFalse(receipt['native_qualified'])
                self.assertEqual(old_records, record_hashes(target, preserved))
                with zipfile.ZipFile(target) as z:
                    self.assertEqual(z.read(PREFIX+'tree/agent.py'), b'VALUE = "new edition"\r\n')
                    self.assertIn(PREFIX+'tree/new.py',z.namelist())
                    self.assertNotIn(PREFIX+'tree/memory/private.md',z.namelist())
                    self.assertNotIn(PREFIX+'tree/AGENTS.md',z.namelist())
                    self.assertNotIn(PREFIX+'app/localharness/deleted.py',z.namelist())
                    self.assertNotIn(PREFIX+'app/static/assets/old.js',z.namelist())
                    self.assertNotIn(PREFIX+'helene.json',z.namelist())
                    self.assertEqual(z.read(PREFIX+'app/resources/soul/skills/review.md'),b'Optional critic')
                    self.assertEqual(mode(z.getinfo(PREFIX+'app/python_app.py')),'100755')
                    for name in ('static','mobile','miniapp'):
                        self.assertIn(PREFIX+'app/'+name+'/assets/current.js',z.namelist())
                    self.assertEqual(z.read(PREFIX+'app/localharness/xtts_tts_worker.py'),b'WORKER = "canonical"\n')
                refresh.verify_archive(target, plan)

    def test_remote_and_language_inputs_invalidate_frontend_receipt(self):
        # A source dependency outside the three emitted projects must stale the
        # receipt just like an edit within app/src.
        f = Fixture(self.root)
        remote = put(f.source, 'desk/remote/src/settings-server.ts', 'export const n = 1;')
        language = put(f.source, 'desk/lang/fixture.json', '{"label":"before"}')
        commands = [{'cwd': 'desk/' + n, 'argv': ['npm', 'run', 'build'], 'returncode': 0} for n in sources.FRONTS]
        f.front.unlink()
        sources.write_receipt(f.source, f.front, commands)
        sources.verify_frontends(f.source, f.front)
        remote.write_text('export const n = 2;', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'stale'):
            sources.verify_frontends(f.source, f.front)
        remote.write_text('export const n = 1;', encoding='utf8')
        language.write_text('{"label":"after"}', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'stale'):
            sources.verify_frontends(f.source, f.front)

    def test_native_npm_preserves_script_semantics_without_inherited_console(self):
        npm=shutil.which('npm')
        if not npm or not shutil.which('node'):
            self.skipTest('Native Node/npm executor not provisioned')
        put(self.root,'package.json',json.dumps({'name':'portable-refresh-executor-smoke','private':True,
            'scripts':{'build':'node -e "console.log(\'ZIP_FRONTEND_SMOKE\')"'}}))
        result=sources.run_frontend_build(npm,self.root)
        self.assertIn(b'ZIP_FRONTEND_SMOKE',result.stdout)

    def test_frontend_command_failure_retains_real_output_and_never_retries(self):
        import subprocess
        failed=subprocess.CompletedProcess(['selected-npm','run','build'],7,b'CONCRETE BUILD ERROR\n')
        with patch.object(sources.subprocess,'run',return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError,'CONCRETE BUILD ERROR'):
                sources.run_frontend_build('selected-npm',self.root)
            run.assert_called_once()

    def test_legacy_view_is_separate_and_real_admission_materializes_current_b(self):
        f = Fixture(self.root, legacy=True)
        plan, replacements, removed = f.plan(legacy_admission=True)
        target = self.root/'legacy.zip'
        refresh.build(f.donor,target,plan,replacements,removed,expected_plan=plan['sha256'])
        program = self.root/'extracted'
        with zipfile.ZipFile(target) as z:
            z.extractall(program)
        program = program/'Helene'
        self.assertEqual((program/'tree/agent.py').read_bytes(),b'LEGACY\n')
        sys.path.insert(0,str(INSTALLER.parent))
        from localharness.source_admission import activate
        fake = types.ModuleType('python_migrate')
        fake.installation_lock=lambda *a,**k:nullcontext()
        with patch.dict(sys.modules,{'python_migrate':fake}):
            self.assertTrue(activate(program))
            self.assertFalse(activate(program))
        self.assertEqual((program/'tree/agent.py').read_bytes(),b'VALUE = "new edition"\r\n')
        self.assertIn('new.py',json.loads((program/'code-upstream.json').read_bytes())['files'])

    def test_wrong_donor_hash_does_not_open_as_package(self):
        f=Fixture(self.root)
        with self.assertRaisesRegex(ValueError,'donor SHA256'):
            f.plan(expected_sha256='0'*64)

    def test_frontend_source_and_output_drift_are_both_rejected(self):
        f=Fixture(self.root)
        source=f.source/'desk/ui-kit/shared.ts'
        original=source.read_bytes();source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Frontend build receipt'):
            f.plan()
        source.write_bytes(original)
        (f.source/'desk/mobile/dist/index.html').write_text('changed',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'Frontend build receipt'):
            f.plan()

    def test_bundle_extra_or_changed_material_refuses(self):
        f=Fixture(self.root)
        put(f.bundle,'runner/extra.py','undeclared')
        with self.assertRaisesRegex(ValueError,'undeclared'):
            f.plan()
        (f.bundle/'runner/extra.py').unlink()
        put(f.bundle,'runner/source_verification_runner.py','changed')
        with self.assertRaisesRegex(ValueError,'bundle input changed'):
            f.plan()

    def test_same_version_stale_core_and_test_bundle_refuse(self):
        f=Fixture(self.root)
        test=f.source/'praxis/test_agent.py'
        before=test.read_bytes();test.write_bytes(b'# newer test suite\n')
        with self.assertRaisesRegex(ValueError,'source/test is stale'):
            f.plan()
        test.write_bytes(before)
        put(f.source,'helene/core/new.py','NEW = "different"\n')
        with self.assertRaisesRegex(ValueError,'source/test is stale'):
            f.plan()

    def test_explicit_verification_dependency_material_is_bound(self):
        f=Fixture(self.root)
        wheel=put(f.bundle,'dependencies/win32/pytest-1-py3-none-any.whl',b'fixture wheel')
        requirement=put(f.bundle,'dependencies/win32/requirements.txt',b'pytest==1 --hash=sha256:fixture\n')
        manifest=f.bundle/'manifest.json'
        value=json.loads(manifest.read_bytes())
        value['dependencies']={'win32':{'files':{p.name:Entry(p.read_bytes()).identity() for p in (wheel,requirement)}}}
        manifest.write_bytes(encoded(value))
        f.kw['verification_sha256']=digest(manifest)
        plan,replacements,removed=f.plan()
        self.assertIn(PREFIX+'verification/dependencies/win32/'+wheel.name,replacements)
        wheel.write_bytes(b'changed wheel')
        with self.assertRaisesRegex(ValueError,'bundle input changed'):
            f.plan()

    def test_source_mutation_during_plan_is_not_a_mixed_composition(self):
        f=Fixture(self.root)
        original=refresh._metadata
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            put(f.source,'helene/core/new.py','MOVED_DURING_PLAN = True\n')
            return result
        with patch.object(refresh,'_metadata',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'Source composition changed'):
                f.plan()

    def test_product_prerelease_version_matches_actual_update_consumer(self):
        sys.path.insert(0,str(INSTALLER.parent))
        from localharness.python_updates import parts
        self.assertEqual(parts(VERSION)[:3],(2,3,0))
        self.assertLess(parts(VERSION),parts('2.3.0'))
        f=Fixture(self.root)
        with self.assertRaisesRegex(ValueError,'product semver'):
            refresh.plan(f.donor,f.source,'2.3.0rc1','windows',**f.kw)

    def test_no_overwrite_or_changed_plan(self):
        f=Fixture(self.root)
        plan,replacements,removed=f.plan()
        target=self.root/'candidate.zip';target.write_bytes(b'viable earlier candidate')
        with self.assertRaises(FileExistsError):
            refresh.build(f.donor,target,plan,replacements,removed,expected_plan=plan['sha256'])
        self.assertEqual(target.read_bytes(),b'viable earlier candidate')
        with self.assertRaisesRegex(ValueError,'Frozen plan'):
            refresh.build(f.donor,self.root/'new.zip',plan,replacements,removed,expected_plan='wrong')

    def test_explicit_runtime_dependency_overlay_binds_only_named_files(self):
        f=Fixture(self.root)
        overlay=self.root/'overlay'
        put(overlay,'runtime/Lib/site-packages/psutil/__init__.py','__version__ = "7"')
        payload=(overlay/'runtime/Lib/site-packages/psutil/__init__.py').read_bytes()
        put(overlay,'manifest.json',encoded({'format':'helene-portable-runtime-overlay.v1','platform':'windows',
            'inputs':{'wheel':{'sha256':'known-wheel-identity'}},'files':{
                'runtime/Lib/site-packages/psutil/__init__.py':Entry(payload).identity()}}))
        plan,replacements,removed=f.plan(runtime_overlay=overlay,runtime_overlay_sha256=digest(overlay/'manifest.json'))
        self.assertTrue(plan['runtime_changed'])
        target=self.root/'overlay.zip'
        refresh.build(f.donor,target,plan,replacements,removed,expected_plan=plan['sha256'])
        with zipfile.ZipFile(target) as z:self.assertEqual(z.read(PREFIX+'runtime/Lib/site-packages/psutil/__init__.py'),payload)

    def test_runtime_overlay_removes_only_exact_obsolete_dependency_members(self):
        f = Fixture(self.root)
        overlay = self.root / 'overlay'
        new_name = 'runtime/lib/site-packages/dependency-48.dist-info/METADATA'
        old_name = 'runtime/lib/library.bin'
        put(overlay, new_name, 'Version: 48\n')
        payload = (overlay / new_name).read_bytes()
        value = {'format': 'helene-portable-runtime-overlay.v1', 'platform': 'windows',
                 'inputs': {'wheel': {'sha256': 'pinned-wheel'}},
                 'files': {new_name: Entry(payload).identity()},
                 'remove': {old_name: sha(bytes(range(255)) * 1024)}}
        manifest = put(overlay, 'manifest.json', encoded(value))
        plan, replacements, removed = f.plan(runtime_overlay=overlay, runtime_overlay_sha256=digest(manifest))
        target = self.root / 'clean-dependency.zip'
        refresh.build(f.donor, target, plan, replacements, removed, expected_plan=plan['sha256'])
        with zipfile.ZipFile(target) as z:
            self.assertNotIn(PREFIX + old_name, z.namelist())
            self.assertEqual(z.read(PREFIX + new_name), payload)
        for bad_remove, message in (({old_name: '0' * 64}, 'prior file differs'),
                                    ({'app/python_app.py': sha(b'OLD\n')}, 'removal outside')):
            value['remove'] = bad_remove
            manifest.write_bytes(encoded(value))
            with self.assertRaisesRegex(ValueError, message):
                f.plan(runtime_overlay=overlay, runtime_overlay_sha256=digest(manifest))

    def test_malicious_zip_paths_links_duplicates_and_profile_are_refused(self):
        f=Fixture(self.root)
        for name in ('Helene/../escape','Helene/data/private.txt'):
            donor=self.root/(sha(name.encode())[:8]+'.zip')
            with zipfile.ZipFile(donor,'w') as z:
                z.writestr(name,b'private')
            with self.assertRaises(ValueError):
                refresh.plan(donor,f.source,VERSION,'windows',**{**f.kw,'expected_sha256':digest(donor)})
        donor=self.root/'link.zip'
        with zipfile.ZipFile(donor,'w') as z:
            info=info_for('Helene/runtime/link');info.external_attr=0o120777<<16;z.writestr(info,b'../outside')
        with self.assertRaisesRegex(ValueError,'Nonregular'):
            refresh.plan(donor,f.source,VERSION,'windows',**{**f.kw,'expected_sha256':digest(donor)})
        import warnings
        duplicate=self.root/'duplicates.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            with zipfile.ZipFile(duplicate,'w') as z:
                z.writestr('Helene/runtime/duplicate',b'first')
                z.writestr('Helene/runtime/duplicate',b'second')
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            refresh.plan(duplicate,f.source,VERSION,'windows',**{**f.kw,'expected_sha256':digest(duplicate)})

    def test_failed_build_installs_neither_candidate_nor_receipts(self):
        f=Fixture(self.root)
        plan,replacements,removed=f.plan()
        target=self.root/'failed.zip'
        with patch.object(refresh,'verify_archive',side_effect=ValueError('bad inventory')):
            with self.assertRaisesRegex(ValueError,'bad inventory'):
                refresh.build(f.donor,target,plan,replacements,removed,expected_plan=plan['sha256'])
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target)+'.artifact.json').exists())
        self.assertFalse(Path(str(target)+'.sha256').exists())
        self.assertFalse(list(self.root.glob('failed.zip.*.partial')))


if __name__=='__main__':unittest.main()
