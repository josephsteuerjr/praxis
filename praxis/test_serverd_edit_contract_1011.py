import contextlib
from pathlib import Path
import unittest
from unittest.mock import patch

import editor
import forge
import serverd_client as client


class RemoteEditContract(unittest.TestCase):
    def test_only_an_explicit_no_effect_stage_is_a_refusal(self):
        for result,expected in [({'ok':False,'stage':'preflight'},'refused'),
                                ({'ok':False,'stage':'preflight','type':'accepted'},'in_doubt'),
                                ({'ok':False,'stage':'preflight','after_error':'lost'},'in_doubt'),
                                ({'ok':False,'stage':'apply'},'in_doubt'),
                                ({'ok':False,'error':'bad input'},'in_doubt'),
                                ({'ok':True,'after_error':'lost'},'in_doubt'),
                                ({'ok':True,'replacements':3},'ok')]:
            self.assertEqual(editor.remote_result(result)['status'],expected)
        self.assertEqual(editor.remote_result({'ok':True,'replacements':3})['replacements'],3)

    def test_all_negotiates_before_sending_and_preserves_exact_intent(self):
        response={'ok':True,'text':'done','replacements':2}
        with patch.object(client,'call',side_effect=[{'ok':True,'features':{'edit_all_v1':True}},response]) as call:
            self.assertEqual(client.workspace_edit_result('/task','replace',path='a',old='x',new='y',all=True),response)
        self.assertEqual(call.call_args_list[0].args,('admin.status',{}))
        self.assertEqual(call.call_args_list[1].args,('workspace.edit',dict(root='/task',action='replace',path='a',old='x',new='y',all=True)))

    def test_unknown_old_or_unreachable_daemon_never_receives_the_edit(self):
        for value in [None,{'ok':True},{'ok':False},{'ok':True,'features':[]},
                      {'ok':True,'features':{'edit_all_v1':'true'}}]:
            with patch.object(client,'call',return_value=value) as call:
                result=client.workspace_edit_result('/task','replace',all=True)
            self.assertEqual(editor.remote_result(result)['status'],'refused')
            self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.args[0],'admin.status')
        with patch.object(client,'call',side_effect=OSError('no status reply')) as call:
            result=client.workspace_edit_result('/task','replace',all=True)
        self.assertEqual(editor.remote_result(result)['status'],'refused');self.assertEqual(call.call_count,1)

    def test_normal_replacement_keeps_the_existing_single_request(self):
        with patch.object(client,'call',return_value={'ok':True}) as call:
            client.workspace_edit_result('/task','replace',path='a',old='x',new='y')
        call.assert_called_once_with('workspace.edit',dict(root='/task',action='replace',path='a',old='x',new='y'))

    def test_forge_routes_all_to_negotiating_host_client(self):
        task={'scope':'host'}
        response={'ok':True,'text':'done','replacements':2,'after':'a'*16}
        with patch.object(forge,'_task_root',return_value=(task,Path('/task'),'')), \
             patch.object(forge,'_remote_client',return_value=client), \
             patch.object(forge,'_remote_observations',return_value=contextlib.nullcontext()), \
             patch.object(client,'call',side_effect=[{'ok':True,'features':{'edit_all_v1':True}},response]) as call:
            result=forge._edit_unlocked('host','replace',path='a',old='x',new='y',all=True,_typed_result=True)
        self.assertIn('"replacements": 2',result);self.assertEqual(call.call_count,2)


if __name__=='__main__':unittest.main()
