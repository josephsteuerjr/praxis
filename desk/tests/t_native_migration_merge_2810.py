"""New native migration refuses retired preserve mode before code/data effects.

Already-admitted legacy review is covered by the source-update pipeline tests.
"""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import windows_installation as installation,code_transition,source_update as pipeline
from localharness import agent_source_update as agent_resolution
from localharness.package_notice import read,write
import t_source_update_pipeline_220 as fixture
from t_source_merge_211 import BASE,BOTH,put


class NativeBridge(unittest.TestCase):
    def setup_fixture(self,conflict=True):
        f=fixture.PipelineTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        if not conflict:put(f.code,'module.py',BASE+'\ncustom_extension = True\n')
        else:put(f.code,'module.py',fixture.LOCAL+'\nlocal_extension = 11\n')
        # A native-style predecessor older than 2.2.1, with a retained stock tree.
        (f.old/'python-app.json').unlink();write(f.old/'build.json',{'version':'1.7.0'})
        put(f.old,'helene.exe','native fixture')
        from localharness.python_platform import runtime_target
        write(f.new/'python-app.json',{'version':'2.2.11','delivery':'portable','target':runtime_target()})
        for name in ('app/python_app.py','app/deskapp.py',('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')):put(f.new,name,'fixture')
        code_transition.seal(f.new)
        put(f.tree,'memory/history.jsonl','retained-private-history\n')
        return f

    def exercise(self,conflict,copy_data=False):
        f=self.setup_fixture(conflict)
        before=(f.old/'helene.json').read_bytes()
        with patch.object(code_transition,'preflight') as preflight,patch('localharness.source_update_host.review') as review,\
             patch('localharness.migration_lifecycle.stop') as stop,patch('localharness.migration_lifecycle.remove_program') as remove:
            with self.assertRaisesRegex(ValueError,'clean code'):
                if copy_data:
                    import python_migrate
                    python_migrate.migrate(f.old,f.new,f.new,f.new/'profile',stop_service=True,
                        prepared=True,portable=True,source_mode='preserve',launch=False)
                else:installation.run(f.new,f.new,old=f.old,data=f.tree,source_mode='preserve',launch=False)
            preflight.assert_not_called();review.assert_not_called();stop.assert_not_called();remove.assert_not_called()
        self.assertEqual((f.old/'helene.json').read_bytes(),before)
        self.assertEqual((f.tree/'memory/history.jsonl').read_text(),'retained-private-history\n')
        self.assertTrue((f.old/'helene.exe').is_file())


    def test_native_conflicts_cannot_reenable_preserve_mode(self):self.exercise(True)

    def test_nonconflicting_edits_cannot_reenable_preserve_mode(self):self.exercise(False)

    def test_standalone_cli_refuses_preserve_before_any_data_copy(self):self.exercise(False,copy_data=True)

    def test_new_migration_cannot_admit_a_preserve_panel_or_stop_the_old_program(self):
        self.exercise(False)



if __name__=='__main__':unittest.main()
