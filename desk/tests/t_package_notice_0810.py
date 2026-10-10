"""A real package-version change produces one durable notice and an agent turn."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
import package_notice as notice
import updates
import runner

class Package(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.program=self.root/'program';self.program.mkdir()
        self.tree=self.root/'data';self.tree.mkdir()
        notice.write(self.program/'helene-build.json',{'version':'1.5.1','complete':True})
    def test_first_install_is_quiet_upgrade_is_observed_from_saved_version(self):
        notice.observe(self.tree,self.program,{'installed':{'version':'1.5.1'}})
        self.assertIsNone(notice.pending(self.tree))
        notice.write(self.program/'helene-build.json',{'version':'1.5.2','complete':True})
        notice.observe(self.tree,self.program,{'installed':{'version':'1.5.1'}})
        self.assertEqual(notice.pending(self.tree)['from_version'],'1.5.1')
        notice.observe(self.tree,self.program,{'installed':{'version':'1.5.1'}})
        self.assertEqual(notice.pending(self.tree)['to_version'],'1.5.2')
    def test_older_linux_home_without_installed_version_receives_one_honest_inspection(self):
        notice.write(self.program/'helene-build.json',{'version':'1.5.1','platform':'linux','complete':True})
        notice.write(self.tree/'memory/.state/born.json',{'state':'done'})
        notice.observe(self.tree,self.program,{})
        receipt=notice.pending(self.tree)
        self.assertEqual(receipt['from_version'],'')
        self.assertIn('прежняя версия здесь не записана',updates.report_note(receipt))
        notice.mark(self.tree,receipt,done=True)
        notice.observe(self.tree,self.program,{})
        self.assertIsNone(notice.pending(self.tree))
    def test_existing_linux_home_has_a_note_without_a_windows_trial(self):
        notice.observe(self.tree,self.program,{'installed':{'version':'1.5.0'}})
        notes=[];turns=[]
        desk=types.SimpleNamespace(archive=lambda text,**kw:notes.append(text),life=lambda *a,**kw:None)
        with patch.object(updates,'on_server',return_value=False), patch.multiple(runner,_tree=self.tree,_desk=desk,_speaker='Владелец',_turn_in_window=lambda *a,**kw:turns.append((a,kw)) or 'spoken'),patch.dict(sys.modules,{'llm':types.SimpleNamespace(configured=lambda:True)}):
            runner._update_report_due();runner._update_report_due()
        self.assertEqual(len(notes),1);self.assertEqual(len(turns),1)
        self.assertIn('с 1.5.0 до 1.5.1',notes[0]);self.assertIn('Осмотри',notes[0]);self.assertIn('автоматического испытания и отката здесь нет',notes[0])
        self.assertEqual(turns[0][1]['speaker'],updates.SYSTEM_SPEAKER)
        self.assertIsNone(notice.pending(self.tree))
    def test_brain_not_configured_keeps_the_request_for_later(self):
        notice.observe(self.tree,self.program,{'installed':{'version':'1.5.0'}})
        desk=types.SimpleNamespace(archive=lambda *a,**kw:None,life=lambda *a,**kw:None)
        with patch.object(updates,'on_server',return_value=False),patch.multiple(runner,_tree=self.tree,_desk=desk),patch.dict(sys.modules,{'llm':types.SimpleNamespace(configured=lambda:False)}):runner._update_report_due()
        self.assertFalse(notice.reported(self.tree)['done']);self.assertIsNotNone(notice.pending(self.tree))
    def test_carry_to_other_program_cannot_awaken_an_old_update_notice(self):
        notice.observe(self.tree,self.program,{'installed':{'version':'1.5.0'}})
        other=self.root/'other';other.mkdir();notice.write(other/'helene-build.json',{'version':'1.5.2','complete':True})
        notice.observe(self.tree,other,{'installed':{'version':'1.5.0'}})
        self.assertIsNone(notice.pending(self.tree))

if __name__=='__main__':unittest.main()
