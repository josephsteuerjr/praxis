from pathlib import Path
import hashlib, json, os, subprocess, sys, zipfile

repo = os.environ['GH_REPO']
assert repo == 'josephsteuerjr/praxis'
spec = json.loads(Path('release.json').read_bytes())
folder = Path('parts') / sys.argv[1]
recipe = json.loads((folder/'RECIPE.json').read_bytes())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def state():
    row = json.loads(subprocess.check_output(['gh','api','repos/'+repo+'/releases/'+str(spec['id'])]))
    assert row['draft'] and row['tag_name']==spec['tag']
    assets = [a for a in row['assets'] if a['name']==recipe['archive']]
    if assets:
        assert len(assets)==1 and assets[0]['state']=='uploaded' and assets[0]['size']==recipe['size']
        assert assets[0]['digest']=='sha256:'+recipe['sha256']
    return bool(assets)


if state():
    print('Exact asset already present',recipe['archive']); sys.exit(0)
url = 'https://github.com/'+repo+'/releases/download/'+recipe['donor_tag']+'/'+recipe['donor']
donor = folder/recipe['donor']
for attempt in range(3):
    result = subprocess.run(['curl','--silent','--show-error','--fail','--location','--retry','3','--retry-all-errors',
                             '--max-time','360','--output',str(donor),url])
    if result.returncode==0 and sha(donor)==recipe['donor_sha256']:
        break
else:
    raise ValueError('Exact donor download did not complete')
delta = folder/'delta.bin'
with delta.open('xb') as out:
    for chunk in recipe['chunks']:
        part = folder/chunk['name']
        assert part.stat().st_size==chunk['size'] and sha(part)==chunk['sha256']
        with part.open('rb') as source:
            while raw:=source.read(4*1024*1024): out.write(raw)
assert sha(delta)==recipe['delta_sha256']
final = Path(recipe['archive'])
with donor.open('rb') as base, delta.open('rb') as changed, final.open('xb') as out:
    for row in recipe['segments']:
        source = base if row['source']=='donor' else changed
        source.seek(row['offset']); raw = source.read(row['length'])
        assert len(raw)==row['length']; out.write(raw)
assert final.stat().st_size==recipe['size'] and sha(final)==recipe['sha256']
with zipfile.ZipFile(final) as archive:
    assert archive.testzip() is None
subprocess.run(['gh','release','upload',spec['tag'],'--repo',repo,str(final)],check=True)
assert state()
print('Exact archive staged',final.name,recipe['sha256'],flush=True)
