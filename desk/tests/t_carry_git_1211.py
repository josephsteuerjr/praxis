"""A real linked worktree survives without access to its former repository."""
import os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import carry_git,carry_payload


@unittest.skipUnless(shutil.which('git'),'Git is needed for actual object/index qualification')
class Git(unittest.TestCase):
    def test_interrupted_pointer_replacement_resumes_from_retained_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=root/'source';repo.mkdir();worktree=root/'worktree'
            def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            git('init');(repo/'file').write_bytes(b'base');git('add','file')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','initial')
            git('worktree','add','-b','agent',str(worktree))
            plan=carry_git.plan({'project':worktree});archive=carry_payload.create(root/'snapshot.zip',plan['roots'])
            home=root/'returned';ready=carry_payload.stage(root/'snapshot.zip',home,expected_sha256=archive['sha256'])
            project=Path(ready['roots']['project']);original=Path.rename;failed=False
            def interrupt(source,target):
                nonlocal failed
                if source.name.startswith('.git-portable-') and not failed:
                    failed=True;raise OSError('fixture interruption after pointer backup')
                return original(source,target)
            with mock.patch.object(Path,'rename',interrupt):
                with self.assertRaisesRegex(OSError,'fixture interruption'):carry_git.rebind(home,plan['worktrees'])
            self.assertFalse((project/'.git').exists());self.assertTrue((worktree/'.git').is_file())
            carry_git.rebind(home,plan['worktrees'])
            self.assertTrue((project/'.git').is_dir())
            subprocess.run(['git','-C',str(project),'fsck','--connectivity-only'],check=True,capture_output=True)
            self.assertEqual(len(list((home/'.git-rebind').glob('*/original-git-pointer'))),1)

    def test_history_index_dirty_untracked_and_independent_commit_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repository=root/'original';repository.mkdir();worktree=root/'worktree'
            def git(where,*args):
                return subprocess.run(['git','--no-optional-locks','-C',str(where),*args],check=True,capture_output=True,text=True).stdout.strip()
            git(repository,'init');(repository/'tracked').write_text('base\n');git(repository,'add','tracked')
            git(repository,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','initial')
            git(repository,'worktree','add','-b','agent',str(worktree))
            (worktree/'tracked').write_text('staged\n');git(worktree,'add','tracked')
            (worktree/'tracked').write_text('working changes\n');(worktree/'untracked.log').write_text('project input\n')
            original_head=git(worktree,'rev-parse','HEAD');original_status=git(worktree,'status','--porcelain','--untracked-files=all')
            plan=carry_git.plan({'project':worktree});self.assertEqual(len(plan['worktrees']),1)
            archive=carry_payload.create(root/'agent.zip',plan['roots'],metadata={'git':plan['worktrees']})
            home=root/'returned';receipt=carry_payload.stage(root/'agent.zip',home,expected_sha256=archive['sha256'])
            restored=Path(receipt['roots']['project'])
            with mock.patch.dict(os.environ,{'GIT_DIR':str(root/'unrelated-missing-repository'),'GIT_INDEX_FILE':str(root/'foreign-index')}):
                rebound=carry_git.rebind(home,plan['worktrees'])
            self.assertTrue(rebound['worktrees'][0]['independent'])
            self.assertTrue((restored/'.git').is_dir());self.assertTrue((worktree/'.git').is_file())
            self.assertEqual(git(restored,'rev-parse','HEAD'),original_head)
            self.assertEqual(git(restored,'status','--porcelain','--untracked-files=all'),original_status)
            self.assertEqual(git(restored,'show',':tracked'),'staged')
            self.assertEqual((restored/'tracked').read_text(),'working changes\n')
            # Once the old common path disappears, the restored index and every
            # object still work. Old bytes remain in a separate retained directory.
            retained=root/'retained-original';repository.rename(retained)
            git(restored,'fsck','--connectivity-only')
            git(restored,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','after return')
            self.assertNotEqual(git(restored,'rev-parse','HEAD'),original_head)
            self.assertEqual(git(retained,'rev-parse','HEAD'),original_head)
            before=git(restored,'rev-parse','HEAD');carry_git.rebind(home,plan['worktrees'])
            self.assertEqual(git(restored,'rev-parse','HEAD'),before)


if __name__=='__main__':unittest.main()
