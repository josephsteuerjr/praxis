"""First private Apple Silicon provisioning, NOT the routine update mechanism.

Operates only in a new caller-selected directory. Uses the existing pinned POSIX
runtime and assembler. Subsequent updates belong to app/localharness/python_updates.
No service, privileged broker, global pip, login, old profile or installation edit.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_python_posix import prepare, runtime_recipe
from python_posix_bundle import assemble
from localharness.python_platform import runtime_target


def verify_kit(root):
    """The producer pins selected source and prebuilt UI, not personal state."""
    manifest = json.loads((root/'kit-files.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 'helene-private-source-kit.v1':
        raise ValueError('Unknown private source kit')
    for name, digest in manifest['files'].items():
        path = root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unconfined kit input')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest:
            raise ValueError('Kit input changed: '+name)
    return manifest


def provision(root, *, resume=False, relay=None, relay_source=None, body=None, bridge=None):
    root = Path(root).resolve(strict=True)
    if runtime_target() != 'macos-arm64':
        raise RuntimeError('This private candidate requires a native Apple Silicon Mac')
    manifest = verify_kit(root)
    program = root/'Helene'
    completion=program/'private-provision-complete.json'
    if program.exists():
        if completion.is_file() and json.loads(completion.read_text()).get('target')=='macos-arm64':
            if any((relay,relay_source,body,bridge)):
                raise ValueError('Existing program retained; add native components through the app, not seed provisioning')
            return program
        raise FileExistsError('Ambiguous existing program retained; never seed-overwrite it. Inspect the failed attempt.')
    prepared = root/'.prepare'
    runtime = prepared/'python'
    if not (prepared/'runtime-input.json').is_file():
        runtime = prepare(prepared, root/'.cache', resume=resume, target='macos-arm64')
    else:
        actual = json.loads((prepared/'runtime-input.json').read_text(encoding='utf-8'))
        recipe = runtime_recipe('macos-arm64')
        if actual.get('target') != 'macos-arm64' or actual.get('publisher_sha256') != recipe['sha256']:
            raise ValueError('Prepared runtime belongs to another target/publisher input')
    # Explicit reduced native-input capability, never a Windows binary substitute.
    # A caller may supply separately built matching Mac inputs for qualification.
    attempt=root/('.assemble-'+uuid.uuid4().hex);attempt.mkdir()
    stage=attempt/'Helene'
    archive = assemble(runtime, root/'source/helene/core', relay, relay_source,
        stage, version='1.9.4', target='macos-arm64', body=body, bridge=bridge,
        without_relay=relay is None)
    receipt = {'format':'helene-private-provision.v1', 'target':'macos-arm64',
        'source_manifest_sha256':hashlib.sha256((root/'kit-files.json').read_bytes()).hexdigest(),
        'program':str(program), 'archive':str(archive),
        'native_inputs':{'relay':bool(relay),'body':bool(body),'bridge':bool(bridge)},
        'qualification':'NOT native-qualified by preparation. Run disposable platform consumers.',
        'routine_update':'Existing Python in-app updater; do not rerun provisioning over a program',
        'limitations':['No privileged broker or service','No qualified tray/autostart',
            'Optional local voice/toolchain payloads unavailable until target-qualified',
            'Without native inputs: direct-provider local agent only; relay/native CU unavailable']}
    # Completion belongs to the successful stage BEFORE its atomic promotion.
    # A crash cannot expose final Helene/ with only the early version passport.
    (stage/'private-provision-complete.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    stage.rename(program)
    pending=root/('.provision-receipt-'+uuid.uuid4().hex)
    pending.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    pending.replace(root/'provision-receipt.json')
    return program


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', type=Path, required=True)
    parser.add_argument('--resume', action='store_true', help='Resume one incomplete first provisioning only')
    parser.add_argument('--relay', type=Path);parser.add_argument('--relay-source', type=Path)
    parser.add_argument('--body', type=Path);parser.add_argument('--bridge', type=Path)
    parser.add_argument('--qualify', action='store_true', help='Run native disposable custody tests; not live profile tests')
    parser.add_argument('--launch', action='store_true', help='Open this new private program, never an existing installation')
    args = parser.parse_args()
    program = provision(args.kit, resume=args.resume, relay=args.relay,
        relay_source=args.relay_source, body=args.body, bridge=args.bridge)
    python = program/'runtime/bin/python3'
    if args.qualify:
        subprocess.run([str(python),'-I',str(args.kit/'source/desk/tests/t_darwin_owner_2410.py')],check=True)
    print(json.dumps({'program':str(program),'native_qualification':'pending unless actual tests above passed'}),flush=True)
    if args.launch:
        os.execv(str(python),[str(python),'-s',str(program/'app/python_app.py'),'--root',str(program)])


if __name__ == '__main__':
    main()
