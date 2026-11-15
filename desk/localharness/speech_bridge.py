"""Runtime capability: one asynchronous speech call, application-owned delivery."""
from __future__ import annotations
from pathlib import Path
from contextlib import nullcontext
import copy
from functools import wraps
import hashlib
from . import essential_backup
from .async_speech import SpeechJobs

DESCRIPTION=('Озвучить текст асинхронно. Вызови один раз и продолжай работу: приложение само '
    'сгенерирует и доставит голос, когда он будет готов. Не жди, не опрашивай результат, '
    'не проверяй аудио и не вызывай send_media или отправку повторно.')
DESCRIPTION_EN=('Speak asynchronously to the current chat. Call once and continue: the application '
    'generates and automatically delivers the voice when ready, even after this turn ends. '
    'Do not wait, poll, review/transcribe the audio, or call send_media/delivery again. '
    'No text introduction, reply call, or follow-up turn is required.')
TURN_NOTE=('Application voice capability: speak(text) is an independent asynchronous delivery channel. '
    'The reply-only rule concerns text. After speak is accepted, continue or finish the turn; '
    'the application delivers audio automatically. No text introduction, audio inspection, '
    'polling, send_media, second reply, or follow-up turn is needed.')


def _language(agent):
    return (getattr(agent,'frame_language',None)
            if getattr(agent,'FRAME_TEXT_BOUNDARIES_API',0)==1 else None)


def _word(agent,key,fallback,**values):
    language=_language(agent)
    return language.text('native.speech.'+key,**values) if language else fallback.format_map(values)


def _pinned(agent,function):
    @wraps(function)
    def call(*args,**kwargs):
        language=_language(agent)
        with language.pin() if language else nullcontext():
            return function(*args,**kwargs)
    return call


def turn_note(agent):
    return _word(agent,'turn_note',TURN_NOTE)


def _install_words(agent):
    layer=getattr(agent,'tool_language',None)
    modern=getattr(layer,'NATIVE_SCHEMA_LANGUAGE_API',0)==1
    for tool in getattr(agent,'TOOLS',()):
        if not isinstance(tool,dict) or tool.get('name')!='speak':continue
        if not modern:
            tool['description']=DESCRIPTION
            continue
        # The application adapter owns its changed async contract, but not an
        # agent's authored description. Preserve all callable fields and inherit
        # only unchanged, already-reviewed parameter descriptions.
        if not layer.accepts_variant(tool):continue
        after=copy.deepcopy(tool);after['description']=DESCRIPTION
        if layer.register_variant(tool,after,descriptions=[{'path':['description'],'key':'native.speech.description'}],purpose='native.speech.purpose'):
            tool.clear();tool.update(after)


def install_projection(agent):
    if getattr(getattr(agent,'tool_language',None),'NATIVE_SCHEMA_LANGUAGE_API',0)==1:
        return
    projection = getattr(agent, 'tool_text_en', None)
    if projection is None or getattr(projection, '_helene_async_speech', False):
        return
    original = projection.apply
    def apply(tools):
        result = original(tools)
        return [dict(tool, description=DESCRIPTION_EN if projection.enabled() else DESCRIPTION)
            if isinstance(tool, dict) and tool.get('name') == 'speak' else tool for tool in result]
    projection.apply = apply
    pointer = getattr(projection, 'pointer_purpose', None)
    if pointer is not None:
        def purpose(name, russian):
            if name == 'speak':
                return ('queue voice once; application delivers automatically; no follow-up'
                    if projection.enabled() else 'озвучить один раз; приложение доставит само, без проверки и повторной отправки')
            return pointer(name, russian)
        projection.pointer_purpose = purpose
    projection._helene_async_speech = True

def install(agent,tree:Path,config_path:Path,*,boundary,owner,deliver,failed=None):
    required=('tool_speak','_TURN_CHANNEL','run_context','_runs')
    missing=[name for name in required if not hasattr(agent,name)]
    if missing:raise RuntimeError('Authored speech admission unavailable: '+', '.join(missing))
    if hasattr(agent,'_application_speech'):return agent._application_speech
    tree,config_path=Path(tree),Path(config_path)
    def configuration():
        value=essential_backup.load(config_path);value['_speech_epoch']=config_path.stat().st_mtime_ns
        return value
    def cancelled(run_id):return boundary.cancelled(agent._runs().manifest(run_id))
    def preflight(job):
        guard=getattr(agent,'guard_outbound_reply',None)
        if guard is None:return
        ctx=agent._TURN_CHANNEL.get();convo=getattr(agent,'_TURN_CONVO',None);orient=getattr(agent,'_TURN_ORIENT',None)
        for text in (job['text'],job['caption']):
            if not text:continue
            accepted=guard(text,str(convo.get() or '') if convo else '',sink={},ctx=ctx,
                orient=str(orient.get() or '') if orient else '',tool_trace='')
            if not accepted or accepted.strip()!=text.strip():raise RuntimeError('Speech text not admitted for this recipient')
    def delivery(job,audio,check):
        with owner.LOCK,boundary.delivery(agent._runs,job['run_id']) as allowed:
            if not allowed:raise InterruptedError('Speech cancelled before delivery')
            check();return deliver(job,audio,check)
    service=SpeechJobs(tree,configuration=configuration,cancelled=cancelled,preflight=preflight,delivery=delivery,failed=failed)
    agent._application_speech=service
    def speak(text:str,caption:str='')->str:
        ctx=agent._TURN_CHANNEL.get();current=agent.run_context.current_run()
        run_id=str(current.run_id) if current is not None else ''
        execution=getattr(agent,'current_tool_execution',lambda:None)() or {}
        if not execution:
            scope=getattr(agent,'_TOOL_EXECUTION',None);execution=scope.get() if scope is not None else {}
        call_id=(execution or {}).get('call_id');chat_id=str(getattr(ctx,'chat_id','') or '')
        if not run_id or not call_id or not chat_id:return _word(agent,'missing','Озвучка недоступна: нет подтверждённого хода, вызова или адресата.')
        identity=hashlib.sha256((run_id+':'+str(call_id)).encode()).hexdigest()[:32]
        try:job=service.submit(text,caption,identity=identity,run_id=run_id,chat_id=chat_id)
        except (OSError,ValueError,RuntimeError,InterruptedError) as exc:return _word(agent,'refused','Озвучка не принята: {error}',error=type(exc).__name__)
        return _word(agent,'accepted',
            'Запрос озвучки принят ({id}, {status}). Продолжай работу: приложение отвечает за синтез и доставку; возможны сбой или отмена. Ждать, проверять аудио или повторно отправлять его не нужно.',
            id=job['id'],status=job['status'])
    speak=_pinned(agent,speak)
    speak.__doc__=DESCRIPTION
    if not hasattr(agent,'_helene_original_speak'):agent._helene_original_speak=agent.tool_speak
    agent.tool_speak=speak
    if isinstance(getattr(agent,'TOOL_IMPL',None),dict):agent.TOOL_IMPL['speak']=speak
    _install_words(agent)
    install_projection(agent)
    return service
