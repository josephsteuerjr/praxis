"""Explicit causal work links, real ledger and notice renderer, no inference."""
import importlib.util
import ast
import types
from unittest.mock import Mock
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'helene/core'), str(ROOT/'praxis')]
import authored_notes
spec=importlib.util.spec_from_file_location('helene_notice_notes_test', ROOT/'helene/core/core/notices.py')
notices=importlib.util.module_from_spec(spec);spec.loader.exec_module(notices)


class LinkedNotes(unittest.TestCase):
    def test_only_exact_explicit_work_and_owner_audience_receive_text(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);ledger=authored_notes.AuthoredNoteLedger(base)
            note=ledger.write('Investigating window recovery; next retry is not yet tested.',
                work_ref='forge:code-aaaaaaaa:agent-bbbbbbbb',audience='owner',chat_id='window',source_ref='run:fixture:call:1')
            ledger.write('OTHER WORK PRIVATE',work_ref='forge:code-cccccccc:agent-dddddddd',audience='owner',chat_id='window')
            with patch.object(notices,'BASE',base),patch.object(notices,'STATE_FILE',base/'notices.json'):
                row=notices.note_node(task_id='code-aaaaaaaa',unit_id='agent-bbbbbbbb',status='done',
                    origin_chat='window',result_ref={'path':'result.json','sha256':'abc'})
                text=notices._line(row,owner_context=True,fresh=True)
                self.assertIn(note['id'],text);self.assertIn('Investigating window',text)
                self.assertIn('Full result (separate evidence)',text)
                self.assertNotIn('OTHER WORK PRIVATE',text)
                public=notices._line(row,owner_context=False,fresh=True)
                for secret in (note['id'],'Investigating window','result.json','run:fixture'):
                    self.assertNotIn(secret,public)
                ledger.close(note['id'])
                self.assertNotIn('Investigating window',notices._line(row,owner_context=True,fresh=True))

    def test_actual_tool_chain_and_decline_enforce_audience(self):
        source=ast.parse((ROOT/'helene/core/agent.py').read_text(encoding='utf-8'))
        function=next(node for node in source.body if isinstance(node,ast.FunctionDef) and node.name=='tool_manage_notes')
        with tempfile.TemporaryDirectory() as directory:
            ledger=authored_notes.AuthoredNoteLedger(directory)
            note=ledger.write('OWNER ONLY',work_ref='forge:code-aaaaaaaa',chat_id='private')
            ctx=types.SimpleNamespace(owner_audience=False,origin_message_id=1)
            lessons=types.SimpleNamespace(chain=Mock(),decline=Mock())
            namespace={'authored_notes':authored_notes,'BASE':Path(directory),'run_context':types.SimpleNamespace(current_run=lambda:None),
                       '_TURN_CHANNEL':types.SimpleNamespace(get=lambda:ctx),'_active_chat':lambda:'public','lessons':lessons}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'agent.py','exec'),namespace)
            tool=namespace['tool_manage_notes']
            self.assertIn('аудитории',tool('chain',note_id='known-skill'))
            self.assertIn('аудитории',tool('decline',note_id=note['id']))
            lessons.chain.assert_not_called();lessons.decline.assert_not_called()

    def test_missing_note_is_not_an_invented_summary_and_legacy_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);ledger=authored_notes.AuthoredNoteLedger(base)
            note=ledger.write('LEGACY GLOBAL PRIVATE',chat_id='private-dm')
            self.assertFalse(authored_notes.visible(note,chat_id='public-room'))
            self.assertTrue(authored_notes.visible(note,owner=True))
            self.assertEqual(authored_notes.for_notice(base,{'work_ref':'forge:code-aaaaaaaa:agent-aaaaaaaa','task_id':'code-aaaaaaaa'},owner=True),'')
            with self.assertRaises(ValueError): ledger.write('x',chat_id='one',audience='chat:other')
            with self.assertRaises(ValueError): ledger.write('x',work_ref='latest-in-this-chat')


if __name__=='__main__':unittest.main()
