"""A frozen inventory fences legal-file count compatibility and CI pipe failures."""
import hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
import build_mac

class Tree(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'out'
        self.files={'core/secrets.py':b'# source\n','agent.py':b'# agent\n',
                    'memory_life.py':b'# memory\n','LICENSE':b'license','NOTICE':b'notice'}
        for name,data in self.files.items():
            target=self.out/'tree'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        self.inventory=self.root/'inventory.json'
        self.inventory.write_text(json.dumps({'version':'test','tree_inventory_scope':'published_payload',
            'frozen_tree_files':{name:hashlib.sha256(data).hexdigest() for name,data in self.files.items()}}))
    def count(self,declared):return build_mac.release_tree_count(self.out,{'tree_files':declared},'test',self.inventory)
    def test_original_legal_files_are_counted_when_the_entire_inventory_matches(self):
        self.assertEqual(self.count(len(self.files)),len(self.files))
    def test_old_selected_count_remains_supported_with_an_exact_inventory(self):
        declared=len(self.files)-len(build_mac.TREE_ADDED)
        self.assertEqual(self.count(declared),declared)
    def test_two_missing_agent_files_cannot_hide_behind_the_legal_count_difference(self):
        for name in ('agent.py','memory_life.py'):(self.out/'tree'/name).unlink()
        with self.assertRaisesRegex(SystemExit,'inventory'):self.count(len(self.files)-2)
    def test_same_count_with_changed_bytes_is_refused(self):
        (self.out/'tree/agent.py').write_bytes(b'# different\n')
        with self.assertRaisesRegex(SystemExit,'inventory'):self.count(len(self.files))
    def test_inclusive_count_without_trusted_inventory_is_refused(self):
        self.inventory.unlink()
        with self.assertRaises(SystemExit):self.count(len(self.files))
        self.assertEqual(self.count(len(self.files)-2),len(self.files)-2)
    def test_inconsistent_count_is_refused_even_with_valid_inventory(self):
        with self.assertRaises(SystemExit):self.count(len(self.files)+1)

@unittest.skipUnless(sys.platform.startswith('linux'),'Bash pipeline integration stays inside Linux')
class Pipeline(unittest.TestCase):
    def test_the_actual_workflow_cannot_report_success_when_the_builder_fails(self):
        import yaml
        root=Path(__file__).resolve().parents[2]
        workflow=yaml.safe_load((root/'.github/workflows/macos.yml').read_text())
        step=next(row for row in workflow['jobs']['build']['steps'] if row.get('name')=='Сборка (build_mac.py)')
        self.assertEqual(step['shell'],'bash')
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);fake=folder/'python3'
            fake.write_text('#!/bin/sh\nprintf "builder refused the archive\\n"\nexit 7\n');fake.chmod(0o755)
            env={**os.environ,'PATH':str(folder)+':'+os.environ['PATH'],'LOGS':str(folder/'logs'),
                 'BUILD':str(folder/'build'),'TREE_TAG':'test','TAG':'test'}
            result=subprocess.run(['bash','--noprofile','--norc','-e','-o','pipefail','-c',step['run']],
                cwd=root,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,7)
            self.assertIn('builder refused',(folder/'logs/build.log').read_text())

if __name__=='__main__':unittest.main()
