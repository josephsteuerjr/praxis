"""Legacy personalization must not rewrite localized frames or literal evidence."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from localharness import owner_words
import agent
import frame_language

LEGACY = min(owner_words.OWNER_ALIASES)
RAW=f'{LEGACY} — Алекс — C:/Users/Alex/Работа/{{literal}} $&'


class LocalizedFrameBoundaries(unittest.TestCase):
    def test_actual_new_core_retains_source_and_current_catalog_after_owner_install(self):
        self.assertEqual(agent.FRAME_TEXT_BOUNDARIES_API,1)
        source=copy.deepcopy(agent.BASE_TOOLS)
        purpose=copy.deepcopy(agent.HAND_PURPOSE)
        mark=agent.frame_trace.mark
        presence=agent._presence_frame
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with self.subTest(ui=ui,frame=language), \
                     patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    ctx=agent.ChannelContext(owner=True)
                    before=agent.catalog_tools_for(ctx)
                    report=owner_words.install(agent,{},name='Mira')
                    self.assertTrue(report['locale_managed']);self.assertFalse(report['active'])
                    self.assertEqual(agent.catalog_tools_for(ctx),before)
                    self.assertIs(agent.frame_trace.mark,mark)
                    self.assertIs(agent._presence_frame,presence)
        self.assertEqual(agent.BASE_TOOLS,source);self.assertEqual(agent.HAND_PURPOSE,purpose)

    def test_completed_owned_frame_and_authored_descriptions_keep_names_and_paths_literal(self):
        def mark(name,zone,kind,text,*args,**kw):return text
        def presence(*args,**kw):return RAW
        schema={'name':'authored','description':RAW,'input_schema':{'type':'object'}}
        fake=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,BASE_TOOLS=[schema],
                             HAND_PURPOSE={'authored':RAW},frame_trace=SimpleNamespace(mark=mark),
                             _presence_frame=presence,_OWNER_TOOLS_CONTRACT=RAW,
                             frame_layout=SimpleNamespace(AUTHORITY={'owner':RAW}))
        report=owner_words.install(fake,{},name='Mira')
        self.assertEqual(report['schemas'],0);self.assertFalse(report['marks'])
        self.assertEqual(fake.frame_trace.mark('contract.owner_tools','stable','text',RAW),RAW)
        self.assertEqual(fake._presence_frame(),RAW);self.assertEqual(schema['description'],RAW)
        self.assertEqual(fake.frame_layout.AUTHORITY['owner'],RAW)
        self.assertEqual(fake._OWNER_TOOLS_CONTRACT,RAW)


if __name__=='__main__':unittest.main()
