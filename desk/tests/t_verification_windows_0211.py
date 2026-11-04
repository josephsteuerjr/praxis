"""Publisher-pinned Windows executor layout and private mutable cache."""
from pathlib import Path
import io,json,os,sys,tempfile,unittest,zipfile
from unittest import mock

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'installer')]
from localharness import source_verification_executor as executor

class WindowsExecutor(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='executor-platform-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.program=self.root/'program'
        self.tools=self.program/'verification-tools';self.operation=self.root/'operation'
        self.operation.mkdir()
        for name,body in {'bin/node.exe':b'publisher PE fixture',
                'npm/bin/npm-cli.js':b'publisher npm fixture',
                'cache/_cacache/content-v2/sha512/public':b'public npm payload'}.items():
            path=self.tools/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body);path.chmod(0o644)
    def manifest(self):
        row=executor.describe(self.tools,platform='win32')
        return {'bundled_executors':{'win32':row},'executor_locks':{'win32':{'tools':{
            name:{'sha256':row['files'][path]['sha256']} for name,path in row['tools'].items()}}}}
    def provision(self,manifest,stopped=lambda:False):
        with mock.patch.object(executor.sys,'platform','win32'):
            return executor.provision(self.program,manifest,self.operation,stopped)
    def test_windows_layout_is_explicit_and_self_contained(self):
        manifest=self.manifest();row=manifest['bundled_executors']['win32']
        self.assertEqual(row['tools'],{'node':'bin/node.exe','npm':'npm/bin/npm-cli.js'})
        selected=self.provision(manifest)
        self.assertEqual(Path(selected['node']),self.tools/'bin/node.exe')
        self.assertEqual(Path(selected['npm']),self.tools/'npm/bin/npm-cli.js')
        self.assertTrue(Path(selected['npm_config_cache']).is_relative_to(self.operation))
    def test_windows_file_permissions_have_portable_inventory_identity(self):
        files=executor.inventory(self.tools)
        self.assertEqual(files['bin/node.exe']['mode'],0o644)
        executor.declaration(executor.describe(self.tools,platform='win32'))
    def test_changed_distributed_windows_tool_is_rejected(self):
        manifest=self.manifest();(self.tools/'npm/bin/npm-cli.js').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'inputs changed'):self.provision(manifest)
        self.assertFalse((self.operation/'native-executor-cache').exists())
    def test_windows_lock_mismatch_never_uses_PATH(self):
        manifest=self.manifest();manifest['executor_locks']['win32']['tools']['node']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'executor lock'):self.provision(manifest)
    def test_windows_and_posix_tool_names_cannot_be_mixed(self):
        row=self.manifest()['bundled_executors']['win32'];row['tools']['npm']='bin/npm'
        with self.assertRaisesRegex(ValueError,'Unsupported'):executor.declaration(row)
    def test_windows_private_cache_changes_do_not_change_shipped_inputs(self):
        manifest=self.manifest();before=executor.inventory(self.tools)
        selected=self.provision(manifest);(Path(selected['npm_config_cache'])/'metadata').write_text('mutable')
        self.assertEqual(self.provision(manifest),selected)
        self.assertEqual(executor.inventory(self.tools),before)
    def test_stop_prevents_windows_cache_admission(self):
        with self.assertRaises(InterruptedError):self.provision(self.manifest(),lambda:True)
        self.assertFalse((self.operation/'native-executor-cache').exists())
    def test_unknown_platform_is_not_silently_a_posix_executor(self):
        with self.assertRaisesRegex(ValueError,'Unsupported'):executor.describe(self.tools,platform='unknown')
    def test_javascript_npm_entry_uses_pinned_node_without_a_command_shell(self):
        from localharness.source_update_battery import _npm_command
        tools={'node':str(self.tools/'bin/node.exe'),'npm':str(self.tools/'npm/bin/npm-cli.js')}
        self.assertEqual(_npm_command(tools),[tools['node'],tools['npm']])
        self.assertEqual(_npm_command({'npm':'/explicit/bin/npm'}),['/explicit/bin/npm'])
    def package(self,entries=None):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
        from portable_zip import Package
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as writer:
            for name,body in (entries or {'obsolete':b'old tool'}).items():
                info=zipfile.ZipInfo('Helene/verification-tools/'+name);info.external_attr=0o100644<<16
                writer.writestr(info,body)
        reader=zipfile.ZipFile(stream);self.addCleanup(reader.close)
        return Package(reader)
    def tool_bundle(self,manifest=None):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
        from portable_zip import Entry
        return {'manifest.json':Entry(json.dumps(manifest or self.manifest()).encode())}
    def test_refresh_embeds_exact_declared_windows_tools_and_removes_old_tools(self):
        from portable_refresh import _verification_tools
        package=self.package();bundle=self.tool_bundle()
        self.assertTrue(_verification_tools(package,bundle,'windows',self.tools))
        expected={'Helene/verification-tools/'+name for name in self.manifest()['bundled_executors']['win32']['files']}
        self.assertEqual(package.names(),expected)
        self.assertNotIn('Helene/verification-tools/obsolete',package.names())
    def test_refresh_rejects_changed_windows_tools_before_writing_package(self):
        package=self.package();bundle=self.tool_bundle()
        from portable_refresh import _verification_tools
        (self.tools/'bin/node.exe').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'tool changed'):_verification_tools(package,bundle,'windows',self.tools)
        self.assertEqual(package.replacements,{})
    def test_refresh_rejects_unadvertised_tools(self):
        package=self.package();bundle=self.tool_bundle({'bundled_executors':{}})
        from portable_refresh import _verification_tools
        with self.assertRaisesRegex(ValueError,'no native manifest'):_verification_tools(package,bundle,'windows',self.tools)
        self.assertEqual(package.replacements,{})
    def test_refresh_can_reuse_exact_declared_tools_from_an_accepted_donor(self):
        entries={name:(self.tools/name).read_bytes() for name in executor.inventory(self.tools)}
        package=self.package(entries);bundle=self.tool_bundle()
        from portable_refresh import _verification_tools
        self.assertTrue(_verification_tools(package,bundle,'windows'))
    def test_refresh_rejects_a_manifest_whose_tool_lock_disagrees_with_its_files(self):
        package=self.package();manifest=self.manifest()
        manifest['executor_locks']['win32']['tools']['npm']['sha256']='0'*64
        from portable_refresh import _verification_tools
        with self.assertRaisesRegex(ValueError,'executor lock'):
            _verification_tools(package,self.tool_bundle(manifest),'windows',self.tools)
        self.assertEqual(package.replacements,{})

if __name__=='__main__':unittest.main()
