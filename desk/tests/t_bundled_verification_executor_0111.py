"""Native tools cannot inherit PATH tools or mutate their shipped cache."""
from pathlib import Path
import hashlib,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_verification_executor as executor

class BundledExecutor(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.program=self.root/'program';self.tools=self.program/'verification-tools'
        for name,content in {'bin/node':b'fixture native tool','bin/npm':b'fixture npm wrapper',
                'npm/bin/npm-cli.js':b'fixture npm JS','cache/_cacache/content-v2/sha512/object':b'public cached tarball'}.items():
            p=self.tools/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content);p.chmod(0o644)
        self.row=executor.describe(self.tools)
        self.manifest={'bundled_executors':{sys.platform:self.row},'executor_locks':{sys.platform:{'tools':{
            name:{'sha256':self.row['files'][path]['sha256']} for name,path in self.row['tools'].items()}}}}
        self.operation=self.root/'operation';self.operation.mkdir()
    def prepare(self,stopped=lambda:False):return executor.provision(self.program,self.manifest,self.operation,stopped)
    def test_pinned_tools_private_cache_and_resume_keep_distribution_unchanged(self):
        value=self.prepare();cache=Path(value['npm_config_cache'])
        self.assertTrue(cache.is_relative_to(self.operation));self.assertEqual(Path(value['node']),self.tools/'bin/node')
        (cache/'new-cache-metadata').write_text('mutable')
        self.assertEqual(self.prepare(),value)
        self.assertEqual(executor.inventory(self.tools),self.row['files'])
    def test_changed_shipped_tool_refuses_before_private_cache(self):
        (self.tools/'bin/node').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'inputs changed'):self.prepare()
        self.assertFalse((self.operation/'native-executor-cache').exists())
    def test_lock_mismatch_does_not_fall_back_to_ambient_tools(self):
        self.manifest['executor_locks'][sys.platform]['tools']['node']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'executor lock'):self.prepare()
    def test_owner_stop_precedes_private_preparation(self):
        with self.assertRaises(InterruptedError):self.prepare(lambda:True)
        self.assertFalse((self.operation/'native-executor-cache').exists())
    @unittest.skipUnless(sys.platform!='win32','Native POSIX symlink capability')
    def test_linked_native_cache_parent_refuses_outside_operation(self):
        outside=self.root/'outside';outside.mkdir();(self.operation/'native-executor-cache').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Linked'):self.prepare()
        self.assertEqual(list(outside.iterdir()),[])
    def test_optional_extension_preserves_existing_explicit_executor_contract(self):
        self.assertEqual(executor.provision(self.program,{},self.operation),{})

if __name__=='__main__':unittest.main()
