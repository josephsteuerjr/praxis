"""Telegram binary intake -> durable archive -> the real turn's vision refs."""
import base64
import gzip
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'desk/localharness'),str(ROOT/'praxis')]
import botapi
import runner
import telegram_media as tm
import media

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZioAAAAASUVORK5CYII=')


class Media(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.tree=Path(tmp.name)
        self.api=Mock()
        self.api.download_media.side_effect=lambda message,path:path.write_bytes(PNG)
        self.api.download_file.side_effect=lambda ident,path:path.write_bytes(PNG)
    def message(self,kind,**info):
        return {'message_id':23,kind:{'file_id':'actual-file','file_unique_id':'stable-file',**info}}
    def test_static_sticker_bytes_are_archived_before_a_wake_gate(self):
        transport=botapi.BotTransport(types.SimpleNamespace(),self.tree,None,{'agent':{'name':'Лира'},'telegram':{}})
        transport.client=self.api; transport.me={'id':99}; transport.username='lyra'
        message=self.message('sticker',emoji='🐈')
        message.update(chat={'id':-77,'type':'group','title':'Room'},**{'from':{'id':11,'first_name':'Автор'}})
        transport._ingest({'message':message})
        row=json.loads((self.tree/'memory/groups/-77.jsonl').read_text(encoding='utf-8'))
        self.assertEqual((self.tree/row['media_path']).read_bytes(),PNG)
        self.assertEqual(row['sender_name'],'Автор')
        self.assertEqual(row['media_kind'],'image')
        self.assertIsNone(transport.pop_pending())
    def test_real_turn_receives_spooled_preview_and_motion_note(self):
        row=tm.persist(self.api,self.tree,'-77',self.message('animation',mime_type='video/mp4',thumbnail={'file_id':'thumb'}))
        row['source_message_id']='23'
        archive=self.tree/'memory/groups/-77.jsonl'; archive.parent.mkdir(parents=True); archive.write_text(json.dumps(row),encoding='utf-8')
        spool=media.MediaSpool(self.tree/'memory/media')
        ctx=types.SimpleNamespace(owner=False,is_dm=False,known=True)
        with patch.multiple(runner,_tree=self.tree,_agent=types.SimpleNamespace(_media_spool=lambda:spool)):
            refs,notes=runner._telegram_media_refs('-77',ctx)
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0].scope,'group')
        self.assertEqual(refs[0].message_id,'23')
        self.assertIn('не вся анимация',notes[0])
        self.assertEqual(Path(refs[0].path).read_bytes(),PNG)
    def test_thumb_failure_keeps_playable_original_without_partial_preview(self):
        def broken(ident,path): path.write_bytes(b'partial'); raise OSError('offline')
        self.api.download_file.side_effect=broken
        row=tm.persist(self.api,self.tree,'-77',self.message('animation',mime_type='video/mp4',thumbnail={'file_id':'thumb'}))
        self.assertTrue((self.tree/row['media_path']).is_file())
        self.assertNotIn('media_preview_path',row)
        self.assertFalse(list((self.tree/'memory/telegram-media').glob('*.part')))
    def test_tgs_is_decompressed_and_external_assets_rejected(self):
        self.api.download_media.side_effect=lambda message,path:path.write_bytes(gzip.compress(json.dumps({'v':'5.5','assets':[]}).encode()))
        row=tm.persist(self.api,self.tree,'-77',self.message('sticker',is_animated=True))
        self.assertEqual(row['media_kind'],'sticker_tgs')
        self.assertEqual(json.loads((self.tree/row['media_path']).read_text())['assets'],[])
        self.assertTrue((self.tree/row['media_original_path']).read_bytes().startswith(b'\x1f\x8b'))
        self.api.download_media.side_effect=lambda message,path:path.write_bytes(gzip.compress(json.dumps({'assets':[{'u':'https://outside/','p':'x'}]}).encode()))
        with self.assertRaises(ValueError): tm.persist(self.api,self.tree,'-78',self.message('sticker',is_animated=True,file_unique_id='other-animation'))
    def test_failed_download_does_not_leave_a_corrupt_final_file(self):
        def broken(message,path): path.write_bytes(b'partial'); raise OSError('offline')
        self.api.download_media.side_effect=broken
        with self.assertRaises(OSError): tm.persist(self.api,self.tree,'-77',self.message('photo'))
        self.assertFalse(list((self.tree/'memory/telegram-media').iterdir()))
    def test_vision_cannot_read_a_path_outside_telegram_storage(self):
        archive=self.tree/'memory/groups/-77.jsonl'; archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({'media_preview_path':'secret.png'}),encoding='utf-8')
        spool=Mock(max_turn_media=4)
        with patch.multiple(runner,_tree=self.tree,_agent=types.SimpleNamespace(_media_spool=lambda:spool)):
            refs,_=runner._telegram_media_refs('-77',types.SimpleNamespace(owner=True))
        self.assertFalse(refs); spool.ingest_path.assert_not_called()

    def test_gif_original_plays_and_vision_gets_a_png_frame(self):
        import io
        from PIL import Image
        data=io.BytesIO()
        Image.new('RGB',(2,2),'red').save(data,format='GIF')
        self.api.download_media.side_effect=lambda message,path:path.write_bytes(data.getvalue())
        row=tm.persist(self.api,self.tree,'-77',self.message('animation',mime_type='image/gif'))
        self.assertTrue((self.tree/row['media_path']).read_bytes().startswith(b'GIF'))
        self.assertTrue((self.tree/row['media_preview_path']).read_bytes().startswith(b'\x89PNG'))

    def test_window_file_reaches_room_as_private_owner_instruction_and_vision(self):
        inbox=self.tree/'memory/.control/desk_inbox';inbox.mkdir(parents=True)
        picture=inbox/'cat.png';picture.write_bytes(PNG)
        bot=botapi.BotTransport(types.SimpleNamespace(),self.tree,None,{'agent':{'name':'Лира'},'telegram':{'owner_id':'42'}})
        bot.owner_id='42'
        bot._wake_senders['-77']=('Другой автор','11')
        with patch.multiple(runner,_tree=self.tree,_bot=bot,_speaker='Владелец'),patch.object(runner,'handle_bot') as turn:
            runner.handle_owner_note('-77','Посмотри картинку', ['cat.png'],ingress_id='note:actual-note')
        turn.assert_called_once_with('-77',sender_override=('Владелец','42'))
        row=json.loads((self.tree/'memory/groups/-77.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(row['source'],'window');self.assertEqual(row['sender_id'],'42')
        self.assertEqual((self.tree/row['media_path']).read_bytes(),PNG)
        spool=media.MediaSpool(self.tree/'memory/media')
        with patch.multiple(runner,_tree=self.tree,_agent=types.SimpleNamespace(_media_spool=lambda:spool)):
            refs,_=runner._telegram_media_refs('-77',types.SimpleNamespace(owner=True,is_dm=False))
        self.assertEqual(len(refs),1);self.assertEqual(refs[0].scope,'owner')
        self.assertEqual(bot.wake_sender('-77'),('Другой автор','11'))

    def test_window_instruction_rejects_an_attachment_outside_its_inbox(self):
        inbox=self.tree/'memory/.control/desk_inbox';inbox.mkdir(parents=True)
        (self.tree/'secret.png').write_bytes(PNG)
        bot=botapi.BotTransport(types.SimpleNamespace(),self.tree,None,{'telegram':{}})
        with patch.multiple(runner,_tree=self.tree,_bot=bot,_speaker='Владелец'),patch.object(runner,'handle_bot') as turn:
            with self.assertRaises(ValueError):runner.handle_owner_note('-77','read',['../../../secret.png'])
        turn.assert_not_called()

    def test_voice_uses_the_agent_hearing_and_its_enabled_gate(self):
        folder=self.tree/'memory/telegram-media';folder.mkdir(parents=True)
        audio=folder/'voice.ogg';audio.write_bytes(b'codec fixture')
        archive=self.tree/'memory/groups/-77.jsonl';archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({'media_kind':'audio','media_path':'memory/telegram-media/voice.ogg','sender_name':'Автор','source_message_id':'9'}),encoding='utf-8')
        transcribe=Mock(return_value='проверь картинки и стикеры')
        context=types.SimpleNamespace(owner=True,is_dm=True)
        with patch.multiple(runner,_tree=self.tree,_agent=types.SimpleNamespace(_media_spool=lambda:Mock(max_turn_media=4)),_voice_state={'ready':False,'why':'расшифровка выключена'}),patch.dict(sys.modules,{'media_audio':types.SimpleNamespace(transcribe=transcribe)}):
            _,notes=runner._telegram_media_refs('-77',context)
            self.assertIn('выключена',notes[0]);transcribe.assert_not_called()
        with patch.multiple(runner,_tree=self.tree,_agent=types.SimpleNamespace(_media_spool=lambda:Mock(max_turn_media=4)),_voice_state={'ready':True}),patch.dict(sys.modules,{'media_audio':types.SimpleNamespace(transcribe=transcribe)}):
            _,notes=runner._telegram_media_refs('-77',context)
            self.assertIn('проверь картинки и стикеры',notes[0]);self.assertIn('Автор',notes[0]);transcribe.assert_called_once_with(audio.resolve())
            runner._telegram_media_refs('-77',context);transcribe.assert_called_once()

    def test_network_failure_preserves_file_identity_and_retry_cannot_replace_an_edit(self):
        bot=botapi.BotTransport(types.SimpleNamespace(),self.tree,None,{'agent':{'name':'Лира'},'telegram':{}})
        bot.client=self.api;bot.me={'id':99};bot.username='lyra'
        message=self.message('sticker',emoji='🐈');message.update(chat={'id':-77,'type':'group','title':'Room'},**{'from':{'id':11,'first_name':'Автор'}})
        self.api.download_media.side_effect=OSError('offline')
        bot._ingest({'message':message})
        archive=self.tree/'memory/groups/-77.jsonl'
        row=json.loads(archive.read_text(encoding='utf-8'));self.assertEqual(row['media_source']['sticker']['file_id'],'actual-file')
        self.api.download_media.side_effect=lambda message,path:path.write_bytes(PNG)
        with patch.object(tm.time,'time',return_value=10**12):tm.recover(self.tree,self.api,bot.rooms)
        row=json.loads(archive.read_text(encoding='utf-8'));self.assertEqual((self.tree/row['media_path']).read_bytes(),PNG);self.assertNotIn('media_error',row)
        self.assertIsNone(bot.pop_pending())
        old=tm.source(message)
        message['sticker']={'file_id':'changed-file','file_unique_id':'changed','emoji':'🦊'}
        bot._ingest({'edited_message':message})
        self.assertFalse(bot.rooms.attach_media('-77','23',old,{'media_path':'stale.webp'}))

if __name__=='__main__': unittest.main()
