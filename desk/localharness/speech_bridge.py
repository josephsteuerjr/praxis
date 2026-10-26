"""Runtime-only speech capability for preserved/older authored agent trees.

Only speak is replaced. Recognition, authored MediaAudio, source files and the
model/runtime directories are untouched. Dispatch still stages through that
agent's own privacy/guard/outbox boundary; this adapter never sends a message.
"""
from __future__ import annotations

from pathlib import Path

from . import essential_backup, speech_client


def install(agent, tree: Path, config_path: Path, *, boundary, owner):
    required = ('tool_speak', '_TURN_CHANNEL', '_TURN_OUTBOUND', 'run_context',
                '_runs', '_stage_turn_media')
    missing = [name for name in required if not hasattr(agent, name)]
    if missing:
        raise RuntimeError('Authored speech staging contract unavailable: ' + ', '.join(missing))
    tree, config_path = Path(tree), Path(config_path)
    def configuration():
        return essential_backup.load(config_path)

    def speak(text: str, caption: str = '') -> str:
        if agent._TURN_CHANNEL.get() is None or agent._TURN_OUTBOUND.get() is None:
            return 'Голосовое можно подготовить только из живого хода Telegram.'
        current = agent.run_context.current_run()
        run_id = current.run_id if current is not None else ''
        def stopped():
            if not run_id:
                return False
            state = agent._runs().manifest(run_id)
            return state.get('status') != 'running' or boundary.cancelled(state)
        audio = None
        try:
            audio = speech_client.synthesize(text, tree, tree / 'media/tts',
                                            configuration=configuration, stopped=stopped)
            # Same locks/order as the edition's cancellation/delivery consumer.
            # Pause winning first suppresses staging. Already admitted staging
            # completes before cancellation is acknowledged; actual delivery is
            # still separately guarded by the existing outbox boundary.
            with owner.LOCK, boundary.delivery(agent._runs, run_id) as allowed:
                if not allowed:
                    raise InterruptedError('Speech cancelled before staging')
                audio.check(stopped)
                outbound = agent._TURN_OUTBOUND.get()
                before = len(outbound)
                note = agent._stage_turn_media(audio.path, kind='audio', caption=caption,
                    voice_note=True, guard_text=f'Точный текст синтезированной речи: {text}', move=True)
                try:
                    # Another process can replace the worker or write Pause
                    # while the authored staging helper is copying bytes.
                    audio.check(stopped)
                except BaseException:
                    for item in outbound[before:]:
                        path = Path(item.path).resolve()
                        if path.is_relative_to(tree.resolve()):
                            path.unlink(missing_ok=True)
                        guards = getattr(agent, '_TURN_MEDIA_GUARD', None)
                        if guards is not None and guards.get() is not None:
                            guards.get().pop(item.queue_id, None)
                    del outbound[before:]
                    raise
                return note
        except InterruptedError:
            return 'Озвучка отменена; аудио не отправлено.'
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            return f'Озвучка недоступна: {str(exc)[:180]}'
        finally:
            if audio is not None:
                # Successful stage(move=True) already moved it. Failed/declined
                # staging must not leave an unclaimed speech artifact behind.
                audio.path.unlink(missing_ok=True)

    if not hasattr(agent, '_helene_original_speak'):
        agent._helene_original_speak = agent.tool_speak
    agent.tool_speak = speak
    if isinstance(getattr(agent, 'TOOL_IMPL', None), dict):
        agent.TOOL_IMPL['speak'] = speak
    return {'installed': True, 'backend': 'application-xtts', 'authored_files_changed': False}
