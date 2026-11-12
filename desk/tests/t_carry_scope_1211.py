"""Scope discovery finds omissions before a transfer promises completeness."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import carry_scope as scope


class Scope(unittest.TestCase):
    def test_additional_agent_uses_configured_shared_application_and_core(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);agent=root/'agents/second';(agent/'data').mkdir(parents=True)
            (root/'app').mkdir();(root/'tree').mkdir();(root/'app/deskapp.py').write_bytes(b'channel')
            config=agent/'helene.json';config.write_text('{"app":"../../app/deskapp.py","code":"../../tree"}')
            result=scope.discover(config);roots={row['kind']:row['path'] for row in result['roots']}
            self.assertEqual(roots['application'],str(root/'app'));self.assertEqual(roots['code'],str(root/'tree'))

    def test_configured_code_and_registered_projects_are_not_replaced_by_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);program=root/'program';home=program/'data';home.mkdir(parents=True)
            code=root/'my-code';code.mkdir();(code/'agent.py').write_bytes(b'custom core')
            project=root/'project';project.mkdir();(project/'work.log').write_bytes(b'project input, not a runtime log')
            (home/'empty').mkdir();(home/'memory/.forge/tasks/local').mkdir(parents=True)
            (home/'memory/.forge/tasks/local/task.json').write_text(json.dumps({'root':str(project)}))
            config=program/'helene.json';config.write_text(json.dumps({'tree':'data','code':str(code),'sandbox':{'mounts':[str(project)]}}))
            result=scope.discover(config)
            self.assertEqual(result['roots'][1]['path'],str(code))
            self.assertEqual(result['roots'][0]['empty_directories'],['empty'])
            self.assertEqual(result['projects'],[{'path':str(project),'via':['sandbox.mounts','forge:local']}])
            self.assertFalse(result['complete'])
            counted=scope.inventory(project);self.assertEqual(counted['files'],1);self.assertGreater(counted['bytes'],0)

    def test_remote_bound_projects_are_reported_and_never_assumed_local(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data/memory/.forge/tasks/remote').mkdir(parents=True);(root/'tree').mkdir()
            (root/'data/memory/.forge/tasks/remote/task.json').write_text(json.dumps({'root':str(root),'scope':'host','root_binding':{'host_id':'other'}}))
            config=root/'helene.json';config.write_text('{}')
            result=scope.discover(config)
            self.assertFalse(result['complete']);self.assertFalse(result['projects'])
            self.assertEqual(result['unavailable'][0]['reason'],'bound_project_requires_mapping')

    def test_real_git_worktree_records_external_common_objects_and_dirty_files(self):
        import shutil
        if not shutil.which('git'):self.skipTest('git absent')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);repo=root/'repo';repo.mkdir();worktree=root/'worktree'
            def git(*args):subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            git('init');(repo/'tracked').write_bytes(b'base');git('add','tracked')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
            git('worktree','add','-b','second',str(worktree))
            (worktree/'tracked').write_bytes(b'dirty');(worktree/'untracked.log').write_bytes(b'also owned')
            result=scope.inventory(worktree);self.assertEqual(result['files'],3)
            self.assertEqual(result['git'][0]['kind'],'worktree')
            self.assertEqual(Path(result['git'][0]['common']),repo/'.git')

    def test_directory_link_is_reported_without_following_or_counting_foreign_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'data';data.mkdir();outside=root/'outside';outside.mkdir()
            (outside/'private').write_bytes(b'not implicitly copied')
            link=data/'linked'
            if os.name=='nt':
                subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
            else:link.symlink_to(outside,target_is_directory=True)
            result=scope.inventory(data)
            self.assertEqual(result['files'],0);self.assertEqual(result['links'][0]['target'],str(outside))


if __name__=='__main__':unittest.main()
