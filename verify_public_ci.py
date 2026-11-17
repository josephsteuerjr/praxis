"""Anonymous public bytes and immutable refs; no credentials or release writes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib, json, urllib.parse, urllib.request, zipfile, subprocess, time

T = Path(__file__).resolve().parent
spec = json.loads((T / 'public-spec.json').read_bytes())
expected = json.loads((T / 'expected-assets.json').read_bytes())
frozen = json.loads((T / 'SOURCE-FROZEN.json').read_bytes())
base = 'https://api.github.com/repos/josephsteuerjr/praxis/'

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent':'Helene-2.8.8-anonymous-verification', 'Accept':'application/octet-stream', 'Cache-Control':'no-cache'}), timeout=180)

def api(path):
    # Shared Actions egress IPs can exhaust the anonymous metadata API quota.
    # Metadata uses the read-only job token; every asset still uses get() above,
    # which sends NO Authorization header and downloads the public browser URL.
    return json.loads(subprocess.check_output(['gh','api','repos/josephsteuerjr/praxis/'+path]))

release = api('releases/tags/v2.8.8')
assert release['id'] == spec['release_id'] and not release['draft'] and not release['prerelease']
assert api('releases/latest')['id'] == release['id']
assert len(release['assets']) == len(expected) and len(expected) in (20,22)
assert {a['name'] for a in release['assets']} == set(expected)
out = T / 'downloads'; out.mkdir()

def one(asset):
    name = asset['name']; row = expected[name]; path = out / name
    assert asset['size'] == row['size'] and asset['digest'] == 'sha256:' + row['sha256']
    attempts = []
    for attempt in range(3):
        digest = hashlib.sha256()
        # A complete response is required, including when a CDN closes early
        # without urllib raising IncompleteRead on read(size).
        url = asset['browser_download_url'] + '?helene_verify=' + str(time.time_ns())
        with get(url) as stream, path.open('wb') as dest:
            status = stream.status
            length = stream.headers.get('Content-Length')
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk); dest.write(chunk)
        observation = {'attempt':attempt+1,'status':status,'content_length':length,
                       'actual_bytes':path.stat().st_size,'actual_sha256':digest.hexdigest()}
        attempts.append(observation)
        if observation['actual_bytes'] == row['size'] and observation['actual_sha256'] == row['sha256']:break
        print(json.dumps({'incomplete_or_changed_download':name,**observation,'expected':row}),flush=True)
    else:raise AssertionError({'asset':name,'expected':row,'attempts':attempts})
    result = {'name':name, **row, 'anonymous':True,'download_attempts':attempts}
    if name.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            names = z.namelist(); assert len(names) == len(set(names))
            if name == 'Helene-2.8.8-source.zip':
                prefix = 'Helene-2.8.8-source/'
                assert set(names) == {prefix + n for n in frozen['files']}
                for n, entry in frozen['files'].items():
                    assert hashlib.sha256(z.read(prefix+n)).hexdigest() == entry['sha256']
                    assert z.getinfo(prefix+n).external_attr >> 16 == int(entry['mode'],8)
                result['raw_git_files_and_modes'] = len(names)
            elif name.startswith('Helene-2.8.8') and 'server-linux-arm64' not in name:
                meta = json.loads(z.read('Helene/python-app.json'))
                assert meta['version'] == '2.8.8' and meta['local_candidate'] is False
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
    if ref.endswith('^{}'): continue
    value = api('git/ref/' + ref); assert value['object']['sha'] == sha, ref
    refs[ref] = sha
protected = json.loads((T / 'PROTECTED-RELEASES-BEFORE.json').read_bytes())
for oldtag, before in protected.items():
    old = api('releases/tags/' + oldtag)
    assert old['id'] == before['id']
    assert {a['name']:{k:a[k] for k in ('id','size','digest','state')} for a in old['assets']} == before['assets'], oldtag
tag = api('git/ref/tags/v2.8.8')
assert tag['object']['type'] == 'commit' and tag['object']['sha'] == frozen['commit']
tree = api('git/trees/' + frozen['tree'] + '?recursive=1')
assert not tree['truncated']
blobs = {r['path']:r for r in tree['tree'] if r['type'] == 'blob'}
assert set(blobs) == set(frozen['files'])
git_zip = out / 'github-tag-source.zip'
attribute_conversions = []
with get('https://codeload.github.com/josephsteuerjr/praxis/zip/refs/tags/v2.8.8') as stream, git_zip.open('wb') as dest:
    while chunk := stream.read(4 * 1024 * 1024): dest.write(chunk)
with zipfile.ZipFile(git_zip) as z:
    files = [n for n in z.namelist() if not n.endswith('/')]
    prefix = files[0].split('/',1)[0] + '/'
    assert {n.removeprefix(prefix) for n in files} == set(frozen['files'])
    for name, row in frozen['files'].items():
        raw = z.read(prefix + name)
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            # git archive applies desk/.gitattributes *.ps1 eol=crlf.
            # Fetch the immutable raw Git bytes anonymously, without accepting
            # a normalized approximation of an unexplained content difference.
            archive_raw = raw
            with get('https://raw.githubusercontent.com/josephsteuerjr/praxis/v2.8.8/' + urllib.parse.quote(name,safe='/')) as stream:
                raw = stream.read()
            assert name.endswith('.ps1') and b'\r\n' in archive_raw and b'\r\n' not in raw, name
            assert archive_raw.replace(b'\r\n',b'\n') == raw, name
            attribute_conversions.append({'path':name,'archive_bytes':len(archive_raw),'raw_git_bytes':len(raw),
                                          'archive_sha256':hashlib.sha256(archive_raw).hexdigest(),'raw_git_sha256':hashlib.sha256(raw).hexdigest()})
        assert hashlib.sha256(raw).hexdigest() == row['sha256'], name
        assert blobs[name]['mode'] == row['mode'], name
        assert blobs[name]['sha'] == hashlib.sha1(('blob ' + str(len(raw)) + '\0').encode() + raw).hexdigest(), name
git_zip.unlink()
assert api('releases/latest')['id'] == release['id']
value = {'passed':True, 'release_id':release['id'], 'url':release['html_url'], 'latest':True,
         'metadata_authenticated':True,'asset_downloads_authenticated':False,
         'prerelease':False, 'source_commit':frozen['commit'], 'all_assets_anonymous':True,
         'assets':results, 'main_and_earlier_tags_preserved':refs,
         'prior_release_and_shared_voice_assets_preserved':protected,
         'github_tag_anonymous_raw_blobs':len(blobs),'github_tag_modes_verified':True,
         'github_archive_attribute_conversions':attribute_conversions}
(T / 'PUBLIC-VERIFIED.json').write_text(json.dumps(value, indent=2), encoding='utf8')
print('ALL',len(expected),'PUBLIC ASSETS VERIFIED', flush=True)
