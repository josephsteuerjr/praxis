"""Real root socket and password-confirmed CLI in the disposable brokerfix home."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time

assert os.environ.get("HELENE_BROKER_STAND") == "1" and os.geteuid() == 0
entry = pwd.getpwnam("brokerfix")
root = Path(entry.pw_dir) / ".local/share/helene"
config = root / "helene.json"
def save(session0):
    cfg = json.loads(config.read_text()); cfg["service"]["session0"] = session0
    config.write_text(json.dumps(cfg)); os.chown(config, entry.pw_uid, entry.pw_gid)
def owner(code):
    result = subprocess.run(["runuser", "-u", "brokerfix", "--", "/opt/helene/runtime/bin/python3", "-c", code], text=True, capture_output=True, check=True)
    return result.stdout.strip()
prefix = "import sys;sys.path.insert(0,'/opt/helene/app/localharness');import broker;"
def call(request):
    return json.loads(owner(prefix + f"import json;print(json.dumps(broker.call_linux({request!r},10)))"))
checks = []
save(True)
assert call({"op": "ping"})["ok"]
assert call({"op": "exec", "argv": ["/usr/bin/id", "-u"], "why": "root stand"})["out"].strip() == "0"
answer = owner(prefix + f"from pathlib import Path;root=Path({str(root)!r});" +
    "import json;b=broker.Broker(root/'data',json.loads((root/'helene.json').read_text()));" +
    "print(b.ask('exec','/usr/bin/id',['-u'],'agent hand stand',5,10))")
assert "код возврата 0" in answer and "вывод: 0" in answer, answer
reply = call({"op": "exec", "argv": ["/bin/sh", "-c", "echo stderr >&2; exit 7"], "why": "receipt stand"})
assert reply["ok"] and reply["code"] == 7 and "stderr" in reply["err"]
checks.extend(["root exec without password", "actual agent hand returns root receipt without window", "nonzero exit and stderr receipt"])
start = time.monotonic()
reply = call({"op": "exec", "argv": ["/bin/sleep", "20"], "timeout_sec": 1, "why": "timeout stand"})
assert reply["code"] is None and time.monotonic() - start < 4
checks.append("bounded root command")
sys.path.insert(0, "/opt/helene/app/localharness")
import broker
assert not broker.call_linux({"op": "ping"}, 5)["ok"]  # root peer remains refused
checks.append("root peer refused")
for action in ("stop", "start", "restart"):
    answer = call({"op": "service", "action": action})
    assert answer["ok"], answer
    state = subprocess.run(["systemctl", "is-active", "helene@brokerfix"], capture_output=True, text=True).stdout.strip()
    assert state == ("inactive" if action == "stop" else "active"), state
checks.append("real systemd stop/start/restart through root socket")
save(False)
assert not call({"op": "exec", "argv": ["/usr/bin/id"], "why": "gate stand"})["ok"]
checks.append("session0 off refuses password-free root")
request = {"op": "exec", "argv": ["/usr/bin/id", "-u"], "why": "confirmed stand", "timeout_sec": 5}
confirmed = subprocess.run(["/opt/helene/helene-svc", "broker-root", json.dumps(request)],
    env={**os.environ, "PKEXEC_UID": str(entry.pw_uid)}, capture_output=True, text=True, check=True)
assert json.loads(confirmed.stdout)["out"].strip() == "0"
checks.append("separate confirmed CLI works with session0 off")
denied = owner("import subprocess; r=subprocess.run(['/usr/bin/pkexec','--disable-internal-agent','/opt/helene/helene-svc','broker-root'," + repr(json.dumps(request)) + "],capture_output=True,timeout=15);print(r.returncode)")
assert denied in ("126", "127"), denied
checks.append("interactive pkexec requires authentication")
save(True)
print(json.dumps({"ok": True, "checks": checks}, ensure_ascii=False))
