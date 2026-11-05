"""Use the server's PyAV wheel contract instead of the legacy desktop pin.

Keep every other distribution requirement. In particular, never attempt an
unplanned FFmpeg/compiler source build on a user's VPS during transfer.
"""
from pathlib import Path
import argparse
import re


def prepare(source, output):
    lines = Path(source).read_text(encoding='utf-8-sig').splitlines()
    lines = [line for line in lines if not re.match(r'^\s*av\s*(?:[<>=!~;\[]|$)',line,re.I)]
    Path(output).write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');a=p.parse_args()
    prepare(a.source,a.output)
