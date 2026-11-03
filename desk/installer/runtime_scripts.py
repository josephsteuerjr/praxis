"""Relocate generated Python console entries without a build-machine interpreter.

Only copied runtime/bin files with a direct Python shebang are transformed.
Wheel RECORD hashes are updated for these deliberate distribution changes.
"""
from pathlib import Path
import base64,csv,hashlib,io,posixpath,re

def digest(raw):return hashlib.sha256(raw).hexdigest()

def make_portable(runtime):
    runtime=Path(runtime).resolve();changed={};records={}
    for script in sorted((runtime/'bin').iterdir()):
        if script.is_symlink():raise ValueError('Linked console input')
        if not script.is_file() or script.stat().st_size>1024*1024:continue
        raw=script.read_bytes();line,separator,body=raw.partition(b'\n')
        if not separator or not re.fullmatch(rb'#![^\r\n]+/python(?:[0-9.]+)?\r?',line):continue
        # This standard shell/Python polyglot always chooses its sibling Python.
        # The program's no-symlink distribution policy keeps dirname sufficient.
        prefix=b'#!/bin/sh\n\'\'\'exec\' "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/python3" -s "$0" "$@"\n\' \'\'\'\n'
        rewritten=prefix+body
        compile(rewritten,str(script),'exec',dont_inherit=True)
        script.write_bytes(rewritten)
        name=script.relative_to(runtime).as_posix()
        changed[name]={'before_sha256':digest(raw),'sha256':digest(rewritten),'size':len(rewritten)}
    for record in sorted(runtime.glob('lib/python*/site-packages/*.dist-info/RECORD')):
        rows=list(csv.reader(io.StringIO(record.read_text(encoding='utf-8'))));updated=False
        base=record.parent.parent.relative_to(runtime).as_posix()
        for row in rows:
            if len(row)!=3:raise ValueError('Malformed wheel RECORD')
            relative=posixpath.normpath(base+'/'+row[0])
            if relative not in changed:continue
            raw=(runtime/relative).read_bytes()
            row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode('ascii')
            row[2]=str(len(raw));updated=True
        if updated:
            old=record.read_bytes();stream=io.StringIO(newline='');csv.writer(stream,lineterminator='\n').writerows(rows)
            record.write_bytes(stream.getvalue().encode('utf-8'))
            records[record.relative_to(runtime).as_posix()]={'before_sha256':digest(old),'sha256':digest(record.read_bytes())}
    return {'format':'helene-portable-runtime-scripts.v1','scripts':changed,'wheel_records':records,
        'policy':'Only generated direct Python shebangs; sibling interpreter, no global Python dependency'}
