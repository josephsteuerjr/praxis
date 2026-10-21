"""Exercise a packaged headless process with isolated synthetic credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

binary = Path(sys.argv[1]).resolve()
assert binary.is_file()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

foreign = Path.home()/".codex/auth.json"
before = digest(foreign)
checks=[]
def check(condition,name):
    assert condition,name
    checks.append(name)

def wait_for(predicate,timeout=15):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        value=predicate()
        if value:return value
        time.sleep(.1)
    raise AssertionError("Timed out waiting for relay state")

def token(payload):
    return "e30."+base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")+".c2ln"

with tempfile.TemporaryDirectory(prefix="praxis-relay-accept-") as temporary:
    root=Path(temporary)
    auth=root/"auth";auth.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1",0));port=listener.getsockname()[1]
    env=dict(os.environ,RELAY_AUTH_DIR=str(auth),RELAY_PORT=str(port),RELAY_NO_BROWSER="1",
             RELAY_CODEX_VERSION="0.163.0",RELAY_MODEL_DISCOVERY="off",
             HTTPS_PROXY="http://127.0.0.1:1",HTTP_PROXY="http://127.0.0.1:1",NO_PROXY="127.0.0.1,localhost")
    env.pop("RELAY_API_KEY",None);env.pop("RELAY_PYTHON",None)
    check("Praxis Relay" in subprocess.check_output([str(binary),"--version"],env=env,text=True),"version names the independent product")
    with (root/"process.log").open("wb") as log:
        process=subprocess.Popen([str(binary),"serve","--headless"],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        try:
            wait_for(lambda:(auth/"control.json").is_file())
            address=json.loads((auth/"control.json").read_text())
            def request(path,body=None,key=True,origin=None,api_key=None):
                headers={}
                if key:headers["x-relay-control"]=address["token"]
                if api_key:headers["Authorization"]="Bearer "+api_key
                if origin:headers["Origin"]=origin
                if body is not None:headers["Content-Type"]="application/json"
                req=urllib.request.Request(f"http://127.0.0.1:{port}"+path,
                    data=json.dumps(body).encode() if body is not None else None,headers=headers)
                try:
                    with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
                except urllib.error.HTTPError as error:return error.code,json.load(error)
            def command(op,slot=None):
                status,value=request("/_relay/control",dict(op=op,slot=slot,port=None))
                assert status==200,(op,value)
                return value
            check(command("status")["running"],"starts without credentials")
            client_key=command("status")["api_key"]
            patch=root/"patch.json"
            patch.write_text(json.dumps({"appearance":{"theme":"dark","scale":115},"labels":{"primary":"Work"},"defaults":{"model":"configured-model","reasoning":"high"}}))
            completed=subprocess.run([str(binary),"configure",str(patch)],env=env,capture_output=True,text=True,encoding="utf-8",timeout=15)
            configured=json.loads(completed.stdout)
            check(completed.returncode==0 and configured["settings"]["appearance"]["theme"]=="dark" and configured["restart_required"],"CLI configures the running service and reports pending API changes")
            saved=(auth/"settings.json").read_bytes()
            code,_=request("/_relay/control",dict(op="configure",settings={"port":0}))
            check(code==409 and (auth/"settings.json").read_bytes()==saved,"invalid settings preserve the previous document")
            check(isinstance(client_key,str) and len(client_key)==47 and client_key.startswith("sk-") and client_key[3:].isalnum(),"client key matches the required 47-character sk- format")
            check(request("/v1/images/models",api_key=client_key)[0]==200,"generated client key is accepted")
            check(request("/v1/images/models",key=False)[0]==401,"missing client key is rejected")
            check(request("/v1/images/models",api_key="relay")[0]==401,"old relay placeholder is rejected")
            check(request("/_relay/control",dict(op="logout",slot="all"),key=False)[0]==403,"anonymous controls are rejected")
            check(request("/_relay/control",dict(op="logout",slot="all"),origin="https://invalid.example")[0]==403,"browser origins are rejected")
            def write_account(slot):
                home=auth if slot=="primary" else auth/"accounts"/slot
                home.mkdir(parents=True,exist_ok=True)
                payload=dict(OPENAI_API_KEY=None,tokens=dict(
                    id_token=token({"email":slot+"@example.invalid","https://api.openai.com/auth":{"chatgpt_plan_type":"pro"}}),
                    access_token="opaque-synthetic-access-"+slot,refresh_token="synthetic-refresh-"+slot,
                    account_id="synthetic-account-"+slot),last_refresh=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()))
                (home/"auth.json").write_text(json.dumps(payload))
            write_account("primary");write_account("secondary")
            status=command("status")
            check(all(a["configured"] for a in status["accounts"]),"both late credentials are discovered")
            check("synthetic-refresh" not in json.dumps(status),"status never exposes subscription tokens")
            command("switch","secondary")
            check(next(a for a in command("status")["accounts"] if a["active"])["slot"]=="secondary","live switching selects the second account")
            command("logout","secondary")
            check(next(a for a in command("status")["accounts"] if a["active"])["slot"]=="primary","logout selects the remaining account")
            # Exercise the actual interpreter discovery/helper startup without
            # opening a browser or contacting the identity provider.
            original=(auth/"auth.json").read_bytes()
            with socket.socket() as callback:
                callback_free=callback.connect_ex(("127.0.0.1",1455))!=0
            if callback_free:
                command("login","primary")
                status=wait_for(lambda: (value if (value:=command("status")).get("login_url") or not value["login"]["pending"] else None))
                check(bool(status.get("login_url")),"a real Python interpreter starts the OAuth callback")
                command("cancel_login")
                check((auth/"auth.json").read_bytes()==original,"cancelled sign-in preserves the old account")
                def callback_closed():
                    with socket.socket() as probe:return probe.connect_ex(("127.0.0.1",1455))!=0
                check(wait_for(callback_closed),"cancellation releases the OAuth callback port")
            else:
                checks.append("OAuth port occupied: interpreter smoke explicitly skipped")
            command("logout","all")
            check(not any(a["configured"] for a in command("status")["accounts"]),"logout all removes both slots")
            check(command("status")["api_key"]==client_key,"account logout preserves the client key")
            check(request("/health")[0]==200,"management stays alive after last logout")
            completed=subprocess.run([str(binary),"status"],env=env,capture_output=True,text=True,encoding="utf-8",timeout=15)
            check(completed.returncode==0 and json.loads(completed.stdout)["running"],"a second CLI controls the live process")
            completed=subprocess.run([str(binary),"stop"],env=env,capture_output=True,text=True,encoding="utf-8",timeout=15)
            check(completed.returncode==0,"headless stop command succeeds")
            check(process.wait(timeout=10)==0,"headless service exits cleanly")
            check(not (auth/"control.json").exists(),"stop removes its control capability")
            process=subprocess.Popen([str(binary),"serve","--headless"],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
            wait_for(lambda:(auth/"control.json").is_file())
            address=json.loads((auth/"control.json").read_text())
            check(command("status")["api_key"]==client_key,"client key survives an actual process restart")
            restarted=command("status")
            check(restarted["settings"]["labels"]["primary"]=="Work" and restarted["settings"]["appearance"]["scale"]==115 and not restarted["restart_required"],"appearance, account names and model defaults survive process restart and apply to the new API")
            command("stop")
            check(process.wait(timeout=10)==0,"restarted service exits cleanly")
        finally:
            if process.poll() is None:
                try:command("cancel_login")
                except Exception:pass
                process.terminate();process.wait(timeout=10)
check(digest(foreign)==before,"Codex authentication is unchanged")
print(json.dumps(dict(passed=True,checks=checks),indent=2))
