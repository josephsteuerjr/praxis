"""Edition ChannelContext and shared frame adapters agree on audience/root peer."""
import unittest
from agent import ChannelContext

class Audience(unittest.TestCase):
    def test_private_owner_dm_has_one_owner_audience_and_root_peer(self):
        ctx=ChannelContext(chat_id='101',room_id='101',principal_id='101',is_dm=True,owner=True)
        self.assertEqual(ctx.scope,'owner');self.assertTrue(ctx.owner_audience);self.assertEqual(ctx.room_chat_id,'101')
    def test_owner_in_a_group_does_not_grant_an_owner_audience(self):
        ctx=ChannelContext(chat_id='-202__topic__7',room_id='-202',principal_id='101',is_dm=False,owner=True)
        self.assertEqual(ctx.scope,'group');self.assertFalse(ctx.owner_audience)
        self.assertEqual(ctx.room_chat_id,'-202');self.assertEqual(ctx.chat_id,'-202__topic__7')
    def test_another_persons_dm_stays_outside_owner_scope(self):
        ctx=ChannelContext(chat_id='303',is_dm=True,owner=False)
        self.assertFalse(ctx.owner_audience);self.assertEqual(ctx.room_chat_id,'303')

if __name__=='__main__':unittest.main()
