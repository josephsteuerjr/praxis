"""Actual tape, route and addressed-followup boundaries across independent languages."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import addressed_threads as at
import frame_epoch as fe
import frame_language as fl
import group_context as gc
import telegram_followups
import telegram_routes as tr
import test_epoch_tape_2010 as fixtures

LANGS=('ru','en','fr','zh')
RAW='Authored Русский 中文 <value>'


class OrientationLanguage(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.EpochSources()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.cfg=self.fixture.base/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.cfg)))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.cfg.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_deleted_evidence_localizes_markers_without_changing_archive_or_self_roles(self):
        self.fixture.prepare();self.fixture.add(7,RAW,outgoing=True)
        gc.observe_deletion(peer_id=fixtures.ROOM,message_id=7,timestamp='2026-10-20T00:00:37Z',origin='live')
        archive=gc.archive_occurrences(fixtures.ROOM)['occurrences']
        occurrence=next(o for o in archive if o.get('row',{}).get('kind')=='message')
        before=gc.archive_path(fixtures.ROOM).read_bytes()
        body_by_frame={}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                row=gc.epoch_occurrence_row(occurrence,deleted_later=True)
                self.assertFalse(row['self']);self.assertTrue(row['deleted_later'])
                self.assertEqual(row['role_line'],row['line'])
                self.assertIn(RAW,row['line'])
                self.assertTrue(row['line'].endswith(fl.text('helper.group_context.deleted_later')))
                expected=body_by_frame.setdefault(frame,row)
                self.assertEqual(row,expected)
                self.assertEqual(before,gc.archive_path(fixtures.ROOM).read_bytes())
        self.assertEqual(len({r['line'] for r in body_by_frame.values()}),4)

    def test_non_deleted_archive_bytes_and_exact_life_identity_do_not_depend_on_language(self):
        self.fixture.add(8,RAW)
        occurrence=gc.archive_occurrences(fixtures.ROOM)['occurrences'][-1]
        expected=gc.epoch_occurrence_row(occurrence)
        signature=gc.epoch_life_signature(occurrence['row'])
        original=copy.deepcopy(occurrence)
        for frame in LANGS:
            self.choose('fr',frame)
            self.assertEqual(gc.epoch_occurrence_row(occurrence),expected)
            self.assertEqual(gc.epoch_life_signature(occurrence['row']),signature)
        self.assertEqual(occurrence,original)

    def test_route_presentation_changes_language_without_changing_scope_or_selector(self):
        selector=fixtures.ROOM+'__topic__11';texts={}
        with patch.object(tr,'status_at',return_value=(tr.UNKNOWN,None)):
            scope=tr.read_scope(fixtures.ROOM,11)
            for ui in LANGS:
                for frame in LANGS:
                    self.choose(ui,frame)
                    line=tr.orientation_line(fixtures.ROOM,11,selector,tr.UNKNOWN)
                    self.assertIn(selector,line);self.assertIn('top_msg_id=11',line)
                    self.assertEqual(tr.read_scope(fixtures.ROOM,11),scope)
                    self.assertEqual(texts.setdefault(frame,line),line)
        self.assertEqual(len(set(texts.values())),4)

    def test_followup_direction_is_clear_and_cross_peer_uid_selection_is_preserved(self):
        item={'id':'fixture','status':'pending','expires_at':1e20,'target_user_id':'42',
              'target_peer_id':'999','target_label':RAW,'sent_excerpt':RAW}
        data={'items':[item]};saved=copy.deepcopy(data)
        with patch.object(at,'_present_ids',return_value={'42'}), patch.object(at,'_slug_map',return_value={}), \
             patch.object(telegram_followups,'_load',return_value=data):
            for frame in LANGS:
                self.choose('en',frame)
                lines=at.collect(SimpleNamespace(chat_id='different-current-peer'))
                self.assertEqual(len(lines),1)
                self.assertIn(RAW,lines[0])
                self.assertEqual(lines[0],fl.text('helper.addressed_threads.collect.130',p0=RAW,p1=RAW))
        self.assertEqual(data,saved)
