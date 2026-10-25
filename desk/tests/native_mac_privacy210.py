"""Observe network calls and deny optional LAN access in a disposable runner."""
from pathlib import Path
import json,subprocess

def instrument(root,evidence):
    code='''import json,os,sys,traceback
from pathlib import Path
def audit(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'):
        try:
            with Path(DESTINATION).open('a') as out:out.write(json.dumps({'event':event,'pid':os.getpid(),'args':repr(args),'stack':traceback.format_stack(limit=7)})+'\\n')
        except OSError:pass
sys.addaudithook(audit)
'''.replace('DESTINATION',repr(str(evidence/'network-calls.jsonl')))
    (root/'app/localharness/native_network_audit.py').write_text(code)
    for name in ('python_app.py','mac_migrate.py'):
        path=root/'app'/name;text=path.read_text();needle='import argparse\n'
        assert needle in text
        path.write_text(text.replace(needle,needle+'import localharness.native_network_audit\n',1))

def deny_network_prompts(evidence):
    # This affects only this ephemeral CI account and deliberately tests the
    # denied path. Never change system defaults or grant global permissions.
    script='''tell application "System Events"
set clicked to 0
repeat with p in (processes whose name is "UserNotificationCenter" or name is "CoreServicesUIAgent")
repeat with w in windows of p
repeat with b in (buttons of w whose name is "Don't Allow")
click b
set clicked to clicked + 1
end repeat
end repeat
end repeat
return clicked
end tell'''
    result=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=12)
    with (Path(evidence)/'privacy-denial.jsonl').open('a') as stream:
        stream.write(json.dumps({'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})+'\n')
