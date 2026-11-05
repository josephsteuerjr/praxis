"""Prepare enabled voice models before the transferred agent begins receiving messages."""
import argparse
import json
from pathlib import Path
import voice
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def prepare(path:Path,connect:bool=False):
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    if connect:
        config['phone']={**(config.get('phone') or {}),'enabled':True,'mode':'automatic'}
        config['phone'].pop('external',None)
        from private_files import write_private
        write_private(path,(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode())
    tree=Path(config.get('tree') or 'data')
    if not tree.is_absolute():tree=path.parent/tree
    from localharness import standalone_relay, server_components
    server_components.prepare(path.parent, tree)
    standalone_relay.ensure_configured(path.parent,config)
    flags=voice.block(config)
    if flags.get('speak') and voice.chosen_voice(config) == 'fish':
        from localharness import fish_speech
        fish_speech.profile(flags)
        fish_speech._key(tree)  # Actual role access, no network or paid request.
    if flags.get('enabled') and not voice.state(tree,config).get('ready'):
        voice.fetch(tree,voice.chosen_model(config),quiet=True)
    if flags.get('speak') and not voice.speech_state(tree,config).get('ready'):
        if voice.chosen_voice(config) == 'xtts' and not (path.parent/'python-app.json').is_file():
            raise RuntimeError('Для XTTS на VPS подготовь Linux runtime и модель на сервере или выбери Fish перед переносом. Данные и Stop сохранены.')
        voice.fetch_voice(tree,voice.chosen_voice(config),quiet=True)
    hearing=voice.state(tree,config)
    speech=voice.speech_state(tree,config)
    if flags.get('enabled') and not hearing.get('ready'):raise RuntimeError('Слух агента ещё не готов')
    if flags.get('speak') and not speech.get('ready'):raise RuntimeError('Голос агента ещё не готов')
    server_components.prepare(path.parent, tree)
    return {'hearing':bool(hearing.get('ready')),'speech':bool(speech.get('ready'))}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--connect',action='store_true')
    args=parser.parse_args();result=prepare(Path(args.config).resolve(),connect=args.connect)
    print(json.dumps(result))
