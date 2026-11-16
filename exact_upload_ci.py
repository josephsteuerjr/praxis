"""Runner-only: assemble and upload exact Windows assets to an existing draft."""
from pathlib import Path
import hashlib,json,os,subprocess,zipfile
root=Path(__file__).resolve().parent;repo=os.environ['GH_REPO']
def sha(p):return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
def run(*a):return subprocess.check_output(list(map(str,a)))
spec=json.loads((root/'release.json').read_bytes());expected=json.loads((root/'expected-assets.json').read_bytes())
r=json.loads(run('gh','api','repos/'+repo+'/releases/'+str(spec['id'])))
assert r['draft'] and r['prerelease'] and r['tag_name']=='v2.8.3'
assert json.loads(run('gh','api','repos/'+repo+'/releases/latest'))['id']==443705935
folder=root/'exact-upload';recipe=json.loads((folder/'RECIPE.json').read_bytes())
run('gh','release','download',recipe['donor_tag'],'--pattern',recipe['donor'],'--dir',folder)
donor=folder/recipe['donor'];assert sha(donor)==recipe['donor_sha256']
delta=folder/'delta.bin'
with delta.open('xb') as out:
    for row in recipe['chunks']:
        p=folder/row['name'];assert p.stat().st_size==row['size'] and sha(p)==row['sha256'];out.write(p.read_bytes())
assert delta.stat().st_size==recipe['delta_size'] and sha(delta)==recipe['delta_sha256']
assets=root/'assets';archive=assets/recipe['archive']
with donor.open('rb') as before,delta.open('rb') as changes,archive.open('xb') as out:
    for row in recipe['segments']:
        stream=before if row['source']=='donor' else changes;stream.seek(row['offset']);raw=stream.read(row['length']);assert len(raw)==row['length'];out.write(raw)
assert archive.stat().st_size==recipe['size'] and sha(archive)==recipe['sha256']
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
assert {p.name for p in assets.iterdir()}==set(expected)
for name,row in expected.items():
    p=assets/name;assert p.stat().st_size==row['size'] and sha(p)==row['sha256']
    run('gh','release','upload','v2.8.3',p)
r=json.loads(run('gh','api','repos/'+repo+'/releases/'+str(spec['id'])))
assert r['draft'] and r['prerelease'] and len(r['assets'])==len(expected)
for asset in r['assets']:
    row=expected[asset['name']];assert asset['size']==row['size'] and asset['digest']=='sha256:'+row['sha256'] and asset['state']=='uploaded'
assert json.loads(run('gh','api','repos/'+repo+'/releases/latest'))['id']==443705935
print(json.dumps({'passed':True,'release':spec['id'],'assets':len(expected),'exact_reconstruction':True,'full_crc':True,'draft_preserved':True,'latest_preserved':True}))
