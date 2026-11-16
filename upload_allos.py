
from pathlib import Path
import hashlib,json,os,subprocess,zipfile
repo=os.environ['GH_REPO'];spec=json.loads(Path('release.json').read_bytes());expected=json.loads(Path('expected-artifacts.json').read_bytes())
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(*a):return subprocess.check_output(list(map(str,a)))
r=json.loads(run('gh','api','repos/'+repo+'/releases/'+str(spec['id'])));assert r['draft'] and r['tag_name']==spec['tag']
present={a['name']:a for a in r['assets']}
def upload(p):
    e=expected[p.name];assert p.stat().st_size==e['size'] and sha(p)==e['sha256'],p.name
    with zipfile.ZipFile(p) as z:assert z.testzip() is None
    if p.name in present:assert present[p.name]['digest']=='sha256:'+e['sha256'];return
    run('gh','release','upload',spec['tag'],str(p));print('uploaded',p.name,flush=True)
for folder in sorted(Path('exact-upload').iterdir()):
    rec=json.loads((folder/'RECIPE.json').read_bytes())
    run('gh','release','download',rec['donor_tag'],'--pattern',rec['donor'],'--dir',str(folder))
    donor=folder/rec['donor'];assert sha(donor)==rec['donor_sha256']
    delta=folder/'delta.bin'
    with delta.open('xb') as out:
        for row in rec['chunks']:
            p=folder/row['name'];assert p.stat().st_size==row['size'] and sha(p)==row['sha256'];out.write(p.read_bytes())
    assert sha(delta)==rec['delta_sha256']
    final=Path('out')/rec['archive'];final.parent.mkdir(exist_ok=True)
    with donor.open('rb') as df,delta.open('rb') as cf,final.open('xb') as out:
        for row in rec['segments']:
            s={'donor':df,'delta':cf}[row['source']];s.seek(row['offset']);d=s.read(row['length']);assert len(d)==row['length'];out.write(d)
    upload(final)
for p in sorted(Path('native').rglob('*.zip')):
    if p.name in expected:upload(p)
for p in sorted(Path('native').rglob('native-*.tar.gz')):
    import tarfile
    with tarfile.open(p) as z:
        for m in z:
            if m.isfile() and m.name.startswith('evidence/package/Helene-2.8.6-macos-') and m.name.endswith('.zip'):
                if Path('exact-upload/macos').exists() and m.name.endswith('-macos-arm64.zip'): continue
                out=Path('out')/Path(m.name).name;out.write_bytes(z.extractfile(m).read());upload(out)
r=json.loads(run('gh','api','repos/'+repo+'/releases/'+str(spec['id'])));assert r['draft']
names={a['name'] for a in r['assets']}
big={n for n in expected if n.endswith('.zip') and n.startswith('Helene-2.8.6') and n!='Helene-2.8.6-source.zip'}
assert big<=names,big-names
print(json.dumps({'passed':True,'uploaded_archives':sorted(big),'draft_preserved':True}))
