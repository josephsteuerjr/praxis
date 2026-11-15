"""Actual native runner suppliers with source bytes, typed receipts and legacy cores."""
import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import agent
import frame_language as fl
import media
import runner
from localharness.transport import Desk

LANGS=('ru','en','fr','zh')
RAW='Authored Русский 中文 {owner}'


class NativeRunnerFrame(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.config=self.root/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.config)))
        self.enterContext(patch.object(runner,'_agent',agent))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_alarm_uses16_axes_and_never_dispatches_or_changes_the_task(self):
        task={'kind':'message','target':RAW,'goal':RAW};before=copy.deepcopy(task);by_frame={}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                text=runner._alarm_note(task)
                self.assertIn(RAW,text);self.assertIn('send_message',text)
                self.assertEqual(by_frame.setdefault(frame,text),text)
                self.assertEqual(task,before)
        self.assertEqual(len(set(by_frame.values())),4)

    def test_actual_file_copy_is_literal_idempotent_and_preserves_changed_source(self):
        inbox=self.root/'memory/.control/desk_inbox/attachments';inbox.mkdir(parents=True)
        path=inbox/(RAW+'.txt');path.write_bytes(b'ORIGINAL')
        run=self.root/'memory/runs/fixture'
        fake=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl,_runs=lambda:SimpleNamespace(path=lambda _:run))
        with patch.object(runner,'_agent',fake),patch.object(runner,'_tree',self.root):
            for frame in LANGS:
                self.choose('zh',frame)
                text=runner._batch_files(['attachments/'+path.name],run_id='fixture')
                self.assertIn(RAW,text[0]);self.assertEqual((run/'files'/path.name).read_bytes(),b'ORIGINAL')
                self.assertEqual(path.read_bytes(),b'ORIGINAL')
            path.write_bytes(b'CHANGED')
            text=runner._batch_files(['attachments/'+path.name],run_id='fixture')
        self.assertEqual((run/'files'/path.name).read_bytes(),b'ORIGINAL')
        self.assertEqual(path.read_bytes(),b'CHANGED');self.assertEqual(len(list((run/'files').glob('*.txt'))),2)

    def test_ingress_preflight_keeps_traversal_refused_before_spool_effect(self):
        spool=SimpleNamespace(ingest_path=Mock(side_effect=AssertionError('read external')))
        fake=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl,_media_spool=lambda:spool)
        for frame in LANGS:
            self.choose('en',frame)
            with patch.object(runner,'_agent',fake),patch.object(runner,'_tree',self.root):
                refs,notes=runner._ingest_attachments(['../../../external.png'],chat_id='window',message_id='m1')
            self.assertEqual(refs,[]);self.assertIn('external.png',notes[0]);spool.ingest_path.assert_not_called()

    def test_birth_is_frame_localized_and_retired_across_language_change(self):
        desk=Desk(self.root,'window','owner','Chat');by_frame={}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame);capture=SimpleNamespace(archive=Mock(),life=Mock())
                with patch.object(runner,'_desk',capture),patch.object(runner,'_tree',self.root), \
                     patch.object(runner,'_agent_name',RAW),patch.object(runner,'_speaker',RAW), \
                     patch.object(runner.platform,'node',return_value=RAW):
                    runner._birth_note()
                note=capture.archive.call_args.args[0]
                self.assertEqual(capture.life.call_args.args[0],note)
                self.assertIn(RAW,note);self.assertIn(str(self.root),note)
                self.assertEqual(by_frame.setdefault(frame,note),note)
        self.assertEqual(len(set(by_frame.values())),4)
        for frame,note in by_frame.items():
            desk.archive(note,outgoing=False,now=runner._now(),sender='Hélène')
        desk.archive(RAW,outgoing=False,now=runner._now(),sender='owner')
        self.choose('zh','zh');marker=self.root/'born.json'
        with patch.object(runner,'_desk',desk):runner._retire_birth_note(marker,{'status':'done'})
        rows=[json.loads(line) for line in (self.root/'memory/groups/window.jsonl').read_text().splitlines()]
        retired=[row for row in rows if row.get('kind')=='birth']
        self.assertEqual(len(retired),4);self.assertTrue(all(row.get('system') for row in retired))
        self.assertEqual({row['text'] for row in retired},set(by_frame.values()))
        self.assertTrue(json.loads(marker.read_text())['note_retired'])
        self.assertFalse(rows[-1].get('system'))

    def test_orientation_is_late_and_does_not_claim_git_absence_or_global_irreversibility(self):
        with patch.object(runner,'_bot',None),patch.object(runner,'_install_root',None), \
             patch.object(runner,'_tree',None),patch.object(runner,'_ORIENT_EXTRA',''):
            self.choose('zh','en');self.assertIn('reply',runner._orient('window'))
        with patch.object(runner,'_git_state',return_value={'repo':False,'exe':True}),patch.object(runner,'_ORIENT_EXTRA',''):
            runner._announce_git(self.root)
            self.choose('zh','en');text=runner._ORIENT_EXTRA()
            self.assertIn('not both confirmed ready',text);self.assertNotIn('no git',text)
            self.choose('en','fr');self.assertNotEqual(runner._ORIENT_EXTRA(),text)

    def test_legacy_core_keeps_original_text_and_does_not_rewrite_authored_data(self):
        self.choose('en','zh')
        with patch.object(runner,'_agent',SimpleNamespace()):
            self.assertEqual(runner._frame_word('unknown','Literal {value}',value=RAW),'Literal '+RAW)
            self.assertEqual(runner._birth_prefixes(),runner._BIRTH_PREFIX)
            self.assertTrue(runner._alarm_note({'goal':RAW}).startswith('Сработал твой будильник'))

    def test_verified_historical_voice_caption_uses16_axes_without_asr_or_archive_rewrite(self):
        folder=self.root/'memory/telegram-media';folder.mkdir(parents=True)
        audio=folder/'voice.ogg';audio.write_bytes(b'fixture audio bytes')
        archive=self.root/'memory/groups/-77.jsonl';archive.parent.mkdir(parents=True)
        row={'media_kind':'audio','media_path':'memory/telegram-media/voice.ogg','sender_name':RAW,'source_message_id':'9'}
        archive.write_text(json.dumps(row,ensure_ascii=False)+'\n',encoding='utf-8');before=archive.read_bytes()
        receipt={'status':'ready','sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'transcript':RAW}
        fake=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl,_media_spool=lambda:SimpleNamespace(max_turn_media=4))
        ctx=SimpleNamespace(owner=True,is_dm=False,scope='group',chat_id='-77');by_frame={}
        with patch.object(runner,'_agent',fake),patch.object(runner,'_tree',self.root), \
             patch.object(runner.voice_admission,'state',return_value=receipt), \
             patch.object(agent.media_audio,'transcribe',side_effect=AssertionError('historical ASR')) as asr:
            for ui in LANGS:
                for frame in LANGS:
                    self.choose(ui,frame);refs,notes=runner._telegram_media_refs('-77',ctx)
                    self.assertEqual(refs,[]);self.assertEqual(len(notes),1)
                    self.assertEqual(notes[0].count(RAW),2);self.assertIn('9',notes[0])
                    self.assertEqual(by_frame.setdefault(frame,notes),notes)
                    self.assertEqual(archive.read_bytes(),before)
            asr.assert_not_called()
        self.assertEqual(len({tuple(v) for v in by_frame.values()}),4)
        with patch.object(runner,'_agent',fake),patch.object(runner,'_tree',self.root), \
             patch.object(runner.voice_admission,'state',return_value={**receipt,'sha256':'0'*64}):
            self.assertEqual(runner._telegram_media_refs('-77',ctx),([],[]))

    def test_actual_sampled_media_keeps_group_scope_bytes_and_literal_caption(self):
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZioAAAAASUVORK5CYII=')
        folder=self.root/'memory/telegram-media';folder.mkdir(parents=True)
        source=folder/'original.mp4';source.write_bytes(b'fixture original')
        image=folder/'preview.png';image.write_bytes(png)
        archive=self.root/'memory/groups/-77.jsonl';archive.parent.mkdir(parents=True)
        row={'media_kind':'video','media_path':'memory/telegram-media/original.mp4','media_preview_path':'memory/telegram-media/preview.png',
             'media_name':'анимация','sender_name':RAW,'text':RAW,'source_message_id':'42'}
        archive.write_text(json.dumps(row,ensure_ascii=False)+'\n',encoding='utf-8');before=archive.read_bytes()
        spool=media.MediaSpool(self.root/'spool')
        fake=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl,_media_spool=lambda:spool)
        ctx=SimpleNamespace(owner=True,is_dm=False,scope='group',chat_id='-77')
        for frame in LANGS:
            self.choose('fr',frame)
            with patch.object(runner,'_agent',fake),patch.object(runner,'_tree',self.root), \
                 patch('telegram_media.vision_frames',return_value=[(image,0.25),(image,0.5)]):
                refs,notes=runner._telegram_media_refs('-77',ctx)
            self.assertEqual(len(refs),2);self.assertIn('анимация',notes[0])
            self.assertIn('0.250',notes[0]);self.assertIn('0.500',notes[0])
            for ref in refs:
                self.assertEqual(ref.scope,'group');self.assertEqual(ref.message_id,'42')
                self.assertEqual(Path(ref.path).read_bytes(),png);self.assertIn(RAW,ref.caption)
            self.assertEqual(archive.read_bytes(),before);self.assertEqual(source.read_bytes(),b'fixture original')
