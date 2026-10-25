"""Observed topic titles survive registry refresh/restart; unknown titles stay explicit."""
import sys, tempfile, unittest, json
from pathlib import Path
from unittest import mock
DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK/'localharness'),str(DESK)]
import botapi, transport
from deskd import readers

class TopicTitles(unittest.TestCase):
    def test_title_survives_archive_update_and_new_rooms_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);key='-123__topic__19'
            archive=root/'memory/groups'/f'{key}.jsonl'
            archive.parent.mkdir(parents=True);archive.write_text('{}\n')
            rooms=botapi.Rooms(root,None,'TestAgent')
            rooms.describe(key,title='Group · Observed topic',is_dm=False)
            rooms._registry(key,archive)
            self.assertEqual(botapi.Rooms(root,None,'TestAgent').meta(key)['title'],'Group · Observed topic')
            archive.write_text('{}\n{}\n')
            transport._registry(root,key,archive)
            saved=json.loads((root/'memory/.state/group_context'/f'{key}.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['message_count'],2)
            self.assertEqual(saved['title'],'Group · Observed topic')
            with mock.patch.object(readers,'tree',lambda:root):
                self.assertEqual(readers._title_for(key,{key:'Group'}),'Group · Observed topic')

    def test_unobserved_title_keeps_topic_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(readers,'tree',lambda:Path(directory)),mock.patch.object(readers,'_room_titles',lambda:{}):
                self.assertEqual(readers._title_for('-123__topic__19',{'-123':'Group','-123__topic__19':'Group'}),'Group · тема 19')

    def test_exact_topic_archive_and_legacy_parent_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);groups=root/'memory/groups';groups.mkdir(parents=True)
            (groups/'topic.jsonl').write_text(json.dumps({'text':'exact topic without redundant topic_id'})+'\n')
            (groups/'parent.jsonl').write_text(json.dumps({'text':'other topic','topic_id':20})+'\n'+json.dumps({'text':'legacy topic','topic_id':19})+'\n')
            catalog=[{'peer_id':'-123__topic__19','archive':'memory/groups/topic.jsonl'},
                     {'peer_id':'-123','archive':'memory/groups/parent.jsonl'}]
            with mock.patch.object(readers,'tree',lambda:root),mock.patch.object(readers,'chats',lambda:catalog):
                self.assertEqual(readers.chat_tail('-123__topic__19')[0]['text'],'exact topic without redundant topic_id')
                catalog.pop(0)
                self.assertEqual([r['text'] for r in readers.chat_tail('-123__topic__19')],['legacy topic'])

if __name__=='__main__': unittest.main()
