"""Anonymous public bytes and immutable refs; no credentials or release writes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib, json, urllib.request, zipfile, subprocess

T = Path(__file__).resolve().parent
spec = json.loads((T / 'public-spec.json').read_bytes())
expected = json.loads((T / 'expected-assets.json').read_bytes())
frozen = json.loads((T / 'SOURCE-FROZEN.json').read_bytes())
base = 'https://api.github.com/repos/josephsteuerjr/praxis/'

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent':'Helene-2.8.6-anonymous-verification', 'Accept':'application/vnd.github+json'}), timeout=180)

def api(path):
    # Shared Actions egress IPs can exhaust the anonymous metadata API quota.
    # Metadata uses the read-only job token; every asset still uses get() above,
    # which sends NO Authorization header and downloads the public browser URL.
    return json.loads(subprocess.check_output(['gh','api','repos/josephsteuerjr/praxis/'+path]))

release = api('releases/tags/v2.8.6')
assert release['id'] == spec['release_id'] and not release['draft'] and not release['prerelease']
assert api('releases/latest')['id'] == release['id']
assert len(release['assets']) == len(expected) and len(expected) in (20,22)
assert {a['name'] for a in release['assets']} == set(expected)
out = T / 'downloads'; out.mkdir()

def one(asset):
    name = asset['name']; row = expected[name]; path = out / name
    assert asset['size'] == row['size'] and asset['digest'] == 'sha256:' + row['sha256']
    digest = hashlib.sha256()
    with get(asset['browser_download_url']) as stream, path.open('xb') as dest:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk); dest.write(chunk)
    assert path.stat().st_size == row['size'] and digest.hexdigest() == row['sha256'], name
    result = {'name':name, **row, 'anonymous':True}
    if name.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            names = z.namelist(); assert len(names) == len(set(names))
            if name == 'Helene-2.8.6-source.zip':
                prefix = 'Helene-2.8.6-source/'
                assert set(names) == {prefix + n for n in frozen['files']}
                for n, entry in frozen['files'].items():
                    assert hashlib.sha256(z.read(prefix+n)).hexdigest() == entry['sha256']
                    assert z.getinfo(prefix+n).external_attr >> 16 == int(entry['mode'],8)
                result['raw_git_files_and_modes'] = len(names)
            elif name.startswith('Helene-2.8.6') and 'server-linux-arm64' not in name:
                meta = json.loads(z.read('Helene/python-app.json'))
                assert meta['version'] == '2.8.6' and meta['local_candidate'] is False
                inventory = json.loads(z.read('Helene/python-package-files.json'))['files']
                hashes = {}
                for entry in z.infolist():
                    if entry.is_dir(): continue
                    with z.open(entry) as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    relative = entry.filename.removeprefix('Helene/')
                    if relative != 'python-package-files.json': hashes[relative] = digest
                assert hashes == inventory, name
                result.update(package_inventory_members=len(inventory), target=meta.get('target','windows-x86_64'))
            else:
                assert z.testzip() is None
            result['full_crc'] = True
    path.unlink()
    print(json.dumps(result), flush=True)
    return result

with ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(one, release['assets']))
refs = {}
for ref, sha in spec['refs_before'].items():
    value = api('git/ref/' + ref); assert value['object']['sha'] == sha, ref
    refs[ref] = sha
tag = api('git/ref/tags/v2.8.6')
assert tag['object']['type'] == 'commit' and tag['object']['sha'] == frozen['commit']
assert api('releases/latest')['id'] == release['id']
value = {'passed':True, 'release_id':release['id'], 'url':release['html_url'], 'latest':True,
         'metadata_authenticated':True,'asset_downloads_authenticated':False,
         'prerelease':False, 'source_commit':frozen['commit'], 'all_assets_anonymous':True,
         'assets':results, 'main_and_earlier_tags_preserved':refs}
(T / 'PUBLIC-VERIFIED.json').write_text(json.dumps(value, indent=2), encoding='utf8')
print('ALL',len(expected),'PUBLIC ASSETS VERIFIED', flush=True)
