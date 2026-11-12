import json
import sys
import types
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import owner_context


class Companion(unittest.TestCase):
    def setUp(self):
        self.records = {'window': [{'actor': 'Owner', 'direction': 'in', 'line': 'main question', 'ts': 10, 'source_id':'1'},
                                   {'actor': 'Agent', 'direction': 'out', 'line': 'main answer', 'ts': 11}],
                        '42': [{'actor':'Owner', 'direction':'in', 'line':'telegram question', 'ts':12},
                               {'actor':'Agent', 'direction':'out', 'line':'telegram answer', 'ts':13}],
                        '-100': [{'line':'private group'}], 'window-abcdef12': [{'line':'other room'}]}
        self.life = types.SimpleNamespace(hot_records=lambda place, limit:self.records.get(place, [])[-limit:],
                                         helene_owner_context=owner_context.policy({'telegram':{'owner_id':42}}, None))

    def test_both_directions_preserve_source_and_authorship(self):
        original = json.dumps(self.records)
        for target, source, own in [('window', 'telegram', 'main'), ('42', 'main', 'telegram')]:
            result = owner_context.companion(self.life, target)
            self.assertIn(source+' question', result)
            self.assertIn(source+' answer', result)
            self.assertNotIn(own+' question', result)
            rows = [json.loads(line) for line in result.splitlines()[1:]]
            self.assertEqual([row['direction'] for row in rows], ['in','out'])
            self.assertEqual([row['actor'] for row in rows], ['Owner','Agent'])
        self.assertEqual(json.dumps(self.records), original)

    def test_never_shared_to_groups_other_rooms_or_previous_owner(self):
        for place in ('-100','window-abcdef12','43',''):
            self.assertEqual(owner_context.companion(self.life,place), '')
        self.life.helene_owner_context=owner_context.policy({'telegram':{'owner_id':43}},None)
        self.assertEqual(owner_context.companion(self.life,'42'), '')
        self.assertEqual(owner_context.companion(self.life,'window'), '')

    def test_legacy_duplicates_and_bounded_oversized_record(self):
        self.records['pult']=[self.records['window'][0]]
        self.assertEqual(owner_context.companion(self.life,'42').count('main question'),1)
        self.life.helene_owner_context['tape_chars']=1000
        self.records['42']=[{'line':'x'*20000,'ts':1,'direction':'in'}]
        result=owner_context.companion(self.life,'window')
        self.assertLess(len(result),1100)
        self.assertIn('"partial":true',result)


if __name__=='__main__':unittest.main()
