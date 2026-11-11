"""The model sees a usable task-local contract; no authority or schema drift."""
import inspect
import unittest
from unittest.mock import patch

import editor
import forge
import forge_worker as worker
import hands
import workshop


class WorkerHandsContract(unittest.TestCase):
    def tools(self):
        return [worker.INSPECT_TOOL, worker.SIGNAL_TOOL, worker.DELEGATE_TOOL,
                worker.TRANSCRIPT_TOOL, worker.EDIT_TOOL, worker.RUN_TOOL,
                worker.PROCESS_TOOL, worker.VERIFY_TOOL, worker.CHECKPOINT_TOOL]

    def test_parameters_tell_worker_what_to_pass(self):
        for tool in self.tools():
            for name, param in tool['input_schema']['properties'].items():
                with self.subTest(tool=tool['name'], parameter=name):
                    self.assertTrue(param.get('description', '').strip())
                    self.assertFalse(any('\u0400' <= c <= '\u04ff' for c in param['description']))

    def test_edit_description_uses_only_worker_address_and_eyes(self):
        text = worker.EDIT_TOOL['description']
        for term in ('inspect(action=read)', 'praxis_hands', 'h.edit', 'snapshot',
                     'sequential', 'confirmed', 'refused', 'in_doubt', 'partial', 'rollback'):
            self.assertIn(term, text)
        for unavailable in ('proposal_id', 'fs_read', 'coding_inspect', 'overwrite', 'force', 'task_id'):
            self.assertNotIn(unavailable, text)
        self.assertEqual(worker.EDIT_TOOL['input_schema'], editor.schema(worker=True))

    def test_operating_contract_invites_python_with_the_same_editor(self):
        with patch.object(forge, 'inspect', return_value='orientation'):
            text = worker._system({'role':'worker', 'task_id':'t', 'id':'w'})
        for term in ('`edit`', 'inspect(action=read)', 'praxis_hands', 'h.edit'):
            self.assertIn(term, text)

    def test_replies_name_existing_project_hands(self):
        self.assertIn('shell/python', workshop.run('true', 'missing-project-for-contract'))
        with patch.object(workshop.selfdev, 'all_items', return_value=[]):
            self.assertIn('proposal(action=test)', workshop.run_tests('self'))

    def test_empty_batch_has_no_phantom_file_receipt(self):
        result={'status':'refused','message':'no files','files':[]}
        with patch.object(hands, 'receipt') as receipt:
            self.assertEqual(editor._receipted('edit','',result),result)
        receipt.assert_not_called()

    def test_process_schema_does_not_leak_batch_parameters(self):
        self.assertNotIn('files', inspect.signature(forge.process).parameters)
        self.assertNotIn('mode', inspect.signature(forge.process).parameters)


if __name__ == '__main__':
    unittest.main()
