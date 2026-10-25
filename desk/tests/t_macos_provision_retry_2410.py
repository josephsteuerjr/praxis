"""Only concrete first-provision interruption findings; NOT native Mac qualification."""
import hashlib,io,json,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
import prepare_python_posix as runtime
import bootstrap_python_macos as bootstrap
from localharness import app_components


class Retry(unittest.TestCase):
    def test_interrupted_extract_and_stale_download_resume_without_final_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'cache';cache.mkdir();output=root/'prepared'
            archive=io.BytesIO()
            with tarfile.open(fileobj=archive,mode='w:gz') as tar:
                raw=b'synthetic interpreter input, never executed'
                info=tarfile.TarInfo('python/bin/python3');info.size=len(raw)
                tar.addfile(info,io.BytesIO(raw))
            raw=archive.getvalue();recipe={'version':'3.14.7','name':'fixture.tar.gz',
                'sha256':hashlib.sha256(raw).hexdigest(),'url':'https://fixture.invalid/python','dependencies':[]}
            (cache/'fixture.tar.download').write_bytes(b'old interrupted download')
            extract=tarfile.TarFile.extractall
            def interrupted(tar,path,*args,**kwargs):
                partial=Path(path)/'python';partial.mkdir();(partial/'incomplete').write_text('retained')
                raise OSError('synthetic interrupted extraction')
            with mock.patch('localharness.python_platform.runtime_target',return_value='macos-arm64'), \
                 mock.patch.object(runtime,'runtime_recipe',return_value=recipe), \
                 mock.patch.object(runtime.urllib.request,'urlopen',return_value=io.BytesIO(raw)), \
                 mock.patch.object(tarfile.TarFile,'extractall',interrupted):
                with self.assertRaisesRegex(OSError,'interrupted'):runtime.prepare(output,cache)
            self.assertFalse((output/'python').exists())
            self.assertEqual(len(list(output.glob('.extract-*'))),1)
            with mock.patch('localharness.python_platform.runtime_target',return_value='macos-arm64'), \
                 mock.patch.object(runtime,'runtime_recipe',return_value=recipe), \
                 mock.patch.object(runtime.subprocess,'check_output',return_value='Python 3.14.7'), \
                 mock.patch.object(runtime.subprocess,'run'):
                result=runtime.prepare(output,cache,resume=True)
            self.assertEqual(result,output/'python')
            self.assertTrue((result/'.publisher-extracted.json').is_file())
            self.assertTrue((output/'runtime-input.json').is_file())
            self.assertEqual(len(list(output.glob('.extract-*'))),2)
            self.assertEqual((cache/'fixture.tar.download').read_bytes(),b'old interrupted download')

    def test_partial_assembly_never_becomes_final_program_and_retry_preserves_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'kit-files.json').write_text(json.dumps({'format':'helene-private-source-kit.v1','files':{}}))
            prepared=root/'.prepare';prepared.mkdir()
            recipe=runtime.runtime_recipe('macos-arm64')
            (prepared/'runtime-input.json').write_text(json.dumps({'target':'macos-arm64','publisher_sha256':recipe['sha256']}))
            def assemble(runtime,edition,relay,relay_source,stage,**kwargs):
                stage.mkdir();(stage/'python-app.json').write_text('{"version":"1.9.4"}')
                if not hasattr(assemble,'failed'):
                    assemble.failed=True
                    raise OSError('synthetic interruption after early passport')
                archive=stage.parent/'fixture.zip';archive.write_bytes(b'fixture')
                return archive
            with mock.patch.object(bootstrap,'runtime_target',return_value='macos-arm64'), \
                 mock.patch.object(bootstrap,'assemble',side_effect=assemble) as build:
                with self.assertRaisesRegex(OSError,'early passport'):bootstrap.provision(root)
                self.assertFalse((root/'Helene').exists())
                self.assertEqual(len(list(root.glob('.assemble-*'))),1)
                program=bootstrap.provision(root,resume=True)
                self.assertTrue((program/'private-provision-complete.json').is_file())
                self.assertEqual(bootstrap.provision(root),program)
                self.assertEqual(build.call_count,2)
                self.assertEqual(len(list(root.glob('.assemble-*'))),2)

    def test_explicit_native_relay_unavailable_is_not_malformed_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'components').mkdir()
            path=root/'components/components.json'
            path.write_text(json.dumps({'format':'helene-components.v1','components':{'relay':{'unavailable':'Native relay not supplied'}}}))
            row=app_components.status(root,root)[0]
            self.assertFalse(row['ready']);self.assertTrue(row['unavailable'])
            self.assertEqual(row['error'],'Native relay not supplied')
            with self.assertRaisesRegex(ValueError,'Native relay'):app_components.install(root,root,'relay')
            path.write_text(json.dumps({'format':'helene-components.v1','components':{}}))
            self.assertIn('invalid',app_components.status(root,root)[0]['error'])


if __name__=='__main__':unittest.main(verbosity=2)
