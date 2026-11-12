"""The agent's own hour: reuse Praxis social_pulse state, context and intent."""
from __future__ import annotations

import copy
import json
from pathlib import Path


class Reflection:
    def __init__(self, tree):
        self.path = Path(tree)/'memory/.state/reflection.json'

    def state(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
                raise ValueError('Invalid reflection state')
            return {'enabled': value['enabled']}
        except FileNotFoundError:
            return {'enabled': False}
        except (OSError, ValueError):
            return {'enabled': False, 'error': 'Reflection state is unreadable. Inspect it; an explicit on/off replaces the switch.'}

    def set(self, enabled):
        from localharness.package_notice import write
        write(self.path, {'enabled': enabled})
        return self.state()

    def install(self, agent):
        if not hasattr(agent, '_helene_reflection_original'):
            agent._helene_reflection_original = (agent.TOOL_IMPL['remind_self'], agent.TOOL_IMPL['my_agenda'])
        original, agenda = agent._helene_reflection_original

        def remind_self(kind='', goal='', when='', target='', after_run='', *, alarms=None, reflection=None):
            single = any((kind, goal, when, target, after_run))
            if sum((single, alarms is not None, reflection is not None)) != 1:
                return json.dumps({'ok': False, 'error': 'Choose one: reflection, alarms, or a single kind/goal.'})
            if reflection is not None:
                if reflection not in ('on', 'off', 'status'):
                    return json.dumps({'ok': False, 'error': 'reflection: on, off or status'})
                result = self.state() if reflection == 'status' else self.set(reflection == 'on')
                return json.dumps({'ok': 'error' not in result, 'reflection': result,
                    'note': 'Your own hour, once per hour while the engine is running. No message or report is required. Stop/Pause still applies.'})
            if alarms is None:
                return original(kind=kind, goal=goal, when=when, target=target, after_run=after_run)
            import tasks
            if (not isinstance(alarms, list) or not 1 <= len(alarms) <= 64
                    or any(not isinstance(row, dict) or set(row)-{'kind','goal','when','target','after_run'}
                           or any(not isinstance(v, str) for v in row.values())
                           or row.get('kind') not in tasks.KINDS or not row.get('goal','').strip() for row in alarms)):
                return json.dumps({'ok': False, 'error': 'alarms: 1..64 objects with kind and goal; optional when, target, after_run. No alarms created.'})
            if not callable(getattr(tasks, 'capture_additions', None)):
                return json.dumps({'ok': False, 'error': 'This preserved core supports single alarms only. No alarms created; use the single form or update the core.'})
            results = []
            for index, row in enumerate(alarms):
                uncertain = False
                with tasks.capture_additions() as created:
                    try:
                        note = original(**row)
                    except Exception as exc:
                        note = 'Alarm creation raised ' + type(exc).__name__
                        uncertain = True
                outcome = 'created' if created else 'unknown' if uncertain else 'refused'
                results.append({'index': index, 'ok': bool(created), 'outcome': outcome, 'ids': created, 'note': note})
            return json.dumps({'ok': all(row['ok'] for row in results), 'results': results,
                'note': 'Keep created IDs; retry only refused items. For unknown outcomes inspect my_agenda before retrying.'}, ensure_ascii=False)

        agent.TOOL_IMPL['remind_self'] = remind_self
        agent.TOOL_IMPL['my_agenda'] = lambda: agenda() + '\nReflection: ' + json.dumps(self.state())
        if isinstance(getattr(agent, 'HAND_PURPOSE', None), dict):
            agent.HAND_PURPOSE.update(remind_self='мой час рефлексии on/off или план конкретных будильников одним пакетом',
                                      my_agenda='мои намеченные возвращения и состояние рефлексии')
        from edition_tools import TOOL_LISTS
        seen = set()
        for attr in (*TOOL_LISTS, 'REMIND_SELF_TOOL'):
            value = getattr(agent, attr, [])
            for tool in ([value] if isinstance(value, dict) else value):
                if not isinstance(tool, dict) or id(tool) in seen:
                    continue
                if tool.get('name') == 'my_agenda':
                    tool['description'] = 'My planned returns and the durable reflection on/off state; check actual execution separately.'
                    english = getattr(agent, 'tool_text_en', None)
                    if english is not None:
                        english.EN['my_agenda'] = {'d': tool['description']}
                        english.POINTER_PURPOSE['my_agenda'] = 'my scheduled returns and reflection on/off state'
                if tool.get('name') != 'remind_self':
                    continue
                seen.add(id(tool))
                schema = tool['input_schema']
                item = copy.deepcopy(schema)
                item['properties'] = {name: value for name, value in item['properties'].items()
                                      if name not in ('alarms', 'reflection')}
                item['required'] = ['kind', 'goal']
                schema['required'] = []
                schema['properties']['alarms'] = {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': item}
                schema['properties']['reflection'] = {'type': 'string', 'enum': ['on','off','status']}
                tool['description'] = (
                    'Plan your own time. Choose ONE form: reflection="on"/"off"/"status" for your own hourly '
                    'reflection (no external cause or owner permission needed); alarms=[{kind,goal,when,...},...] '
                    'to plan several concrete returns in one call; or kind/goal/when for one alarm. '
                    'Reflection is your own hour to think, explore, work, review open intentions or stay silent; '
                    'no message or report is required. Use alarms for specific future steps, not a chain of '
                    'alarms to simulate reflection. Batch receipts identify each created alarm; retry refused items only; '
                    'for unknown outcomes inspect my_agenda before retrying. '
                    'kind=wake for a normal turn with communication available; window for focus; message/email '
                    'for a fresh decision at the deadline (never automatic sending); note for a reminder. '
                    'when: ISO datetime, in 30m, tomorrow 10:00, daily 09:00, every 4h. '
                    'target for a recipient; after_run waits for a durable run to finish. my_agenda lists plans; '
                    'unschedule removes an alarm by ID. Stop/Pause suspends autonomous wakes.')
                english = getattr(agent, 'tool_text_en', None)
                if english is not None:
                    english.EN['remind_self'] = {**english.EN.get('remind_self', {}), 'd': tool['description']}
                    english.POINTER_PURPOSE['remind_self'] = 'my own hourly reflection on/off or a batch of concrete alarms'

    def tick(self, invoke):
        if not self.state()['enabled']:
            return
        import appetite
        import heartbeat
        import social_pulse
        if appetite.background_hold():
            return
        sleep = heartbeat.parse_hours(__import__('os').getenv('PRAXIS_SLEEP_WINDOW', '4-6'))
        if heartbeat.hour_in(heartbeat.local_now().hour, sleep):
            return
        pulse_id = social_pulse.begin()
        if pulse_id is None:
            return
        try:
            import telegram_followups
            import mailroom
            context = heartbeat.window_context(include_pacing=False)
            context = '\n\n'.join(x for x in (social_pulse.observability(pulse_id), context,
                                              telegram_followups.LEDGER.context()) if x)
            seen = social_pulse.mailbox_seen()
            mailbox_hashes = [str(entry['hash']) for entry in mailroom.list_open()
                              if entry.get('hash') and entry['hash'] not in seen][:12]
            mailbox_index = mailroom.index_block(12, exclude_hashes=seen)
            with social_pulse.active(pulse_id):
                result = invoke(pulse_id, social_pulse.goal(context), mailbox_index=mailbox_index)
            if result == 'deferred':
                social_pulse.defer(pulse_id, detail='another authored turn is active')
            else:
                social_pulse.finish(pulse_id, ok=result in ('silent', 'done'), detail='own hour returned: ' + str(result),
                                    mailbox_hashes=mailbox_hashes)
        except Exception:
            social_pulse.finish(pulse_id, ok=False, detail='own hour failed; see runner log')
            raise
