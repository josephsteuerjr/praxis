"""Prepare only a disposable systemd container, never an owner's Linux installation."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

assert os.environ.get("HELENE_BROKER_STAND") == "1" and os.geteuid() == 0
subprocess.run(["useradd", "-m", "brokerfix"], check=False, capture_output=True)
entry = pwd.getpwnam("brokerfix")
program = Path("/opt/helene")
subprocess.run(["runuser", "-u", "brokerfix", "--", str(program / "helene-svc"), "home"], check=True, stdout=subprocess.DEVNULL)
root = Path(entry.pw_dir) / ".local/share/helene"
cfg = json.loads((root / "helene.json").read_text())
cfg.update(setup_complete=True, agent_mode="interactive", agent={"name": "Broker fixture"})
cfg["model"].update(key="fixture", model="fixture", base_url="http://127.0.0.1:9/v1")
cfg["telegram"] = {"enabled": False}
cfg["service"]["session0"] = True
cfg["computer"]["enabled"] = False
(root / "helene.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
os.chown(root / "helene.json", entry.pw_uid, entry.pw_gid)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_linux
Path("/usr/share/polkit-1/actions/app.helene.policy").write_text(build_linux.POLKIT_POLICY)
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "enable", "--now", "helene-broker.service", "helene@brokerfix.service"], check=True)
subprocess.run(["systemctl", "restart", "helene@brokerfix.service"], check=True)
print(json.dumps({"owner": "brokerfix", "uid": entry.pw_uid, "root": str(root)}))
