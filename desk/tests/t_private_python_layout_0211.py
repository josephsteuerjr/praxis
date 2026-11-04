"""Closure/stop/tamper boundaries; real native flag/SDK proof is separate."""
from pathlib import Path
import ctypes,hashlib,os,shutil,sys,tempfile,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_verification_python as python

class PrivatePython(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='private-python-fixture-')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.runtime=self.root/'runtime';self.runtime.mkdir()
        bodies={'python.exe':b'exe','python314.dll':b'dll','python314.zip':b'zip',
            'python314._pth':b'../tree\nimport site\n','psutil/__init__.py':b'root sdk',
            'psutil-7.2.2.dist-info/METADATA':b'root metadata','Lib/site-packages/sdk.py':b'library sdk',
            'git/bin/git.exe':b'not python','bash.exe':b'not python','Scripts/helper.exe':b'not python'}
        files={}
        for name,body in bodies.items():
            path=self.runtime/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
            files[name]={'sha256':hashlib.sha256(body).hexdigest(),'mode':'100644'}
        with mock.patch.object(python.sys,'platform','win32'):
            self.layout=python.describe({'root':str(self.runtime),'files':files},self.runtime/'python.exe')
        self.target=self.root/'slot/interpreter'
    def provision(self,stopped=lambda:False):
        with mock.patch.object(python,'_registry'):
            return python.provision(self.layout,self.target,stopped)
    def test_root_sdks_and_metadata_are_included_but_program_startup_paths_are_not(self):
        names=set(self.layout['files'])
        self.assertTrue({'psutil/__init__.py','psutil-7.2.2.dist-info/METADATA','Lib/site-packages/sdk.py','bash.exe'}<=names)
        self.assertFalse({'python314._pth','git/bin/git.exe','Scripts/helper.exe'}&names)
    def test_every_private_byte_is_copied_without_a_shared_writable_inode(self):
        self.assertEqual(self.provision(),str(self.target/'python.exe'))
        python.verify(self.layout,self.target)
        for name in self.layout['files']:
            self.assertNotEqual((self.target/name).stat().st_ino,(self.runtime/name).stat().st_ino)
            self.assertEqual((self.target/name).stat().st_nlink,1)
    def test_changed_origin_is_refused_and_never_becomes_an_approved_layout(self):
        (self.runtime/'python314.dll').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'input changed'):self.provision()
    def test_stop_before_copy_writes_no_executable_or_sdk_bytes(self):
        with self.assertRaises(InterruptedError):self.provision(lambda:True)
        self.assertFalse(any(p.is_file() for p in self.target.rglob('*')))
    def test_mutated_private_sdk_is_rejected_without_changing_the_original(self):
        self.provision();before=(self.runtime/'psutil/__init__.py').read_bytes()
        (self.target/'psutil/__init__.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Tests changed'):python.verify(self.layout,self.target)
        self.assertEqual((self.runtime/'psutil/__init__.py').read_bytes(),before)
    def test_new_startup_configuration_is_rejected_after_execution(self):
        self.provision();(self.target/'python314._pth').write_text('../unbound\nimport site\n')
        with self.assertRaisesRegex(ValueError,'Undeclared'):python.verify(self.layout,self.target)
    def test_existing_target_is_never_overlaid(self):
        self.target.mkdir(parents=True);(self.target/'keep').write_text('retained')
        with self.assertRaisesRegex(ValueError,'already exists'):self.provision()
        self.assertEqual((self.target/'keep').read_text(),'retained')

    @unittest.skipUnless(os.name=='nt','Native Windows SDK cache permissions')
    def test_only_cache_creation_is_denied_while_SDK_source_integrity_stays_strict(self):
        self.provision();cache=self.target/'Lib/site-packages/__pycache__'
        with self.assertRaises(PermissionError):(cache/'untrusted.pyc').write_bytes(b'not input')
        with self.assertRaises(PermissionError):(cache/'nested').mkdir()
        python.verify(self.layout,self.target)
        # Source tampering is still detected by the unchanged byte inventory.
        (self.target/'Lib/site-packages/sdk.py').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError,'Tests changed'):python.verify(self.layout,self.target)

    @unittest.skipUnless(os.name=='nt','Native Windows SDK cache permissions')
    def test_cache_permissions_leave_normal_private_tree_cleanup_available(self):
        self.provision();shutil.rmtree(self.target)
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(os.name=='nt','Native Windows SDK cache permissions')
    def test_permission_installation_failure_refuses_provision(self):
        actual=ctypes.WinDLL
        def api(name,*args,**kwargs):
            dll=actual(name,*args,**kwargs)
            if name=='advapi32':dll.SetSecurityInfo=mock.Mock(return_value=5)
            return dll
        with mock.patch.object(ctypes,'WinDLL',side_effect=api),self.assertRaises(OSError):self.provision()

    @unittest.skipUnless(os.name=='nt','Native Windows SDK cache permissions')
    def test_stop_before_cache_preparation_cannot_admit_the_interpreter(self):
        prepare=python._prevent_bytecode
        with mock.patch.object(python,'_prevent_bytecode'):self.provision()
        with self.assertRaises(InterruptedError):prepare(self.layout,self.target,lambda:True)
        self.assertFalse(any(p.name=='__pycache__' for p in self.target.rglob('*')))
        python.verify(self.layout,self.target)

    def test_stop_latched_during_cache_preparation_refuses_interpreter_admission(self):
        stopped=[False]
        def prepare(*unused):stopped[0]=True
        with mock.patch.object(python,'_prevent_bytecode',side_effect=prepare),self.assertRaises(InterruptedError):
            self.provision(lambda:stopped[0])

if __name__=='__main__':unittest.main()
