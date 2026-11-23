"""Actual Git source snapshots below a long portable profile; no global config."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_merge as merge


class PortableLongPaths(unittest.TestCase):
    def test_actual_three_way_merge_under_long_unicode_profile(self):
        executable=shutil.which('git')
        self.assertIsNotNone(executable,'The declared source-merge executor needs Git')
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            # Each component is legal; only the complete path crosses MAX_PATH.
            # Windows CreateProcess itself requires a usable current-directory
            # length. Keep that below its limit while Git loose objects exceed it.
            suffix=Path('memory')/'.source-updates'/('a'*64)
            prefix='portable-profile-α-'
            padding=235-len(str(base/suffix))-1-len(prefix)
            self.assertGreater(padding,0)
            profile=base/(prefix+('p'*padding))
            sides=[]
            for name in ('base','local','incoming'):
                side=base/name;side.mkdir();(side/'agent.py').write_bytes(b'VALUE = "exact stock"\n');sides.append(side)
            (sides[1]/'authored.py').write_bytes(b'OWNER_EDIT = True\n')
            (sides[2]/'incoming.py').write_bytes(b'UPSTREAM = True\n')
            plan=profile/'memory'/'.source-updates'/('a'*64)
            self.assertGreater(len(str(plan/'repository.git'/'objects'/'aa'/('b'*38))),260)
            result=merge.create(*sides,plan,exe=executable)
            self.assertEqual(result['state'],'ready')
            self.assertEqual((plan/'result'/'agent.py').read_bytes(),b'VALUE = "exact stock"\n')
            self.assertEqual((plan/'result'/'authored.py').read_bytes(),b'OWNER_EDIT = True\n')
            self.assertEqual((plan/'result'/'incoming.py').read_bytes(),b'UPSTREAM = True\n')
            merge.validate_inputs(plan,merge.input_signature(result))
            self.assertEqual(merge.inventory(plan/'result'),result['automatic'])
            for side in sides:self.assertFalse((side/'.git').exists())

    @unittest.skipUnless(os.name=='nt','Windows process cwd limit')
    def test_merge_repository_and_input_paths_exceed_win32_cwd_limit(self):
        from localharness.windows_paths import Path as FilePath, wide
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root=FilePath(directory)/('part'*19)/('part'*21)/('part'*21)/('part'*21)
            try:
                sides=[]
                for name in ('base','local','incoming'):
                    side=root/name;side.mkdir(parents=True)
                    (side/'tool.py').write_text('shared')
                    sides.append(side)
                (sides[1]/'kept.py').write_text('local')
                (sides[2]/'new.py').write_text('incoming')
                out=root/'plan';self.assertGreater(len(str(out)),320)
                result=merge.create(*sides,out,exe=shutil.which('git'))
                self.assertEqual(result['state'],'ready')
                self.assertEqual((out/'result/kept.py').read_text(),'local')
                self.assertEqual((out/'result/new.py').read_text(),'incoming')
            finally:
                first=FilePath(directory)/('part'*19)
                def writable(action, name, error):
                    if not isinstance(error[1], PermissionError):
                        raise error[1]
                    os.chmod(name, 0o600)
                    action(name)
                if first.exists():
                    shutil.rmtree(wide(first), onerror=writable)

    def test_git_longpath_setting_is_per_command_not_user_configuration(self):
        original=subprocess.run;seen=[]
        def observe(command,*args,**kwargs):
            seen.append((command,kwargs['env']));return original(command,*args,**kwargs)
        with tempfile.TemporaryDirectory() as temporary,mock.patch.object(merge.subprocess,'run',side_effect=observe):
            merge.git(Path(temporary)/'private.git','init','--bare','--quiet',exe=shutil.which('git'))
        self.assertTrue(seen)
        for command,env in seen:
            self.assertIn('core.longpaths=true',command)
            self.assertNotIn('--global',command)
            self.assertEqual(env['GIT_CONFIG_NOSYSTEM'],'1')
            self.assertEqual(env['GIT_CONFIG_GLOBAL'],os.devnull)


if __name__=='__main__':unittest.main()
