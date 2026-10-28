"""Actual durable dispatch and offline SDK consumers of exact read/admission."""
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import agent
import llm
import media
import memory_life
import run_context
from run_context import RunContext
from run_manager import RunManager
from test_coverage_vs_current import Base, ROOM


@contextmanager
def sdk_fixture(root, create):
    """Use the real provider adapter; replace only SDK/network and unrelated UI."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(llm, 'CONFIG_PATH', root/'offline-llm.json'))
        stack.enter_context(patch.object(llm, '_CACHE', {'mtime': None, 'cfg': None}))
        stack.enter_context(patch.dict(llm._TEST_CLIENTS, {}, clear=True))
        config = llm._from_env()
        config['frameworks']['anthropic']['api_key'] = 'synthetic-offline-key'
        config['roles']['voice'].update(framework='anthropic', model='claude-sonnet-4')
        config['roles']['voice'].pop('fallback_framework', None)
        llm.save_config(config)
        llm.use_test_client(NS(messages=NS(create=create)), 'anthropic')
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('offline test attempted network')))
        stack.enter_context(patch.object(agent, '_arm_private_floor', return_value=0))
        stack.enter_context(patch.object(agent, '_drain_owner_input', return_value=False))
        stack.enter_context(patch.object(agent, '_work_loop_continue', return_value=False))
        yield


def response(*, call=None):
    return NS(stop_reason='tool_use' if call else 'end_turn', model='claude-sonnet-4',
              content=[NS(type='tool_use', **call)] if call else [NS(type='text', text='offline done')],
              usage=NS(input_tokens=7, output_tokens=2))


class ActualCompactDispatch(Base):
    def test_every_exact_page_reaches_sdk_through_normal_dispatch_and_result_storage(self):
        meta = self._compact([self._msg(1, 'original source')],
            summary=('Ж🐺漢字e\u0301\u2028\u0085"\\' * 2200) + '\n\nхвост\t \n  ')
        source = memory_life.read_compact(meta['id'], ROOM)['text']
        path = memory_life.BASE / meta['path']; before = path.read_bytes()
        manager = RunManager(self.tmp/'runs')
        context = manager.create(RunContext.create(kind='computer', goal='exact compact read',
            principal_id='owner', scope='owner'), 'synthetic input')
        manager.transition(context.run_id, 'running')
        token = run_context.set_run(context.with_status('running'))
        self.addCleanup(run_context.reset_run, token)
        calls, pieces, digest = [], [], hashlib.sha256(source.encode('utf8')).hexdigest()
        offset = 0
        def create(**kwargs):
            nonlocal offset
            calls.append(copy.deepcopy(kwargs))
            if len(calls) > 1:
                blocks = kwargs['messages'][-1]['content']
                result = next(b['content'] for b in blocks if b['type'] == 'tool_result')
                page_text, marker = result.rsplit('\n[ResultRef ', 1)
                self.assertIn('sha256=', marker)
                self.assertNotIn('chars externalized', page_text)
                self.assertLessEqual(len(page_text), 4000)
                header, body = page_text.split('\n\n', 1)
                page = json.loads(header.split('\n', 1)[1])
                self.assertEqual(page['start_char'], offset)
                self.assertEqual(page['sha256'], digest)
                pieces.append(body)
                if page['eof']:
                    self.assertIsNone(page['next_start_char'])
                    return response()
                offset = page['next_start_char']
            return response(call={'id': 'compact-'+str(len(calls)), 'name': 'memory_compact',
                'input': {'action': 'read', 'place': ROOM, 'compact_id': meta['id'],
                          'start_char': offset, 'expected_sha256': digest if offset else ''}})
        with sdk_fixture(self.tmp, create), patch.object(agent, '_RUN_MANAGER', manager):
            answer = agent._terminal_tool_loop(system='synthetic system',
                messages=[{'role': 'user', 'content': 'read the complete compact'}],
                tools=[agent.MEMORY_COMPACT_TOOL], max_iters=100)
        self.assertEqual(answer, 'offline done')
        self.assertGreater(len(pieces), 3)
        self.assertEqual(''.join(pieces), source)
        self.assertEqual(path.read_bytes(), before)
        events = manager.events(context.run_id)
        results = [row for row in events if row.get('kind') == 'tool_result']
        self.assertEqual(len(results), len(pieces))
        self.assertTrue(all(not row['result']['inline']['truncated'] for row in results))
        self.assertTrue(all('start_char' in c['tools'][0]['input_schema']['properties'] for c in calls))


class ActualMediaSDK(Base):
    def test_historical_missing_and_available_photo_cross_real_archive_and_sdk_once(self):
        manager = RunManager(self.tmp/'runs')
        spool = media.MediaSpool(self.tmp/'spool')
        old = spool.ingest_bytes(b'\xff\xd8\xff\xe0historical', kind='photo', filename='old.jpg',
            scope='owner', chat_id='777', message_id=11)
        old.path.unlink()
        fresh = spool.ingest_bytes(b'\xff\xd8\xff\xe0current', kind='photo', filename='current.jpg',
            scope='owner', chat_id='777', message_id=13)
        for mid, text, direction in [(11, 'old photo', 'in'), (12, 'prior answer', 'out'), (13, 'new text', 'in')]:
            memory_life.record_message('777', text, actor='owner' if direction=='in' else 'Praxis',
                direction=direction, source_id=str(mid), ts=mid)
        calls = []
        def create(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            return response()
        with sdk_fixture(self.tmp, create), ExitStack() as stack:
            for name, value in [('_RUN_MANAGER', manager), ('_MEDIA_SPOOL', spool)]:
                stack.enter_context(patch.object(agent, name, value))
            stack.enter_context(patch.object(agent, '_build_prompt_parts', return_value=('persona','contract','evidence')))
            stack.enter_context(patch.object(agent, '_presence_frame', return_value=''))
            stack.enter_context(patch.object(agent, '_presence_evidence', return_value=''))
            stack.enter_context(patch.object(agent.work_loop, 'reply_hand_enabled', return_value=True))
            envelope = agent.voice_turn_envelope('777', 'old photo\nprior answer\nnew text',
                ctx=agent.ChannelContext(chat_id='777', owner=True, origin_message_id=13),
                media_refs=(old, fresh), history=[{'role':'user','content':'old photo'},
                    {'role':'assistant','content':'prior answer'}], current_text='new text')
        self.assertFalse(envelope.failed)
        self.assertEqual(len(calls), 1)
        block_list = calls[0]['messages'][-1]['content']
        text = '\n'.join(b['text'] for b in block_list if b['type']=='text')
        self.assertIn('new text', text)
        self.assertIn('недоступно', text)
        self.assertNotIn('old photo', text)
        image = next(b for b in block_list if b['type']=='image')
        self.assertEqual(image['source']['type'], 'base64')
        import base64
        self.assertEqual(base64.b64decode(image['source']['data']), fresh.path.read_bytes())
        archived = list((manager.path(envelope.run_id)/'artifacts').glob('*inbound-*'))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), fresh.path.read_bytes())
        missing = [e for e in manager.events(envelope.run_id) if e['kind']=='inbound_media_unavailable']
        self.assertEqual(missing[0]['attachments'][0]['sha256'], old.sha256)


if __name__ == '__main__':
    unittest.main()
