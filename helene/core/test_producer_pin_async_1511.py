"""Language remains coherent across awaits, concurrent calls and cancellation."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import frame_language as fl


class AsyncProducerPin(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.path)))
        self.choose('en')

    def choose(self,language):
        self.path.write_text(json.dumps({'agent':{'frame_language':language}}),encoding='utf-8')

    def test_setting_change_during_await_belongs_to_the_next_operation(self):
        @fl.pinned
        async def produce(change=False):
            first=fl.language()
            if change:self.choose('zh')
            await asyncio.sleep(0)
            return first,fl.language()
        async def run():
            self.assertEqual(await produce(True),('en','en'))
            self.assertEqual(await produce(),('zh','zh'))
        asyncio.run(run())

    def test_two_concurrent_contexts_keep_their_own_language_and_data(self):
        raw={'payload':'Literal Русский 中文'}
        @fl.pinned
        async def produce():
            first=fl.language()
            await asyncio.sleep(0)
            return first,fl.language(),raw
        async def call(language):
            with fl.use(language):return await produce()
        async def run():
            result=await asyncio.gather(call('fr'),call('zh'))
            self.assertEqual(result[0][:2],('fr','fr'))
            self.assertEqual(result[1][:2],('zh','zh'))
            self.assertIs(result[0][2],raw);self.assertIs(result[1][2],raw)
        asyncio.run(run())

    def test_cancelled_operation_releases_its_pin(self):
        @fl.pinned
        async def produce():
            self.choose('fr')
            await asyncio.sleep(0)
            raise asyncio.CancelledError()
        async def run():
            with self.assertRaises(asyncio.CancelledError):await produce()
            self.assertEqual(fl.language(),'fr')
        asyncio.run(run())
