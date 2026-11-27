import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

import focus_interrupt as focus

with patch.dict(os.environ):
    import mtproto_runner as runner


def message(mid=50, sender=11, text="!", **overrides):
    fields = dict(message=text, sender_id=sender, id=mid, out=False, date=datetime.now(timezone.utc),
                  fwd_from=None, via_bot_id=None)
    return SimpleNamespace(**dict(fields, **overrides))


class FocusYield(unittest.IsolatedAsyncioTestCase):
    async def test_only_armed_peer_can_ring_and_rpc_exposes_no_message(self):
        from owner_telegram_service import OwnerTelegram
        with TemporaryDirectory() as tmp, patch.object(focus,"peer_file",return_value=Path(tmp)/"peer"):
            focus.publish_peer(23)
            s=OwnerTelegram(None,11,Path(tmp),Path(tmp))
            s.wakes=Mock()
            s.wakes.was_sent.return_value=False
            s.state="ready"
            with self.assertRaises(ValueError):
                s.focus_watch(24)
            token=s.focus_watch(23)["token"]
            async def arrive(msg,peer=23):
                await s.focus_arrival(SimpleNamespace(chat_id=peer,message=msg,is_private=True,
                    out=True,id=msg.id,date=msg.date))
            for msg in [message(text="ordinary"),message(fwd_from=True),message(via_bot_id=42),
                        message(sender=42),message(date=datetime.now(timezone.utc)-timedelta(seconds=4))]:
                await arrive(msg)
            await arrive(message(),24)
            self.assertIsNone(s.focus_watch(23,token)["signal"])
            s.wakes.was_sent.return_value=True
            await arrive(message())
            self.assertIsNone(s.focus_watch(23,token)["signal"])
            s.wakes.was_sent.return_value=False
            msg=message(text="! local callback")
            await arrive(msg)
            self.assertEqual(s.focus_watch(23,token),{"signal":focus.fingerprint(msg)})
            self.assertEqual(s.focus_watch(23,token,True),{})
            self.assertEqual(s.focus_watch(23,token),{"expired":True})

    async def test_normal_recipient_acquires_lock_while_group_waits_then_parent_recovers_it(self):
        r = runner
        lock=r._OneMind()
        await lock.acquire()
        trail=[]
        async def pass_(label):
            async with lock:
                trail.append(label)
                await asyncio.sleep(0)
        group=asyncio.create_task(pass_("group"))
        await asyncio.sleep(0)
        dm=asyncio.create_task(pass_("dm"))
        await asyncio.sleep(0)
        await asyncio.wait_for(lock.yield_to(dm),2)
        self.assertTrue(lock.locked())
        self.assertEqual(trail,["dm"])
        lock.release()
        await group
        self.assertEqual(trail,["dm","group"])

    async def test_superseded_recipient_returns_lock_and_replacement_can_take_it(self):
        r = runner
        lock=r._OneMind()
        await lock.acquire()
        cancelled=asyncio.create_task(asyncio.sleep(0))
        cancelled.cancel()
        await lock.yield_to(cancelled)
        self.assertTrue(lock.locked())
        async def replacement():
            async with lock:
                return True
        task=asyncio.create_task(replacement())
        await lock.yield_to(task)
        self.assertTrue(task.result())
        self.assertTrue(lock.locked())
        lock.release()

    async def test_handover_waits_for_native_source_and_the_normal_pass(self):
        r = runner
        lock=r._OneMind()
        await lock.acquire()
        loop=asyncio.get_running_loop()
        bell=r._FocusYield(loop,"work")
        msg=message()
        bell.signal=focus.fingerprint(msg)
        tasks=[]
        async def history(peer):
            yield msg
        async def normal_pass():
            # Native inbox admission already checked sender and bound the real source.
            meta=dict(is_dm=True,is_owner=True,sender_id=11,origin_message_id=msg.id+1)
            self.assertFalse(bell.addressed("11",meta))
            bell.native_mid=msg.id
            self.assertFalse(bell.addressed("-10",meta))
            self.assertFalse(bell.addressed("11",dict(meta,sender_id=12)))
            self.assertTrue(bell.addressed("11",meta))
            bell.offered.put_nowait(asyncio.current_task())
            async with lock:
                self.assertIs(r._FOCUS_YIELD,bell)
                self.assertFalse(focus.active())
                bell.finished_pass(dict(meta,origin_text="my question"),"ordinary-run")
        async def caught_up():
            tasks.append(asyncio.create_task(normal_pass()))
        client=SimpleNamespace(connect=AsyncMock(),catch_up=caught_up,
                               iter_messages=history,disconnect=AsyncMock())
        with patch.object(r,"client",client),patch.object(r,"_ONE_MIND",lock),patch.object(r,"OWNER_ID",11):
            result=await asyncio.wait_for(bell.handover(),2)
        self.assertEqual(result,"ordinary-run")
        self.assertTrue(lock.locked())
        self.assertIsNone(r._FOCUS_YIELD)
        client.disconnect.assert_awaited_once()
        await asyncio.gather(*tasks)
        lock.release()

    async def test_bare_bell_waits_for_a_real_owner_question(self):
        r = runner
        lock=r._OneMind()
        await lock.acquire()
        bell=r._FocusYield(asyncio.get_running_loop(),"work")
        msg=message()
        bell.signal=focus.fingerprint(msg)
        acknowledged=asyncio.Event()
        async def history(peer):
            yield msg
        async def native(text,mid,rid):
            bell.native_mid=msg.id
            meta=dict(is_dm=True,is_owner=True,sender_id=11,origin_message_id=mid,origin_text=text)
            self.assertTrue(bell.addressed("11",meta))
            bell.offered.put_nowait(asyncio.current_task())
            async with lock:
                bell.finished_pass(meta,rid)
            if text=="!":
                acknowledged.set()
        tasks=[]
        async def caught_up():
            tasks.append(asyncio.create_task(native("!",50,"initial")))
        client=SimpleNamespace(connect=AsyncMock(),catch_up=caught_up,iter_messages=history,disconnect=AsyncMock())
        with patch.object(r,"client",client),patch.object(r,"_ONE_MIND",lock),patch.object(r,"OWNER_ID",11):
            exchange=asyncio.create_task(bell.handover())
            await asyncio.wait_for(acknowledged.wait(),2)
            self.assertFalse(exchange.done())
            client.disconnect.assert_not_awaited()
            tasks.append(asyncio.create_task(native("I need you",51,"followup")))
            self.assertEqual(await asyncio.wait_for(exchange,2),"followup")
        self.assertTrue(lock.locked())
        await asyncio.gather(*tasks)
        lock.release()

    async def test_deleted_bell_returns_without_any_chat_dispatch_or_release(self):
        r = runner
        lock=r._OneMind()
        await lock.acquire()
        async def empty(peer):
            if False:
                yield None
        bell=r._FocusYield(asyncio.get_running_loop(),"work")
        bell.signal=focus.fingerprint(message())
        client=SimpleNamespace(connect=AsyncMock(),catch_up=AsyncMock(),iter_messages=empty,disconnect=AsyncMock())
        with patch.object(r,"client",client),patch.object(r,"_ONE_MIND",lock),patch.object(r,"OWNER_ID",11):
            self.assertEqual(await bell.handover(),"")
        self.assertTrue(lock.locked())
        lock.release()
