"""Fresh unauthenticated public downloads, full ZIP/manifest checks and raw Git source."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,stat,subprocess,time,urllib.request,zipfile
R=Path(__file__).resolve().parent;O=R/'public-downloads';O.mkdir();repo='josephsteuerjr/praxis';commit='a8145e56ce122953c9292d6b90232280cd675eea'
expected=json.loads((R/'expected.json').read_bytes());expected={r['name']:r for r in expected}
def fetch_json(url):
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Helene-public-release-verifier','Cache-Control':'no-cache'}),timeout=60) as r:return json.load(r)
release=fetch_json('https://api.github.com/repos/'+repo+'/releases/latest?check='+str(time.time_ns()))
assert release['id']==443161064 and release['tag_name']=='v2.7.0' and not release['draft'] and not release['prerelease'] and 'Beta' in release['name']
assets={a['name']:a for a in release['assets']};assert set(assets)==set(expected)
results=[]
for name,e in expected.items():
 a=assets[name];assert a['size']==e['size'] and a['digest']=='sha256:'+e['sha256'] and a['state']=='uploaded'
 p=O/name
 for attempt in range(4):
  try:
   with urllib.request.urlopen(urllib.request.Request(a['browser_download_url']+'?verify='+str(time.time_ns()),headers={'User-Agent':'Helene-public-release-verifier'}),timeout=90) as response,p.open('wb') as out:
    while raw:=response.read(1024*1024):out.write(raw)
   with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
   assert p.stat().st_size==e['size'] and digest==e['sha256'];break
  except Exception:
   if attempt==3:raise
 row={'name':name,'size':e['size'],'sha256':digest,'public_download':True}
 if name.endswith('.zip'):
  with zipfile.ZipFile(p) as z:
   names=z.namelist();assert len(names)==len(set(names));hashes={}
   for i in z.infolist():
    q=PurePosixPath(i.filename);assert not q.is_absolute() and '..' not in q.parts and '\\' not in i.filename and ':' not in i.filename and not stat.S_ISLNK(i.external_attr>>16)
    if not i.is_dir():
     with z.open(i) as f:hashes[i.filename]=hashlib.file_digest(f,'sha256').hexdigest()
   if 'Helene/python-package-files.json' in hashes:
    manifest=json.loads(z.read('Helene/python-package-files.json'))['files'];assert {n.removeprefix('Helene/'):h for n,h in hashes.items() if n!='Helene/python-package-files.json'}==manifest
    meta=json.loads(z.read('Helene/python-app.json'));assert meta['version']=='2.7.0' and meta['local_candidate'] is False
    assert hashes['Helene/verification/manifest.json']==meta['verification_bundle_sha256']
   elif 'Helene/SERVER-SOURCE.json' in hashes:
    source=json.loads(z.read('Helene/SERVER-SOURCE.json'));assert source['version']=='2.7.0' and source['target']=='linux-arm64'
    for n,r in source['core'].items():assert hashes['Helene/tree/'+n]==r['sha256']
    for n,r in source['application'].items():
     if n not in ('app/desk.json','app/component-downloads.json','server/updater/shipped-code.json'):assert hashes['Helene/'+n]==r['sha256']
   row.update(full_crc_sha=True,zip_entries=len(names))
 results.append(row);print(json.dumps(row),flush=True)
source=json.loads((O/'SOURCE-2.7.0.json').read_bytes());front=json.loads((O/'FRONTENDS-2.7.0.json').read_bytes());assert source['public_commit']==commit and front['source_commit']==commit
rawroot=R/'raw-source';subprocess.run(['git','init',str(rawroot)],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['git','-C',str(rawroot),'fetch','--depth=1','https://github.com/'+repo+'.git','refs/tags/v2.7.0'],check=True)
actual=subprocess.check_output(['git','-C',str(rawroot),'rev-parse','FETCH_HEAD^{commit}']).decode().strip();assert actual==commit
tree=subprocess.check_output(['git','-C',str(rawroot),'ls-tree','-r','-z',commit]);gitrows={}
for record in tree.split(b'\0'):
 if not record:continue
 head,name=record.split(b'\t');mode,kind,oid=head.split();assert kind==b'blob';gitrows[name.decode()]={'oid':oid.decode(),'mode':mode.decode()}
assert set(gitrows)==set(source['files'])
proc=subprocess.Popen(['git','-C',str(rawroot),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
with zipfile.ZipFile(O/'Helene-2.7.0-source.zip') as z:
 assert len(z.namelist())==len(gitrows)
 for n,r in gitrows.items():
  proc.stdin.write((r['oid']+'\n').encode());proc.stdin.flush();header=proc.stdout.readline().split();raw=proc.stdout.read(int(header[2]));assert proc.stdout.read(1)==b'\n'
  assert z.read('Helene-2.7.0-source/'+n)==raw and hashlib.sha256(raw).hexdigest()==source['files'][n]['sha256'] and r['mode']==source['files'][n]['mode']
proc.stdin.close();assert proc.wait()==0
for p in O.glob('Helene-*.zip'):
 with zipfile.ZipFile(p) as z:
  if 'Helene/python-package-files.json' not in z.namelist():continue
  count=generated=0
  for n in z.namelist():
   prefix='Helene/verification/repository/'
   if not n.startswith(prefix) or n.endswith('/'):continue
   rel=n.removeprefix(prefix)
   if rel.startswith('live/'):continue
   if rel in source['files']:assert hashlib.sha256(z.read(n)).hexdigest()==source['files'][rel]['sha256'];count+=1
   elif rel in front['outputs']:assert hashlib.sha256(z.read(n)).hexdigest()==front['outputs'][rel]['sha256'];generated+=1
   else:raise AssertionError('Unexpected verification source: '+rel)
  assert count==2890 and generated==123
checksums={line.split('  ',1)[1]:line.split('  ',1)[0] for line in (O/'SHA256SUMS').read_text().splitlines()}
assert set(checksums)==set(expected)-{'SHA256SUMS'}
for n,h in checksums.items():assert expected[n]['sha256']==h
for p in O.glob('*.zip.sha256'):
 digest,name=p.read_text().strip().split('  ',1);assert expected[name]['sha256']==digest and p.name==name+'.sha256'
latest=fetch_json('https://api.github.com/repos/'+repo+'/releases/latest?final='+str(time.time_ns()));assert latest['id']==release['id'] and {a['name']:a['digest'] for a in latest['assets']}=={a['name']:a['digest'] for a in release['assets']}
result={'passed':True,'release_id':release['id'],'tag':'v2.7.0','title':release['name'],'latest':True,'prerelease':False,'source_commit':commit,'raw_git_source_files':len(gitrows),'assets':results,'no_authenticated_downloads':True,'full_crc_sha_manifests_and_source':True}
(R/'PUBLIC-VERIFIED.json').write_text(json.dumps(result,indent=2));print('PUBLIC_VERIFICATION_COMPLETE',len(results),flush=True)
