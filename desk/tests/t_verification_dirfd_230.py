"""Audit-floor causal checks in fresh processes; POSIX uses real *at syscalls.

Set HELENE_AUDIT_RUNNER to a retained preimage for the old/new comparison.
Windows proves only common paths and explicit unresolved-descriptor refusal;
it does not emulate a successful POSIX operation.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(os.environ.get('HELENE_AUDIT_RUNNER',
    str(Path(__file__).resolve().parents[1] / 'localharness/source_verification_runner.py'))).resolve()
PROGRAM = r'''
import importlib.util,json,os,shutil,sys
from pathlib import Path
runner,base,scenario=sys.argv[1:]
base=Path(base);root=base/'inside';outside=base/'outside'
root.mkdir();outside.mkdir()
(root/'victim').write_text('inside');(outside/'victim').write_text('outside')
report=outside/'report.json'
spec=importlib.util.spec_from_file_location('trusted_runner',runner)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
events=[]
watched={'os.remove','os.rmdir','os.mkdir','os.chmod','os.chown','os.utime','os.rename','os.link','os.symlink'}
sys.addaudithook(lambda event,args:events.append([event,list(args)]) if event in watched else None)
def denied(operation):
    try:operation()
    except PermissionError:return
    raise AssertionError('out-of-root mutation was not refused')
if scenario=='common':
    os.chdir(root)
    fd=os.open(root/'victim',os.O_RDWR)
    module._write_fence(root,report)
    Path('relative').write_text('yes');(root/'absolute').write_text('yes')
    os.ftruncate(fd,2);os.close(fd)
    with open(os.devnull,'w') as sink:sink.write('yes')
    report.write_text('report allowed')
    denied(lambda:(outside/'forbidden').write_text('no'))
    os.mkdir('sub');os.rmdir('sub');os.unlink('relative')
    assert (root/'absolute').read_text()=='yes'
    assert not (outside/'forbidden').exists()
elif os.name!='posix':
    os.chdir(root)
    module._write_fence(root,report)
    # This is refusal evidence, not a fake successful native dir_fd operation.
    denied(lambda:sys.audit('os.remove','victim',999999))
    print('POSIX descriptor execution unavailable; unresolved descriptor refused')
else:
    insidefd=os.open(root,os.O_RDONLY);outsidefd=os.open(outside,os.O_RDONLY)
    os.chdir(outside if scenario in {'inside','rmtree'} else root)
    if scenario=='rmtree':
        tree=root/'tree';(tree/'nested').mkdir(parents=True)
        (tree/'nested'/'leaf').write_text('x')
    if scenario=='moved':
        moving=root/'moving';moving.mkdir();(moving/'victim').write_text('keep')
        movingfd=os.open(moving,os.O_RDONLY)
        moving.rename(outside/'moved')
    module._write_fence(root,report)
    if scenario=='inside':
        os.mkdir('made',dir_fd=insidefd)
        os.chmod('victim',0o600,dir_fd=insidefd)
        os.utime('victim',ns=(1000000000,2000000000),dir_fd=insidefd)
        os.chown('victim',-1,-1,dir_fd=insidefd)
        os.rename('victim','renamed',src_dir_fd=insidefd,dst_dir_fd=insidefd)
        os.link('renamed','linked',src_dir_fd=insidefd,dst_dir_fd=insidefd)
        os.symlink('../renamed','made/symlink',dir_fd=insidefd)
        assert (root/'made/symlink').read_text()=='inside'
        os.unlink('made/symlink',dir_fd=insidefd)
        os.unlink('linked',dir_fd=insidefd)
        os.rmdir('made',dir_fd=insidefd)
        # Absolute operands ignore even a bad descriptor, like the OS does.
        os.chmod(root/'renamed',0o600,dir_fd=999999)
        os.unlink(b'renamed',dir_fd=insidefd)
        assert (outside/'victim').read_text()=='outside'
    elif scenario=='outside':
        operations=[
            lambda:os.unlink('victim',dir_fd=outsidefd),
            lambda:os.mkdir('made',dir_fd=outsidefd),
            lambda:os.rmdir('absent',dir_fd=outsidefd),
            lambda:os.chmod('victim',0o600,dir_fd=outsidefd),
            lambda:os.chown('victim',-1,-1,dir_fd=outsidefd),
            lambda:os.utime('victim',None,dir_fd=outsidefd),
            lambda:os.rename('victim','new',src_dir_fd=insidefd,dst_dir_fd=outsidefd),
            lambda:os.rename('victim','new',src_dir_fd=outsidefd,dst_dir_fd=insidefd),
            lambda:os.link('victim','new',src_dir_fd=insidefd,dst_dir_fd=outsidefd),
            lambda:os.link('victim','new',src_dir_fd=outsidefd,dst_dir_fd=insidefd),
            lambda:os.symlink(str(root/'victim'),'new',dir_fd=outsidefd),
            lambda:os.symlink(str(outside/'victim'),'new',dir_fd=insidefd),
            lambda:os.unlink('../outside/victim',dir_fd=insidefd),
        ]
        for operation in operations:denied(operation)
        assert (root/'victim').read_text()=='inside'
        assert (outside/'victim').read_text()=='outside'
        assert not (root/'new').exists() and not (outside/'new').exists()
    elif scenario=='rmtree':
        shutil.rmtree(tree)
        assert not tree.exists()
        assert any(event=='os.remove' and args[0]=='leaf' and args[1]>=0 for event,args in events)
    elif scenario=='invalid':
        dead=os.dup(insidefd);os.close(dead)
        denied(lambda:os.unlink('victim',dir_fd=dead))
        # A regular file fd must not acquire directory authority either.
        regular=os.open(root/'victim',os.O_RDONLY)
        denied(lambda:os.unlink('victim',dir_fd=regular))
        os.close(regular)
        assert (root/'victim').read_text()=='inside'
    elif scenario=='moved':
        denied(lambda:os.unlink('victim',dir_fd=movingfd))
        assert (outside/'moved/victim').read_text()=='keep'
        os.close(movingfd)
    elif scenario=='unreadable':
        if sys.platform.startswith('linux'):
            original=os.readlink
            def unavailable(path,*a,**kw):
                if str(path).startswith('/proc/self/fd/'):raise OSError('unavailable')
                return original(path,*a,**kw)
            os.readlink=unavailable
        elif sys.platform=='darwin':
            import fcntl
            def unavailable(*a,**kw):raise OSError('unavailable')
            fcntl.fcntl=unavailable
        denied(lambda:os.unlink('victim',dir_fd=insidefd))
        assert (root/'victim').read_text()=='inside'
    os.close(insidefd);os.close(outsidefd)
print(json.dumps({'scenario':scenario,'platform':sys.platform,'events':events},
    default=lambda value: {'bytes_hex':value.hex()} if isinstance(value,bytes) else str(value)))
'''


class AuditDirectoryDescriptors(unittest.TestCase):
    def check(self, scenario):
        with tempfile.TemporaryDirectory(prefix='helene-audit-dirfd-') as tmp:
            done = subprocess.run([sys.executable, '-B', '-c', PROGRAM,
                str(RUNNER), tmp, scenario], capture_output=True, text=True,
                encoding='utf8', env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(done.returncode, 0, done.stdout + '\n' + done.stderr)
            observed = json.loads(done.stdout.splitlines()[-1])
            self.assertEqual(observed['scenario'], scenario)
            return observed

    def test_actual_runner_records_real_rmtree_outcome_and_report(self):
        with tempfile.TemporaryDirectory(prefix='helene-audit-report-') as tmp:
            base = Path(tmp)
            work = base / 'work'
            work.mkdir()
            source = work / 'test_tree.py'
            source.write_text("import pathlib,shutil,tempfile,unittest\n"
                "class TreeCase(unittest.TestCase):\n"
                " def test_tree(self):\n"
                "  with tempfile.TemporaryDirectory() as tmp:\n"
                "   p=pathlib.Path(tmp)/'tree'/'nested';p.mkdir(parents=True)\n"
                "   (p/'leaf').write_text('x');shutil.rmtree(p.parent)\n"
                "   self.assertFalse(p.parent.exists())\n", encoding='utf8')
            report = base / 'report.json'
            done = subprocess.run([sys.executable, '-B', str(RUNNER),
                '--root', str(work), '--source', str(source),
                '--sandbox', str(work), '--report', str(report)],
                cwd=base, capture_output=True, text=True, encoding='utf8',
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(done.returncode, 0, done.stdout + '\n' + done.stderr)
            value = json.loads(report.read_text(encoding='utf8'))
            self.assertTrue(value['complete'])
            self.assertTrue(value['successful'])
            self.assertTrue(value['coverage_complete'])
            self.assertEqual(value['collected'], value['executed'])
            self.assertEqual(value['tests_run'], 1)
            self.assertEqual([row['status'] for row in value['outcomes']], ['passed'])

    def test_default_absolute_fd_nullsink_report_and_outside_floor(self):
        self.check('common')

    def test_inroot_dirfd_real_mutations_or_explicit_unavailability(self):
        observed = self.check('inside')
        if os.name == 'posix':
            lengths = {'os.mkdir':3, 'os.chmod':3, 'os.chown':4, 'os.utime':4,
                       'os.rename':4, 'os.link':4, 'os.symlink':3, 'os.remove':2, 'os.rmdir':2}
            for event, count in lengths.items():
                self.assertTrue(any(e == event and len(a) == count for e,a in observed['events']), event)

    def test_outside_dirfds_and_both_pair_operands_refuse(self):
        self.check('outside')

    def test_actual_shutil_rmtree_uses_descriptor_not_cwd(self):
        self.check('rmtree')

    def test_closed_and_nondirectory_descriptors_refuse(self):
        self.check('invalid')

    def test_descriptor_renamed_outside_is_not_cached_authority(self):
        self.check('moved')

    def test_unreadable_descriptor_identity_refuses_without_guess(self):
        self.check('unreadable')


if __name__ == '__main__':
    unittest.main()
