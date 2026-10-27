"""Transport exact qualified ZIP bytes using public base ranges and small literals."""
from pathlib import Path
import hashlib,json,subprocess,urllib.request,zipfile
HERE=Path(__file__).resolve().parent
M=json.loads((HERE/'manifest.json').read_text())
REPO='josephsteuerjr/praxis';RID=426060488
assert M['repo']==REPO and M['release_id']==RID and M['tag']=='v2.2.10'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def api():return json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/releases/{RID}']))
def current():
 r=api();assert r['draft'] and r['tag_name']==M['tag'];return r
current()
with zipfile.ZipFile(HERE/'literals.zip') as literals:
 for row in M['assets']:
  name=row['name'];assert name==Path(name).name and row['platform'] in ('windows','macos','debian','fedora')
  present=[a for a in current()['assets'] if a['name']==name]
  if present:
   assert len(present)==1 and present[0]['state']=='uploaded' and present[0]['size']==row['size'] and present[0]['digest']=='sha256:'+row['sha256']
   print('ALREADY_CORRECT',name,flush=True);continue
  base=Path('base-'+row['platform']+'.zip');output=Path(name)
  assert row['base_url'].startswith('https://github.com/'+REPO+'/releases/download/v2.2.4/')
  with urllib.request.urlopen(row['base_url'],timeout=120) as source,base.open('xb') as dest:
   while block:=source.read(4*1024*1024):dest.write(block)
  assert base.stat().st_size==row['base_size'] and sha(base)==row['base_sha256']
  patch=literals.read(row['platform']+'.bin');assert hashlib.sha256(patch).hexdigest()==row['literal_sha256']
  with base.open('rb') as source,output.open('xb') as dest:
   for kind,offset,length in row['segments']:
    if kind=='literal':dest.write(patch[offset:offset+length])
    else:
     assert kind=='base' and offset>=0 and offset+length<=row['base_size'];source.seek(offset)
     while length:
      block=source.read(min(length,4*1024*1024));assert block;dest.write(block);length-=len(block)
  assert output.stat().st_size==row['size'] and sha(output)==row['sha256']
  print('EXACT_QUALIFIED_BYTES_RECONSTRUCTED',name,row['sha256'],flush=True)
  present=[a for a in current()['assets'] if a['name']==name]
  assert not present,'Another uploader owns this asset; do not replace it'
  sent=subprocess.run(['gh','release','upload',M['tag'],str(output),'--repo',REPO],capture_output=True)
  if sent.returncode:print('UPLOAD_RETURNED_NONZERO_RECONCILING',name,sent.returncode,flush=True)
  a=next(a for a in current()['assets'] if a['name']==name)
  assert a['state']=='uploaded' and a['size']==row['size'] and a['digest']=='sha256:'+row['sha256']
  print('CLOUD_UPLOADED_VERIFIED',name,flush=True)
  # Only exact temporary files created by this runner are removed.
  base.unlink();output.unlink()
print('FOUR_ASSETS_DELIVERED_NO_PROMOTION',flush=True)
