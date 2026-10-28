"""Prepare enabled voice models before the transferred agent begins receiving messages."""
import argparse
import json
import os
from pathlib import Path
import voice

def prepare(path:Path,connect:bool=False):
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    if connect:
        config['phone']={**(config.get('phone') or {}),'enabled':True,'mode':'automatic'}
        config['phone'].pop('external',None)
        temporary=path.with_suffix('.connecting')
        temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        if os.name!='nt':os.chmod(temporary,0o600)
        os.replace(temporary,path)
    tree=Path(config.get('tree') or 'data')
    if not tree.is_absolute():tree=path.parent/tree
    flags=voice.block(config)
    if flags.get('enabled') and not voice.state(tree,config).get('ready'):
        voice.fetch(tree,voice.chosen_model(config),quiet=True)
    if flags.get('speak') and not voice.speech_state(tree,config).get('ready'):
        voice.fetch_voice(tree,voice.chosen_voice(config),quiet=True)
    hearing=voice.state(tree,config)
    speech=voice.speech_state(tree,config)
    if flags.get('enabled') and not hearing.get('ready'):raise RuntimeError('Слух агента ещё не готов')
    if flags.get('speak') and not speech.get('ready'):raise RuntimeError('Голос агента ещё не готов')
    return {'hearing':bool(hearing.get('ready')),'speech':bool(speech.get('ready'))}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--connect',action='store_true')
    args=parser.parse_args();result=prepare(Path(args.config).resolve(),connect=args.connect)
    print(json.dumps(result))
