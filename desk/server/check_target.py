"""Refuse a mismatched native server archive before starting children."""
from pathlib import Path
import json,platform,sys


def check(root,*,machine=None):
    root=Path(root);passport=root/'helene-build.json'
    if not passport.is_file():return None  # legacy source installations
    target=json.loads(passport.read_text(encoding='utf8')).get('server_target')
    if target is None:return None
    arch={'aarch64':'arm64','arm64':'arm64','x86_64':'x86_64','amd64':'x86_64'}.get(str(machine or platform.machine()).lower())
    if target not in ('linux-arm64','linux-x86_64') or target!='linux-'+str(arch):
        raise ValueError('Server archive architecture differs from this host: '+str(target))
    relay=root/'relay-product/praxis-relay-headless'
    if relay.is_file():
        with relay.open('rb') as stream:raw=stream.read(20)
        if raw[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(raw[18:20],'little')!={'arm64':183,'x86_64':62}[arch]:
            raise ValueError('Server relay architecture differs from this host')
    return target


if __name__=='__main__':print(check(sys.argv[1]))
