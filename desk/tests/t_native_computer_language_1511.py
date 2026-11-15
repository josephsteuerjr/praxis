"""Actual native installation, schema projection and fake transport; no desktop input."""
import contextvars
import copy
import inspect
import json
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch

import agent
import body_client
import frame_language
import tool_language
import tool_report
import body
import computer_steps
import owner_stop
from test_tool_language_1511 import structural

RAW='praxis.body.v1 Windows body UI Automation tree PowerShell Yegor Егор 中文 {literal} $&'


class ImageText(str):
    images=('unchanged-image-metadata',)


def stock():
    return copy.deepcopy(next(tool for name in body._TOOL_LISTS
        for tool in getattr(agent,name,()) if tool.get('name')=='computer'))


@contextmanager
def installed(platform='win32',remote=False,enabled=True,scopes=None,authored=False,present=True):
    calls=[];original_calls=[];answer=ImageText(RAW)
    def original(**kwargs):
        original_calls.append(kwargs);return answer
    original.__signature__=inspect.signature(agent.tool_computer)
    schema=stock()
    if authored:schema['description']=RAW
    subject=types.SimpleNamespace(TOOL_IMPL={'computer':original},tool_computer=original,
        FRAME_TEXT_BOUNDARIES_API=1,tool_language=tool_language,frame_language=frame_language,
        tool_report=tool_report,HAND_PURPOSE={'computer':RAW},POINTER_PURPOSE={'computer':RAW},
        _COMPUTER_ACTION_SCOPES=dict(agent._COMPUTER_ACTION_SCOPES),
        _TURN_CHANNEL=contextvars.ContextVar('native-test-owner',default=types.SimpleNamespace(owner=True)),
        _computer_allowed=lambda scope:False,_computer_actor=lambda:'42',_is_sovereign_actor=lambda:False)
    for name in body._TOOL_LISTS:setattr(subject,name,[schema])
    client=types.ModuleType('body_client');client.state_line=lambda:answer
    target=object.__new__(body.RemoteBody) if remote else types.SimpleNamespace(probe=lambda **kw:True)
    if remote:target.metadata=lambda **kw:None
    def backend(capability,payload,**kwargs):
        calls.append((capability,copy.deepcopy(payload),kwargs))
        return {'ok':capability!='desktop.window.read','observed':False,'observation':{'error':RAW},'literal':RAW}
    with tempfile.TemporaryDirectory(prefix='helene-native-computer-') as folder,ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules,{'body_client':client}))
        stack.enter_context(patch.object(body,'HAS_BODY',present))
        stack.enter_context(patch.object(body,'_BODY',target))
        stack.enter_context(patch.dict(body.STATE,{'enabled':enabled,'scopes':list(body.SCOPES) if scopes is None else scopes,
            'available':True,'connected':True,'reason':RAW},clear=True))
        stack.enter_context(patch.object(body,'_settings',return_value=('http://not-contacted','test','pc')))
        stack.enter_context(patch.object(body,'call',side_effect=backend))
        stack.enter_context(patch.object(owner_stop,'stopped',return_value=False))
        stack.enter_context(patch.object(body.sys,'platform',platform))
        body.install(subject,Path(folder),{'computer':{'enabled':enabled,'scopes':list(body.SCOPES) if scopes is None else scopes}})
        yield subject,calls,original_calls,answer,client


