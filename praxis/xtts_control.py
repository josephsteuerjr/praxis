"""Explicit host-side XTTS configuration. Prints no credentials or speech text."""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from xtts_protocol import XttsError, digest, encoded
from xtts_tts_client import XttsTTSClient


from xtts_profiles import write_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path("/opt/praxis-xtts"))
    parser.add_argument("--live-root", type=Path, default=Path("/opt/praxis"))
    parser.add_argument("--actor", default="operator")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status")
    commands.add_parser("catalog")
    configure = commands.add_parser("configure")
    voices = configure.add_mutually_exclusive_group(required=True)
    voices.add_argument("--speaker")
    voices.add_argument("--reference", type=Path)
    configure.add_argument("--language", default="ru")
    commands.add_parser("enable")
    commands.add_parser("disable")
    args = parser.parse_args()
    if args.action in {"status", "catalog"}:
        return execute(args)
    import fcntl
    args.prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.prefix / "control.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return execute(args)


def execute(args):
    if args.action == "catalog":
        from xtts_protocol import file_digest
        model = args.prefix / "model"
        manifest = json.loads((model / "manifest.json").read_text())
        if file_digest(model / "speakers_xtts.pth") != manifest["files"]["speakers_xtts.pth"]:
            raise XttsError("model_integrity")
        program = "import torch,sys,json; voices=torch.load(sys.argv[1],map_location='cpu',weights_only=True); print(json.dumps(sorted(voices),ensure_ascii=False))"
        subprocess.run([str(args.prefix / "runtime/bin/python"), "-B", "-c", program,
                        str(model / "speakers_xtts.pth")], check=True)
        return
    home = args.live_root / "memory/.state/xtts"
    chosen = home / "selection.json"
    client = XttsTTSClient(home / "scratch-client", socket_path=home / "xtts.sock",
                           token_file=args.live_root / "memory/.secrets/xtts-client.token", timeout=600)
    if args.action == "status":
        try:
            health = client.health()
        except XttsError as exc:
            health = {"ready": False, "phase": exc.code}
        selection = json.loads(chosen.read_text()) if chosen.exists() else {"enabled": False}
        print(json.dumps({"selection": selection, "service": health}, ensure_ascii=False))
        return
    disabled = {"schema": "praxis.xtts.selection.v1", "enabled": False}
    if args.action == "configure":
        reference = args.reference.resolve(strict=True) if args.reference else None
        if reference and (not reference.is_file() or reference.suffix.lower() != ".wav"):
            raise XttsError("reference_must_be_local_wav")
        if not args.language or not 1 <= len(args.language) <= 10:
            raise XttsError("invalid_language")
        write_config(chosen, disabled, actor=args.actor)
        write_config(args.prefix / "profile.json", {"speaker": args.speaker or "",
                     "reference": str(reference or ""), "language": args.language}, actor=args.actor)
        print(json.dumps({"configured": True, "enabled": False, "next": "enable"}))
        return
    if args.action == "disable":
        write_config(chosen, disabled, actor=args.actor)
        subprocess.run(["systemctl", "disable", "--now", "praxis-xtts.service"], check=True)
        print(json.dumps({"enabled": False}))
        return
    profile = json.loads((args.prefix / "profile.json").read_text())
    if not profile.get("speaker") and not profile.get("reference"):
        raise XttsError("voice_not_configured")
    subprocess.run(["systemctl", "restart", "praxis-xtts.service"], check=True)
    try:
        health = wait_ready(client, time.monotonic() + 610)
        # CLI and MiniApp share the same canonical profile identifiers.
        if profile.get('reference'):
            result=client.add_voice(profile['reference'],Path(profile['reference']).stem[:80],actor=args.actor)
            voice=next(row for row in client.voices()['voices'] if row['id']==result['voice_id'])
        else:
            voice=next(row for row in client.voices()['voices'] if row['name']==profile.get('speaker'))
        catalogue=client.voices()
        value=client.command('voice_select',{'voice_id':voice['id'],'model_id':voice['model_id'],
                 'profile_id':voice['profile_id'],'expected_revision':catalogue['selection_revision'],
                 'operation_id':uuid.uuid4().hex},actor=args.actor)
        subprocess.run(["systemctl", "enable", "praxis-xtts.service"], check=True)
        print(json.dumps(value))
    except BaseException:
        subprocess.run(["systemctl", "stop", "praxis-xtts.service"], check=False)
        raise


def wait_ready(client, deadline):
    while time.monotonic() < deadline:
        try:
            health = client.health()
        except XttsError:
            health = {}
        if health.get("ready"):
            return health
        if health.get("phase") == "failed":
            raise XttsError("model_load_failed")
        time.sleep(1)
    raise XttsError("warmup_timeout")


if __name__ == "__main__":
    try:
        main()
    except XttsError as error:
        raise SystemExit(error.code)
