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
