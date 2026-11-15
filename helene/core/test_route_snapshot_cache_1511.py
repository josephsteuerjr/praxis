"""Read one route observation per operation, preserving historical and durable bindings."""
import copy
import json
from unittest.mock import Mock,patch
import memory_life as ml
import telegram_routes as routes
from test_life_places import Base,ROOM,A,B

class RouteSnapshotCache(Base):
    def forum(self):
        routes.observe(ROOM,kind='get_forum_topics_ok',since_message_id=1,until_message_id=999999)
        routes.observe_branches(ROOM,{100:None,200:None})

    def move_after_first_read(self):
        original=routes.read;calls=[]
        def read(peer):
            row=original(peer);calls.append(str(peer))
            if len(calls)==1:
                changed=copy.deepcopy(row);changed['branches']={'100':300,'200':300}
                path=routes._path(peer);replacement=path.with_suffix('.swap')
                replacement.write_text(json.dumps(changed),encoding='utf8');replacement.replace(path)
            return row
        return read,calls

    def test_public_place_status_and_branches_use_the_same_real_file_observation(self):
        self.forum();read,calls=self.move_after_first_read()
        with patch.object(routes,'read',side_effect=read):self.assertEqual(routes.place_of(A),ROOM)
        self.assertEqual(calls,[ROOM]);self.assertEqual(routes.place_of(A),ROOM+'__topic__300')
        with patch.object(routes,'read',side_effect=OSError('fixture unreadable')):
            self.assertEqual(routes.place_of(A),A)

    def test_actual_event_selection_reads_route_once_and_next_operation_observes_change(self):
        self.forum()
        first=ml.append_event('conversation_message',chat_id=A,text='literal-a',source='fixture')
        second=ml.append_event('conversation_message',chat_id=B,text='literal-b',source='fixture')
        before={p:p.read_bytes() for p in ml.EVENTS_DIR.glob('*.jsonl')};read,calls=self.move_after_first_read()
        with patch.object(routes,'read',side_effect=read):selected=ml.iter_events(chat_id=ROOM)
        self.assertEqual({r['id'] for r in selected},{first['id'],second['id']});self.assertEqual(calls,[ROOM])
        self.assertEqual(ml.iter_events(chat_id=ROOM),[])
        self.assertEqual({r['id'] for r in ml.iter_events(chat_id=ROOM+'__topic__300')},{first['id'],second['id']})
        self.assertEqual({p:p.read_bytes() for p in ml.EVENTS_DIR.glob('*.jsonl')},before)

    def test_exact_place_guard_and_durable_binding_keep_their_existing_precedence(self):
        self.forum();bound,resolve=ml._place_read_resolver();self.assertEqual(resolve(A),ROOM)
        with ml._state_write_guard(A):
            bound,resolve=ml._place_read_resolver();self.assertEqual(resolve(A),A)
        self.assertEqual(ml.adopt_place(A),ROOM)
        binding=ml.bindings_path().read_bytes();routes.observe_branches(ROOM,{100:300})
        event=ml.append_event('conversation_message',chat_id=A,text='literal',source='fixture')
        with ml._state_write_guard(A):
            self.assertTrue(ml._same_place(A,ROOM))
            self.assertEqual([r['id'] for r in ml.iter_events(chat_id=ROOM)],[event['id']])
        self.assertEqual(ml.bindings_path().read_bytes(),binding)

    def test_historical_epoch_ranges_and_unknown_gaps_are_not_reinterpreted(self):
        record={'epochs':[
            {'epoch':1,'forum_status':routes.FALSE,'since_message_id':10,'until_message_id':20},
            {'epoch':2,'forum_status':routes.TRUE,'since_message_id':30,'until_message_id':40},
        ]}
        for message,wanted in [(None,(routes.TRUE,2)),(5,(routes.UNKNOWN,0)),(10,(routes.FALSE,1)),
                               (20,(routes.FALSE,1)),(25,(routes.UNKNOWN,0)),(30,(routes.TRUE,2)),
                               (40,(routes.TRUE,2)),(41,(routes.TRUE,2))]:
            with self.subTest(message=message),patch.object(routes,'read',return_value=copy.deepcopy(record)) as read:
                self.assertEqual(routes.status_at(ROOM,message),wanted);read.assert_called_once_with(ROOM)
                self.assertEqual(routes._status_from_record(record,message),wanted)
