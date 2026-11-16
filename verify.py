
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,time,urllib.request,zipfile
expected=json.loads(Path('expected.json').read_bytes())
base='https://github.com/josephsteuerjr/praxis/releases/download/v2.3.0-rc.18/'
def one(pair):
 name,row=pair;path=Path(name)
 for attempt in range(3):
  digest=hashlib.sha256()
  request=urllib.request.Request(base+name+'?verify='+str(time.time_ns()),headers={'User-Agent':'Helene286-component-verification','Accept':'application/octet-stream','Cache-Control':'no-cache'})
  with urllib.request.urlopen(request,timeout=180) as stream,path.open('wb') as out:
   while raw:=stream.read(4*1024*1024):digest.update(raw);out.write(raw)
  if path.stat().st_size==row['size'] and digest.hexdigest()==row['sha256']:break
 else:raise AssertionError('Incomplete or wrong public component: '+name)
 if name.endswith('.zip'):
  with zipfile.ZipFile(path) as package:assert package.testzip() is None
 result={'name':name,**row,'anonymous':True,'full_crc':name.endswith('.zip')}
 path.unlink();print(json.dumps(result),flush=True);return result
with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,expected.items()))
Path('PUBLIC-VOICE-VERIFIED.json').write_text(json.dumps({'passed':True,'resources':rows,'original_component_bytes':True,'authorization_headers_sent':False},indent=2))
