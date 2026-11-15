"""Remaining conditional/call receipts: localized defaults, exact inputs, no actual exit."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import agent
import frame_language as frame

RAW='Windows Егор 中文 {literal} $&'

class ProducerTail(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(agent,'_reindex'))
    def text(self,language,key,**values):
        with frame.use(language):return frame.text('agent.receipt.'+key,**values)
    def test_remember_preserves_authored_fact_links_scope_and_source_all16_axes(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent.graph,'resolve',return_value='person-id'),patch.object(agent.people,'append_fact',return_value=True) as write,patch.object(agent.people,'path_for',return_value=self.root/'person.md'),patch.object(agent.memory_index,'ensure_index_line'),patch.object(agent.graph,'add_edge',return_value=RAW) as edge,patch.object(agent,'_active_memory_source_ref',return_value='literal:source'):
                    out=agent.tool_remember(RAW,RAW,visibility='private',relates_to=RAW,relation=RAW)
                    self.assertEqual(out,self.text(language,'remember',person=RAW,visibility='private',linked=' '+RAW))
                    write.assert_called_once_with('person-id',RAW,RAW,'private',2,source_ref='literal:source')
                    edge.assert_called_once_with(RAW,RAW,RAW,source='literal:source')
    def test_schedule_cancel_outcome_and_id_are_not_inferred_from_translated_text(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                for success in (True,False):
                    with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent.tasks,'cancel',return_value=success) as cancel:
                        self.assertEqual(agent.tool_unschedule(RAW),self.text(language,'cancelled' if success else 'cancel_missing',id=RAW))
                        cancel.assert_called_once_with(RAW)
    def test_panic_records_literal_or_localized_default_and_only_requests_mocked_exit_all16_axes(self):
        path=self.root/'panic'
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                for reason in (RAW,''):
                    with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'PANIC_SENTINEL',path),patch.object(agent,'_schedule_exit') as exit_request,patch.object(agent,'tool_journal') as journal:
                        self.assertEqual(agent.tool_panic(reason),self.text(language,'panic'))
                        expected=reason or self.text(language,'choice')
                        self.assertTrue(path.read_text(encoding='utf-8').endswith(' '+expected))
                        journal.assert_called_once_with('[panic] '+expected,salience=3);exit_request.assert_called_once()
    def test_panic_write_failure_does_not_claim_saved_sentinel_or_prevent_exit_request(self):
        for language in frame.LANGUAGES:
            missing=Mock();missing.parent.mkdir.side_effect=OSError('fixture denied')
            with frame.use(language),patch.object(agent,'PANIC_SENTINEL',missing),patch.object(agent,'_schedule_exit') as exit_request,patch.object(agent,'tool_journal') as journal:
                self.assertEqual(agent.panic(RAW),self.text(language,'panic'))
                missing.write_text.assert_not_called();journal.assert_not_called();exit_request.assert_called_once()