class NativeComputerLanguages(unittest.TestCase):
    def test_actual_install_preserves_literal_results_and_image_metadata_on_all_targets(self):
        for platform,remote in (('win32',False),('darwin',False),('linux',False),('linux',True)):
            with self.subTest(platform=platform,remote=remote),installed(platform,remote) as (subject,calls,original,answer,client):
                self.assertIs(subject.TOOL_IMPL['computer'](action='read',path=RAW),answer)
                self.assertEqual(answer.images,('unchanged-image-metadata',))
                self.assertEqual(original,[{'action':'read','path':RAW}]);self.assertFalse(calls)
                self.assertIs(client.state_line(),answer)
                before=copy.deepcopy(subject.BASE_TOOLS)
                self.assertEqual(body.describe_for_mac(subject),0)
                self.assertFalse(any(body.speak_mac(subject).values()))
                self.assertFalse(any(body.speak_linux(subject).values()))
                self.assertEqual(subject.BASE_TOOLS,before)
                self.assertEqual(subject.HAND_PURPOSE,{'computer':RAW})
                self.assertEqual(subject.POINTER_PURPOSE,{'computer':RAW})

    def test_actual_installed_catalog_and_K_follow_frame_language_for_all16_axes_and_targets(self):
        for platform,remote in (('win32',False),('darwin',False),('linux',False),('linux',True)):
            with installed(platform,remote) as (subject,calls,original,answer,client):
                raw=subject.BASE_TOOLS[0];rule=tool_language._raw_rule(raw)
                self.assertIsNotNone(rule);self.assertEqual(len(rule['descriptions']),60)
                self.assertTrue(all(getattr(subject,name)[0] is raw for name in body._TOOL_LISTS))
                props=raw['input_schema']['properties']
                self.assertEqual(props['read']['properties']['backend']['enum'],['auto','win32'] if platform=='win32' or remote else ['auto'])
                for ui in frame_language.LANGUAGES:
                    for language in frame_language.LANGUAGES:
                        with self.subTest(platform=platform,remote=remote,ui=ui,frame=language),patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                            rendered=tool_language.apply([raw])[0]
                            self.assertEqual(structural(rendered),structural(raw))
                            for entry in rule['descriptions']:
                                value=rendered
                                for part in entry['path']:value=value[part]
                                self.assertEqual(value,tool_language._render(entry))
                            self.assertEqual(agent._hand_purpose(raw),agent._hand_purpose(rendered))
                            with patch.object(agent,'BASE_TOOLS',[]),patch.object(agent,'OWNER_TOOLS',[raw]):
                                catalog={tool['name']:tool for tool in agent.catalog_tools_for(agent.ChannelContext(owner=True))}
                            self.assertEqual(catalog['computer'],rendered)
                self.assertFalse(calls);self.assertFalse(original)

    def test_augmented_dispatch_signature_and_remote_target_use_single_windows_call(self):
        events=[{'type':'text','text':RAW}]
        for platform,remote in (('win32',False),('darwin',False),('linux',False),('linux',True)):
            with installed(platform,remote) as (subject,calls,original,answer,client):
                hand=subject.TOOL_IMPL['computer']
                ask={'action':'step','idempotency_key':'literal-intent','hwnd':'0x123','expected_pid':42,
                     'events':events,'settle_ms':0,'read':{'text_contains':RAW}}
                inspect.signature(hand).bind(**ask)
                result=json.loads(hand(**ask))
                self.assertTrue(result['ok']);self.assertFalse(result['observed']);self.assertFalse(original)
                expected=['desktop.input.perform_and_read'] if platform=='win32' or remote else ['desktop.input.perform','desktop.window.read']
                self.assertEqual([call[0] for call in calls],expected)
                payload=calls[0][1]['input'] if len(calls)==1 else calls[0][1]
                self.assertEqual(payload['events'],events)
                self.assertEqual(result['observation']['error'] if len(calls)==1 else result['observation']['read']['literal'],RAW)

    def test_localized_refusals_are_recorded_and_never_dispatch(self):
        for language in frame_language.LANGUAGES:
            with frame_language.use(language),installed(scopes=['computer.read']) as (subject,calls,original,answer,client):
                slot=tool_report.new();token=tool_report.attach(slot)
                try:
                    result=subject.TOOL_IMPL['computer'](action='step',idempotency_key='test',hwnd='0x123',events=[{}])
                finally:tool_report.detach(token)
                self.assertEqual(result,frame_language.text('native.computer.step.rights'))
                self.assertEqual(slot['outcome'],'refused');self.assertFalse(calls);self.assertFalse(original)
            with frame_language.use(language),installed(enabled=False) as (subject,calls,original,answer,client):
                slot=tool_report.new();token=tool_report.attach(slot)
                try:result=subject.TOOL_IMPL['computer'](action='read',path=RAW)
                finally:tool_report.detach(token)
                expected=frame_language.text('native.body.disabled',where=frame_language.text('native.body.available'))
                self.assertEqual(result,expected);self.assertEqual(slot['outcome'],'refused')
                self.assertFalse(calls);self.assertFalse(original)

    def test_authored_schema_is_literal_and_step_declines_unknown_shape(self):
        with installed('darwin',authored=True) as (subject,calls,original,answer,client):
            raw=subject.BASE_TOOLS[0]
            self.assertEqual(raw['description'],RAW);self.assertNotIn('read',raw['input_schema']['properties'])
            self.assertFalse(getattr(subject.TOOL_IMPL['computer'],'_helene_step',False))
            self.assertIs(tool_language.apply([raw])[0],raw)
            self.assertIs(subject.TOOL_IMPL['computer'](action='read',path=RAW),answer)
            self.assertFalse(calls)

    def test_absent_body_removes_all_promises_without_grant_or_start(self):
        with installed(present=False) as (subject,calls,original,answer,client):
            self.assertNotIn('computer',subject.TOOL_IMPL)
            self.assertTrue(all(not getattr(subject,name) for name in body._TOOL_LISTS))
            self.assertTrue(all(not subject._computer_allowed(scope) for scope in body.SCOPES))
            self.assertFalse(calls);self.assertFalse(original)

    def test_owned_parts_reject_free_text_and_deep_or_malformed_composition(self):
        leaf={'key':'native.computer.step.note'}
        tool_language._validate_template({'parts':[leaf,leaf]})
        invalid=[{}, {'parts':[]},{'parts':['free authored words']},{'parts':[leaf]*5},
                 {'parts':[leaf],'key':'native.computer.step.note'}, {'key':leaf['key'],'values':{'x':'extra'}}]
        deep=leaf
        for _ in range(5):deep={'parts':[deep]}
        invalid.append(deep)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises((ValueError,KeyError)):
                tool_language._validate_template(value)

    def test_body_state_captions_translate_only_owned_words_with_literal_identity(self):
        probe={'ok':True,'identity':{'kind':RAW,'integrity':RAW},'manifest':{'execution_contexts':[
            {'kind':'interactive','available':False},{'kind':'system','available':True}]}}
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(body_client,'available',return_value=True),patch.object(body_client,'status_probe_cached',return_value=probe),patch.object(body_client,'_settings',return_value=('url','token',RAW)):
                    line=body_client.state_line()
                    suffix='; interactive='+frame_language.text('body.state.not_connected')+', system='+frame_language.text('body.state.ready')
                    self.assertEqual(line,frame_language.text('body.state.summary',device=RAW,kind=RAW,integrity=RAW,protocol=body_client.PROTOCOL,suffix=suffix))
                    self.assertEqual(line.count(RAW),3)
                with frame_language.use(language),patch.object(body_client,'available',return_value=True),patch.object(body_client,'status_probe_cached',return_value={'ok':False,'error':RAW}):
                    self.assertEqual(body_client.state_line(),frame_language.text('body.state.offline',detail=RAW))


if __name__=='__main__':unittest.main()
