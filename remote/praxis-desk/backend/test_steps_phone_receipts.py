import unittest
from unittest.mock import patch
from deskd import phone_activity

class PhoneReceipts(unittest.TestCase):
    def test_observed_execution_and_outcome_are_retained_with_bounds(self):
        event={'seq':1,'kind':'tool_execution','receipt':{'kind':'process','handler':'engine.run','argv':['python','-c','print(1)'],'cwd':'/project','exit_code':1,'secret_extra':'not projected'},'metadata':{'outcome':'refused','target':'a.py'},'private_extra':'not projected'}
        with patch.object(phone_activity.forge_reader,'events',return_value={'events':[event],'has_more':False}):
            got=phone_activity.events(None,'task','unit')['events'][0]
        self.assertEqual(got['receipt']['argv'],event['receipt']['argv'])
        self.assertEqual(got['receipt']['exit_code'],1)
        self.assertEqual(got['outcome'],'refused')
        self.assertEqual(got['target'],'a.py')
        self.assertNotIn('private_extra',got)
        self.assertNotIn('secret_extra',got['receipt'])

    def test_receipt_does_not_invent_success_or_crash_on_malformed_fields(self):
        events=[{'seq':1,'kind':'tool_result','receipt':['bad'],'metadata':{'outcome':['bad']}}, {'seq':2,'kind':'tool_result','receipt':{'argv':['x'*9000]*130},'metadata':None}]
        with patch.object(phone_activity.forge_reader,'events',return_value={'events':events}):
            rows=phone_activity.events(None,'task','unit')['events']
        self.assertEqual(rows[0]['outcome'],'unknown')
        self.assertNotIn('outcome',rows[1])
        self.assertEqual(len(rows[1]['receipt']['argv']),100)
        self.assertEqual(len(rows[1]['receipt']['argv'][0]),2000)

if __name__=='__main__':unittest.main()
