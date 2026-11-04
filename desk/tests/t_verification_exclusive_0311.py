"""Exclusive creation checks the directory entry without trusting its leaf.

Fresh processes are necessary because audit hooks cannot be removed. The
deleted-leaf scenario deterministically reproduces the observed Windows
resolve result; the OS still performs every open and enforces O_EXCL itself.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(os.environ.get('HELENE_AUDIT_RUNNER',
    str(Path(__file__).resolve().parents[1] / 'localharness/source_verification_runner.py'))).resolve()
PROGRAM = r'''
import importlib.util,os,sys
from pathlib import Path
from unittest import mock
runner,base,scenario=sys.argv[1:]
base=Path(base);root=base/'inside';outside=base/'outside'
root.mkdir();outside.mkdir()
leaf=root/'lock';victim=outside/'victim';victim.write_text('preserve')
spec=importlib.util.spec_from_file_location('trusted_runner',runner)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
real_resolve=type(root).resolve
def resolve(path,*a,**kw):
    if path==leaf and scenario in {'deleted-leaf','ordinary-leaf'}:return outside/'deleted-leaf'
    if path==root/'parent' and scenario=='outside-parent':return outside
    return real_resolve(path,*a,**kw)
def denied(fn):
    try:fn()
    except PermissionError:return
    raise AssertionError('outside mutation accepted')
if scenario=='existing':leaf.write_text('keep lock')
if scenario=='symlink':
    try:leaf.symlink_to(victim)
    except OSError as exc:
        if os.name=='nt' and getattr(exc,'winerror',None)==1314:
            print('SKIP: symlink privilege unavailable');sys.exit(77)
        raise
module._write_fence(root,base/'report.json')
flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY
with mock.patch.object(type(root),'resolve',resolve):
    if scenario=='deleted-leaf':
        fd=os.open(leaf,flags,0o600);os.write(fd,b'created');os.close(fd)
        assert leaf.read_text()=='created'
    elif scenario in {'existing','symlink'}:
        try:os.open(leaf,flags|os.O_TRUNC,0o600)
        except FileExistsError:pass
        else:raise AssertionError('existing leaf opened')
        assert victim.read_text()=='preserve'
        if scenario=='existing':assert leaf.read_text()=='keep lock'
        if scenario=='symlink':denied(lambda:os.open(leaf,os.O_WRONLY|os.O_TRUNC))
    elif scenario=='outside-parent':
        denied(lambda:os.open(root/'parent'/'lock',flags,0o600))
    elif scenario=='ordinary-leaf':
        for value in [os.O_WRONLY,os.O_CREAT|os.O_WRONLY,os.O_EXCL|os.O_WRONLY]:
            denied(lambda:os.open(leaf,value,0o600))
    elif scenario=='outside':
        denied(lambda:os.open(outside/'lock',flags,0o600))
        denied(lambda:os.open(root/'..'/'outside'/'lock',flags,0o600))
assert victim.read_text()=='preserve'
assert not (outside/'lock').exists()
print('PASS '+scenario)
'''

class ExclusiveCreationFence(unittest.TestCase):
    def check(self, scenario):
        with tempfile.TemporaryDirectory(prefix='exclusive-fence-') as tmp:
            result = subprocess.run([sys.executable,'-B','-c',PROGRAM,str(RUNNER),tmp,scenario],
                capture_output=True,text=True,encoding='utf8',timeout=30,
                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode==77:self.skipTest(result.stdout.strip())
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            self.assertIn('PASS '+scenario,result.stdout)
    def test_deleted_leaf_does_not_redirect_exclusive_creation(self):self.check('deleted-leaf')
    def test_existing_leaf_is_never_opened_or_truncated(self):self.check('existing')
    def test_external_symlink_is_not_followed(self):self.check('symlink')
    def test_parent_containment_still_applies(self):self.check('outside-parent')
    def test_ordinary_opens_still_resolve_the_leaf(self):self.check('ordinary-leaf')
    def test_external_and_parent_traversal_creations_refused(self):self.check('outside')

if __name__=='__main__':unittest.main()
