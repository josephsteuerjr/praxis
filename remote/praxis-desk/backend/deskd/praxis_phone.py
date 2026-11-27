"""Praxis phone edition. Read existing canonical Forge records, without importing the agent."""
import asyncio
from . import readers,phone_activity,forge_reader,forge_control,forge_snapshot
# Fill the Forge reader's memory before the first phone page asks for it.
forge_snapshot.warm_once()

def routes(Route,Fail):
    async def tasks(call):
        try:
            limit=max(1,min(int(call.query.get('limit') or 20),100))
            return await asyncio.to_thread(phone_activity.activity,readers.tree(),limit,call.query.get('cursor',''))
        except (ValueError,forge_reader.ReadError) as error:
            return Fail(getattr(error,'status',400),str(error))
    async def events(call):
        try:
            offset=max(0,int(call.query.get('offset') or 0));limit=max(1,min(int(call.query.get('limit') or 50),100));seq=max(0,int(call.query.get('after_seq') or 0))
            return await asyncio.to_thread(phone_activity.events,readers.tree(),call.query.get('task_id',''),call.query.get('unit_id',''),offset,limit,call.query.get('generation',''),seq)
        except (ValueError,forge_reader.ReadError) as error:
            return Fail(getattr(error,'status',400),str(error))
    async def payload(call):
        try:
            return await asyncio.to_thread(forge_reader.payload,readers.tree(),call.query.get('task_id',''),call.query.get('unit_id',''),call.query.get('sha256',''),call.query.get('kind','payload'),int(call.query.get('offset') or 0),int(call.query.get('limit') or 8000))
        except (ValueError,forge_reader.ReadError) as error:
            return Fail(getattr(error,'status',400),str(error))
    async def activity(call):
        # Do not pretend local Hélène updater/reviewer jobs exist on the server.
        # The real participant roster is read by the Forge panel itself.
        return {'update':None,'reviewers':[],'recent_reviewers':[],'recent_reviewers_cursor':None}
    async def control(call,write=False):
        try:
            if not write:
                if call.query.get('command_id'):
                    return await asyncio.to_thread(forge_control.receipt,readers.tree(),call.query['command_id'])
                return await asyncio.to_thread(forge_control.recent,readers.tree(),call.query.get('task_id',''))
            body=call.body or {}
            if not isinstance(body,dict) or body.get('action') not in {'stop','prepare_recovery','recover'}:
                return Fail(400,'Нужно действие stop, prepare_recovery или recover.')
            result=await asyncio.to_thread(forge_control.enqueue,readers.tree(),body.get('task_id',''),body['action'],
                body.get('agent_id',''),body.get('expected_generation',''),body.get('command_id',''),
                host_binding=body.get('host_binding',''),reason=body.get('reason',''))
            return result
        except (ValueError,forge_reader.ReadError) as error:
            return Fail(getattr(error,'status',400),str(error))
    async def control_post(call):
        return await control(call,write=True)
    return (
        Route('GET','/api/phone/forge/control',control,private=True),
        Route('POST','/api/phone/forge/control',control_post,private=True),
        Route('GET','/api/phone/forge/tasks',tasks,private=True),
        Route('GET','/api/phone/forge/events',events,private=True),
        Route('GET','/api/phone/forge/payload',payload,private=True),
        Route('GET','/api/phone/activity',activity,private=True),
    )

def summary():
    page=phone_activity.activity(readers.tree(),30)
    return page['active']+page['recent']
