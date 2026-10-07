"""Actual polkit/PAM password confirmation twice; only a disposable fixture account."""
import json
import os
from pathlib import Path
import pty
import pwd
import re
import select
import subprocess
import sys
import time

window = sys.argv[1:2] == ["--window"]
if sys.argv[1:2] in (["--worker"], ["--window-worker"]):
    # An unprivileged agent matches a desktop polkit agent. pkexec's embedded
    # setuid tty agent has an upstream cookie/UID bug in older polkit versions.
    ready, notify = os.pipe()
    window_worker = sys.argv[1] == "--window-worker"
    subject = sys.argv[2] if window_worker else str(os.getpid())
    agent = subprocess.Popen(["/usr/bin/pkttyagent", "--process", subject,
        "--notify-fd", str(notify)], pass_fds=(notify,))
    os.close(notify)
    try:
        assert select.select([ready], [], [], 10)[0], "polkit agent did not register"
        os.read(ready, 1)
        if window_worker:
            root = Path.home() / ".local/share/helene"
            sys.path.insert(0, str(root / "app/localharness"))
            import broker
            answer = broker.Broker(root / "data", json.loads((root / "helene.json").read_text())).ask(
                "exec", "/usr/bin/id", ["-u"], "window password stand", 5, 60)
            assert "код возврата 0" in answer and "вывод: 0" in answer, answer
            print(json.dumps({"tool_answer": answer}))
            code = 0
        else:
            code = subprocess.run(["/usr/bin/pkexec", "--disable-internal-agent",
                "/opt/helene/helene-svc", "broker-root", sys.argv[2]]).returncode
    finally:
        os.close(ready); agent.terminate(); agent.wait(timeout=5)
    sys.exit(code)

assert os.environ.get("HELENE_BROKER_STAND") == "1"
assert (os.geteuid() == pwd.getpwnam("brokerfix").pw_uid) if window else os.geteuid() == 0
entry = pwd.getpwnam("brokerfix")
password = "BrokerStand-Only-123!"
if not window:
    subprocess.run(["usermod", "-aG", "sudo", "brokerfix"], check=True)
    subprocess.run(["chpasswd"], input=f"brokerfix:{password}\n", text=True, check=True)
request = json.dumps({"op": "exec", "argv": ["/usr/bin/id", "-u"], "why": "password stand"})
config = Path(entry.pw_dir) / ".local/share/helene/helene.json"
cfg = json.loads(config.read_text()); cfg["service"]["session0"] = False
config.write_text(json.dumps(cfg))
prompted = []
for _ in range(2):
    pid, fd = pty.fork()
    if pid == 0:
        worker = [sys.executable, str(Path(__file__).resolve()),
            "--window-worker" if window else "--worker", sys.argv[2] if window else request]
        if window: os.execv(sys.executable, worker)
        os.execv("/usr/sbin/runuser", ["runuser", "-u", "brokerfix", "--", *worker])
    sent = False; selected = False; data = b""; deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if select.select([fd], [], [], .1)[0]:
            try: part = os.read(fd, 8192)
            except OSError: break
            if not part: break
            data += part
            if not selected and b"Choose identity" in data:
                number = re.search(rb"(\d+)\.\s+brokerfix", data).group(1)
                os.write(fd, number + b"\n"); selected = True
            if not sent and b"password:" in data.lower():
                os.write(fd, password.encode() + b"\n"); sent = True
    else:
        os.kill(pid, 9)
    _, status = os.waitpid(pid, 0); os.close(fd)
    assert os.waitstatus_to_exitcode(status) == 0, data.decode(errors="replace")[-1500:]
    replies = [json.loads(line[line.index('{"'):]) for line in data.decode(errors="replace").splitlines() if '{"' in line]
    assert replies, data.decode(errors="replace")[-1500:]
    assert "tool_answer" in replies[-1] if window else replies[-1]["out"].strip() == "0"
    prompted.append(sent)
assert prompted == [True, True], "a second command reused authorization without a password"
cfg["service"]["session0"] = True; config.write_text(json.dumps(cfg))
print(json.dumps({"ok": True, "checks": [
    "agent hand -> window -> pkexec -> root receipt" if window else "interactive root command requires a real password",
    "each command asks again"]}))
