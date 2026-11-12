"""Actual packaged root relay + desk channel; synthetic auth and blocked provider.

The caller owns the disposable container and has enabled relay with an unreachable
proxy. No owner credentials, provider consent or generation are involved.
"""
from pathlib import Path
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request


def check(url, key, container, output):
    output=Path(output);output.mkdir(exist_ok=False)
    inspected=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
    if not inspected['Name'].lstrip('/').startswith(('helene-arm-', 'helene-device-server-')):
        raise ValueError('Use an owned subscription qualification container')
    def request(path,body=None):
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Origin':url}
        query=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        with urllib.request.urlopen(query,timeout=12) as response:return json.load(response)
    def available():
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                state=request('/api/phone/subscription')
                if state.get('available'):return state
            except (OSError,ValueError):pass
            time.sleep(.5)
        raise RuntimeError('Packaged root relay did not become available through desk channel')
    state=available();assert state['browser_login'] is False
    try:request('/api/phone/subscription',{'action':'login','method':'browser'})
    except urllib.error.HTTPError as exc:assert exc.code==409
    else:raise AssertionError('Server offered a loopback browser callback')
    login=request('/api/phone/subscription',{'action':'login','slot':'secondary','method':'device_code'})['login']
    assert login['method']=='device_code' and (login['pending'] or login['error']),login
    if login.get('id'):
        request('/api/phone/subscription',{'action':'cancel_login','slot':'secondary','login_id':login['id']})
    state=request('/api/phone/subscription');assert not state['login']['pending'] and state['login']['device_code'] is None
    for user in ('desk','helene'):
        result=subprocess.run(['docker','exec','--user',user,container,'python','-c',
            "from pathlib import Path;Path('/opt/helene/data/relay/local_auth/control.json').read_bytes()"],capture_output=True)
        assert result.returncode!=0,'Private control descriptor exposed to '+user
    # Import remains a supported alternative and must survive an actual restart.
    claims={'email':'device-fixture@example.invalid','https://api.openai.com/auth':{'chatgpt_plan_type':'pro'}}
    id_token='e30.'+base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')+'.c2ln'
    auth={'tokens':{'access_token':'synthetic-device-access','refresh_token':'synthetic-device-refresh',
                    'id_token':id_token,'account_id':'synthetic-device-account'},
          'last_refresh':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    receipt=request('/api/relay/auth/import',{'auth':auth})
    deadline=time.monotonic()+90
    while receipt['phase']=='queued' and time.monotonic()<deadline:
        time.sleep(1);receipt=request('/api/relay/auth/status?id='+receipt['id'])
    assert receipt['phase']=='active',receipt
    before=available();assert before['slots'][0]['stored'],before
    subprocess.run(['docker','restart','--time','15',container],check=True,capture_output=True,timeout=45)
    # Docker may allocate a different random host port when restarting a
    # container whose published HostPort was empty. Follow the actual mapping.
    restarted=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
    mapped=restarted['NetworkSettings']['Ports']['8094/tcp'][0]['HostPort']
    url='http://127.0.0.1:'+mapped
    after=available();assert after['slots'][0]['stored'],after
    catalog=request('/api/brain-models?refresh=1')
    assert catalog['ok'] and catalog['by_framework']['primary']['models'],catalog
    for user in ('desk','helene'):
        result=subprocess.run(['docker','exec','--user',user,container,'python','-c',
            "from pathlib import Path;Path('/opt/helene/data/relay/local_auth/auth.json').read_bytes()"],capture_output=True)
        assert result.returncode!=0,'Subscription token exposed to '+user
    safe=json.dumps({'before':before,'after':after,'import_phase':receipt['phase']})
    for private in ('synthetic-device-access','synthetic-device-refresh',id_token,'api_key','auth_directory'):
        assert private not in safe,private
    value={'passed':True,'device_control':True,'browser_refused':True,'private_descriptor_denied':['desk','helene'],
           'private_auth_denied':['desk','helene'],'import_and_restart':True,'catalog_read':True,'before':before,'after':after,
           'scope':'Native package and synthetic credentials; no provider consent or generation'}
    (output/'PASS.json').write_text(json.dumps(value,indent=2))
    return value
