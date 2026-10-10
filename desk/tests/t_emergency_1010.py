"""Emergency controls and outgoing Telegram media through their real boundaries."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK/'localharness'), str(DESK.parent/'praxis')]
import botapi
import emergency
import owner_stop
from deskd import readers

PNG = b'\x89PNG\r\n\x1a\nsynthetic-image'


class Emergency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root/'helene.json'
        self.config.write_text(json.dumps({'tree':'data'}))
        (self.root/'helene-svc.exe').write_bytes(b'fixture-never-executed')
    def transport(self, username='lyra'):
        target = botapi.BotTransport(types.SimpleNamespace(), self.root/'data', None,
            {'agent':{'name':'Лира'}, 'telegram':{'owner_id':'17'}})
        target.username = username; target.me = {'id':91}
        target.owner_control = Mock(); target.client = Mock()
        return target
    def send(self, target, text, sender=17, **fields):
        message = {'message_id': 4, 'text': text, 'from':{'id':sender,'first_name':'Sender'},
                   'chat':{'id':-77,'type':'group','title':'Synthetic'}, **fields}
        target._ingest({'message':message})
    def test_owner_panic_has_priority_without_address_or_brain(self):
        target = self.transport()
        target._addressed = Mock(return_value=False)
        self.send(target,'/panic')
        target.owner_control.assert_called_once()
        self.assertIsNone(target.pop_pending())
        archive = self.root/'data/memory/groups/-77.jsonl'
        self.assertEqual(json.loads(archive.read_text())['text'],'/panic')
    def test_authenticated_suffix_and_owner_only(self):
        for text, sender, accepted in [('/panic@LYRA',17,True),('/panic@other',17,False),
                                      ('/panic',18,False),('/panic@lyra reason',17,True),
                                      ('please /panic',17,False)]:
            with self.subTest(text=text,sender=sender):
                target = self.transport(); self.send(target,text,sender)
                self.assertEqual(target.owner_control.called,accepted)
    def test_edit_and_history_do_not_trigger_emergency(self):
        target=self.transport()
        message={'message_id':5,'text':'/panic','from':{'id':17},'chat':{'id':17,'type':'private'}}
        target._ingest({'message':message,'_history':True})
        target._ingest({'edited_message':message})
        target.owner_control.assert_not_called()
    def test_windows_service_stop_uses_authenticated_pipe_without_shell_or_ui(self):
        with patch.object(emergency,'WINDOWS',True), patch.object(owner_stop,'stopped',side_effect=[False,True]), \
             patch('broker.read_token',return_value='synthetic-secret'), patch('broker.call_service',return_value={'ok':True}) as pipe, \
             patch.object(emergency.subprocess,'run') as spawn:
            result=emergency.request(self.config,via='agent')
        self.assertTrue(result['latched']); spawn.assert_not_called()
        request=pipe.call_args.args[1]
        self.assertEqual((request['op'],request['args']),('panic',['agent']))
        self.assertNotIn('cmd',request)
    def test_pipe_receipt_without_latch_is_not_success(self):
        with patch.object(emergency,'WINDOWS',True), patch.object(owner_stop,'stopped',return_value=False), \
             patch('broker.read_token',return_value='synthetic-secret'), patch('broker.call_service',return_value={'ok':True}):
            with self.assertRaisesRegex(RuntimeError,'не подтверждён'):
                emergency.request(self.config,via='phone')
    def test_posix_latch_survives_repeated_stop_and_denies_resume_by_autonomy(self):
        path=self.root/'owner-state/stop.json'
        with patch.object(emergency,'WINDOWS',False), patch.object(owner_stop,'seed_file',return_value=path):
            emergency.request(self.config,via='agent',reason='requested self stop')
            original=path.read_bytes()
            self.assertTrue(owner_stop.stopped())
            emergency.request(self.config,via='telegram')
            self.assertEqual(path.read_bytes(),original)
            owner_stop.configure(self.root/'data')
            with self.assertRaises(RuntimeError): owner_stop.require_admission()
            with self.assertRaises(RuntimeError): owner_stop.resume()
    def test_outgoing_picture_survives_delivery_source_removal_and_is_in_chat_reader(self):
        target=self.transport()
        target.client.upload.return_value={'message_id':29}
        source=self.root/'transient.png';source.write_bytes(PNG)
        target.deliver_file(source,chat_id='-77',media_kind='photo')
        source.unlink()
        with patch.object(readers,'tree',return_value=self.root/'data'):
            rows=readers.chat_tail('-77')
            self.assertEqual(rows[0]['media_kind'],'image')
            self.assertEqual((self.root/'data'/rows[0]['media_path']).read_bytes(),PNG)
            self.assertEqual(rows[0]['source_message_id'],'29')
    def test_failed_upload_does_not_publish_picture_as_delivered(self):
        target=self.transport();target.client.upload.side_effect=OSError('offline')
        source=self.root/'transient.png';source.write_bytes(PNG)
        with self.assertRaises(OSError): target.deliver_file(source,chat_id='-77',media_kind='photo')
        self.assertFalse((self.root/'data/memory/groups/-77.jsonl').exists())


if __name__ == '__main__': unittest.main()
