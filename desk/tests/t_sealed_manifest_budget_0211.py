"""Large declared executors/wheels, unchanged default source read ceiling."""
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from portable_sources import sealed
from portable_refresh import _sealed_declared,_bundle

class SealedBudgets(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='sealed-budget-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
    def put(self,name,raw):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        return {'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'mode':'100644'}
    def test_explicit_single_file_and_total_budgets_both_apply(self):
        self.put('one',b'1234');self.put('two',b'56')
        self.assertEqual(set(sealed(self.root,max_file_bytes=4,max_bytes=6)),{'one','two'})
        with self.assertRaisesRegex(ValueError,'byte budget'):sealed(self.root,max_file_bytes=3,max_bytes=6)
        with self.assertRaisesRegex(ValueError,'byte budget'):sealed(self.root,max_file_bytes=4,max_bytes=5)
    def test_exact_declared_sizes_do_not_admit_an_extra_file_over_the_budget(self):
        self.put('declared',b'1234');self.put('extra',b'5')
        with self.assertRaisesRegex(ValueError,'undeclared'):_sealed_declared(self.root,{'declared':4})
    def test_linked_root_or_nested_directory_is_refused_before_enumeration(self):
        outside=self.root/'outside';outside.mkdir();(outside/'leaf').write_bytes(b'1')
        for nested in (False,True):
            with self.subTest(nested=nested):
                root=self.root/('nested-root' if nested else 'root-link')
                if nested:root.mkdir();link=root/'linked'
                else:link=root
                if os.name=='nt':
                    import _winapi
                    _winapi.CreateJunction(str(outside),str(link))
                else:link.symlink_to(outside,target_is_directory=True)
                calls=[];real_scandir=os.scandir
                def observed(path):
                    current=Path(path);calls.append(current)
                    if current==link or current.is_relative_to(link):
                        raise AssertionError('linked directory enumerated before refusal')
                    return real_scandir(path)
                try:
                    with mock.patch.object(os,'scandir',side_effect=observed):
                        with self.assertRaisesRegex(ValueError,'[Ll]ink|directory unavailable'):
                            _sealed_declared(root,{'linked/leaf' if nested else 'leaf':1})
                    self.assertNotIn(link,calls)
                finally:
                    if os.name=='nt':link.rmdir()
                    else:link.unlink()
    def test_invalid_or_unbounded_manifest_sizes_refuse_before_reading(self):
        for sizes in ({'x':-1},{'x':True},{'x':'4'},{'x':512*1024*1024+1},{}):
            with self.subTest(sizes=sizes),self.assertRaises(ValueError):_sealed_declared(self.root,sizes)
    def test_default_source_limit_remains_32MiB_but_explicit_node_size_is_supported(self):
        size=32*1024*1024+1;path=self.root/'node.exe'
        with path.open('wb') as stream:stream.truncate(size)
        with self.assertRaisesRegex(ValueError,'byte budget'):sealed(self.root)
        entries=_sealed_declared(self.root,{'node.exe':size})
        self.assertEqual(len(entries['node.exe'].data),size)
    def test_large_verification_wheel_remains_bound_to_manifest_sha_size_and_names(self):
        raw=b'0'*(32*1024*1024+1)
        wheel=self.put('dependencies/win32/pinned.whl',raw)
        source=self.put('repository/source.py',b'SOURCE=1\n');runner=self.put('runner/runner.py',b'RUNNER=1\n')
        manifest={'format':'helene-verification-bundle.v2','version':'fixture',
            'files':{'source.py':source},'runners':{'runner.py':runner},
            'dependencies':{'win32':{'files':{'pinned.whl':wheel}}}}
        p=self.root/'manifest.json';p.write_text(json.dumps(manifest));expected=hashlib.sha256(p.read_bytes()).hexdigest()
        entries=_bundle(self.root,expected,'fixture')
        self.assertEqual(entries['dependencies/win32/pinned.whl'].identity()['sha256'],wheel['sha256'])
        (self.root/'dependencies/win32/pinned.whl').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'input changed'):_bundle(self.root,expected,'fixture')

if __name__=='__main__':unittest.main()
