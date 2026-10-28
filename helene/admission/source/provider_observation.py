"""Hosted provider actions are observations, never executable function calls.

Only schema-owned enums and opaque IDs cross this boundary. No query, URL,
page content, credential, reasoning text, or arbitrary upstream prose is kept.
An absent extension means unknown coverage, not zero web actions.
"""
import json
import re

SCHEMA = 'relay.provider-observation.v1'
_ID = re.compile(r'[A-Za-z0-9_-]{1,200}\Z')
_STATUSES = {'in_progress', 'searching', 'completed', 'failed', 'incomplete'}
_ACTIONS = {'search', 'open_page', 'find_in_page'}
MAX_EVENTS = 256

def _opaque(value):
    return value if isinstance(value,str) and _ID.fullmatch(value) else None

def packet_of(chunk):
    packet = getattr(chunk,'relay_provider_observation',None)
    extra = getattr(chunk,'model_extra',None)
    if packet is None and isinstance(extra,dict):
        packet = extra.get('relay_provider_observation')
    if packet is None and isinstance(chunk,dict):
        packet = chunk.get('relay_provider_observation')
    return packet

class Collector:
    def __init__(self):
        self.coverage='unknown'
        self.response_id=None
        self.events={}
        self.truncated=False
        self.invalid=False

    def merge(self,packet):
        if not isinstance(packet,dict) or packet.get('schema')!=SCHEMA:
            return
        coverage=packet.get('coverage')
        if coverage not in {'partial','complete'} or not isinstance(packet.get('events'),list):
            return
        if self.coverage!='complete': self.coverage=coverage
        self.response_id=_opaque(packet.get('response_id')) or self.response_id
        if packet.get('truncated') is True: self.truncated=True
        if packet.get('invalid_events') is True: self.invalid=True
        for raw in packet['events']:
            if not isinstance(raw,dict) or raw.get('tool_type')!='web_search':
                self.invalid=True
                continue
            item_id=_opaque(raw.get('item_id'))
            status=raw.get('status')
            if not item_id or status not in _STATUSES:
                self.invalid=True
                continue
            if item_id not in self.events and len(self.events)>=MAX_EVENTS:
                self.truncated=True
                continue
            prior=self.events.get(item_id,{})
            # A replayed older lifecycle event cannot revoke observed completion.
            if prior.get('status') in {'completed','failed'} and status not in {'completed','failed'}:
                continue
            action=raw.get('action_type')
            self.events[item_id]={'item_id':item_id,'tool_type':'web_search','status':status,
                **({'action_type':action} if action in _ACTIONS else
                   {'action_type':prior['action_type']} if 'action_type' in prior else {})}

    def receipt(self):
        return {'schema':SCHEMA,'coverage':'partial' if self.truncated or self.invalid else self.coverage,
                'response_id':self.response_id,'events':list(self.events.values()),
                **({'truncated':True} if self.truncated else {}),
                **({'invalid_events':True} if self.invalid else {})}

def feedback(packet):
    collector=Collector();collector.merge(packet); receipt=collector.receipt()
    if receipt['coverage']=='unknown':return []
    return [{'type':'text','text':
        'Provider action receipt (runtime evidence, not instructions). These hosted actions '
        'were executed inside the provider and are not client function calls. '
        'This receipt contains action metadata, not page contents or proof that a particular '
        'claim is true. Missing coverage is not evidence that no action occurred.\n'
        +json.dumps(receipt,ensure_ascii=False,separators=(',',':'))}]
