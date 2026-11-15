"""Bounded derived accelerators cannot replace canonical event evidence."""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch
import memory_life as ml
from test_life_places import Base,ROOM

RAW='Literal Русский 中文 {value} \n '

class LifeCachePort(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(ml._EVENTS_CACHE,clear=True));self.enterContext(patch.dict(ml._EVENT_LOOKUPS,clear=True))
        self.enterContext(patch.dict(os.environ,PRAXIS_EVENTS_CACHE='on'))

    def event(self,key,ts,text=RAW):
        return ml.append_event('conversation_message',chat_id=ROOM,actor='Literal',direction='out',text=text,source='fixture',source_id=key,dedupe_key=key,ts=ts,meta={'literal':RAW})

    def test_witnessed_append_advances_warm_indexes_without_reparsing_history(self):
        first=self.event('key-a',1710000000);second=self.event('key-b',1710086400)
        self.assertEqual([r['id'] for r in ml.iter_events()],[first['id'],second['id']])
        self.assertEqual(ml._lookup_events(dedupe_key='key-a')[0]['id'],first['id'])
        third=self.event('key-c',1710086401)
        with patch.object(ml,'_event_file_records',wraps=ml._event_file_records) as parse:
            got=ml._lookup_events(dedupe_key='key-c');parse.assert_not_called()
        self.assertEqual(got,[third]);self.assertEqual(got[0]['text'],RAW)
        got[0]['text']='altered';got[0]['meta']['literal']='altered'
        self.assertEqual(ml._lookup_events(dedupe_key='key-c'),[third])
        canonical=[json.loads(line) for path in sorted(ml.EVENTS_DIR.glob('*.jsonl')) for line in path.read_text(encoding='utf8').split('\n') if line]
        self.assertEqual(canonical,[first,second,third])

    def test_same_size_rewrite_with_restored_mtime_and_later_truncation_invalidate_the_index(self):
        first=self.event('key-a',1710000000,text='AAAA');self.assertEqual(ml._lookup_events(dedupe_key='key-a'),[first])
        path=next(ml.EVENTS_DIR.glob('*.jsonl'));prior=path.stat();raw=path.read_bytes();changed=raw.replace(b'AAAA',b'BBBB');self.assertEqual(len(raw),len(changed))
        if os.name=='nt':
            # Python 3.14 st_ctime is creation time on Windows. Canonical rewrites
            # use a new file identity; do not pretend an in-place mtime forgery is visible.
            replacement=path.with_suffix('.replacement');replacement.write_bytes(changed)
            os.utime(replacement,ns=(prior.st_atime_ns,prior.st_mtime_ns));os.replace(replacement,path)
        else:
            # The tmpfs stand may stamp several writes within one filesystem clock tick.
            time.sleep(.03)
            path.write_bytes(changed);os.utime(path,ns=(prior.st_atime_ns,prior.st_mtime_ns))
        current=path.stat();self.assertEqual(path.read_bytes(),changed)
        old=(prior.st_dev,prior.st_ino,prior.st_size,prior.st_mtime_ns,prior.st_ctime_ns)
        new=(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns,current.st_ctime_ns)
        self.assertNotEqual(old,new,{'old':old,'new':new,'reason':'A stat-derived cache cannot observe an identical metadata stamp'})
        self.assertEqual(ml._lookup_events(dedupe_key='key-a')[0]['text'],'BBBB',{'old':old,'new':new})
        path.write_bytes(b'');self.assertEqual(ml._lookup_events(dedupe_key='key-a'),[])
        self.assertEqual(ml.iter_events(),[])

    def test_cache_off_bypasses_a_previously_warm_lookup_and_keeps_exact_results(self):
        first=self.event('key-a',1710000000);self.assertEqual(ml._lookup_events(dedupe_key='key-a'),[first]);self.assertTrue(ml._EVENT_LOOKUPS)
        with patch.dict(os.environ,PRAXIS_EVENTS_CACHE='off'),patch.object(ml,'_event_file_records',wraps=ml._event_file_records) as parse:
            self.assertEqual(ml._lookup_events(dedupe_key='key-a'),[first]);self.assertGreater(parse.call_count,0)
            self.assertFalse(ml._EVENT_LOOKUPS)
        self.assertEqual(ml._lookup_events(dedupe_key='key-a'),[first])

    def test_unreadable_duplicate_lookup_does_not_authorize_a_new_canonical_append(self):
        first=self._say(ROOM,'original');paths={p:p.read_bytes() for p in ml.EVENTS_DIR.glob('*.jsonl')}
        with patch.object(ml,'_lookup_events',side_effect=OSError('fixture read failure')):
            with self.assertRaises(OSError):ml.record_message(ROOM,'new',actor='Literal',dedupe_key='must-read-canonical')
        self.assertEqual({p:p.read_bytes() for p in ml.EVENTS_DIR.glob('*.jsonl')},paths)
