"""Lossless delivery of already-qualified bytes; no product build or execution."""
from pathlib import Path
import hashlib,http.client,json,os,subprocess,time,urllib.error,urllib.request,zipfile
D=Path(__file__).resolve().parent;M=json.loads((D/'manifest.json').read_text());repo=M['repo'];release_id=M['release_id'];tag=M['tag'];tmp=Path(os.environ['RUNNER_TEMP'])/'delivery214';tmp.mkdir()
def digest(path):return hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
def api(route,method='GET',body=None):
    args=['gh','api','repos/'+repo+'/'+route,'--method',method]
    if body is not None:
        request=tmp/'request.json';request.write_text(json.dumps(body));args+=['--input',str(request)]
    return json.loads(subprocess.check_output(args))
def assets():return {row['name']:row for row in api('releases/'+str(release_id)+'?fresh='+str(__import__('time').time_ns()))['assets']}
def upload(name,path,row):
    existing=assets().get(name)
    if existing:
        assert existing['size']==row['size'] and existing['digest']=='sha256:'+row['sha256'],name
        print('RETAIN_EXACT_ASSET',name,flush=True);return
    assert path.stat().st_size==row['size'] and digest(path)==row['sha256'],name
    subprocess.run(['gh','release','upload',tag,str(path),'--repo',repo],check=True)
    current=assets()[name];assert current['size']==row['size'] and current['digest']=='sha256:'+row['sha256'],name
    print('UPLOADED_EXACT_ASSET',name,flush=True)
def download_asset(asset_id,path,want):
    with path.open('wb') as out:subprocess.run(['gh','api','-H','Accept: application/octet-stream','repos/'+repo+'/releases/assets/'+str(asset_id)],stdout=out,check=True)
    assert path.stat().st_size==want['size'] and digest(path)==want['sha256'],path.name
literal_archive=tmp/'literals.zip'
with literal_archive.open('xb') as output:
    for chunk in M['literal_chunks']:
        data=(D/chunk['name']).read_bytes();assert hashlib.sha256(data).hexdigest()==chunk['sha256'];output.write(data)
assert digest(literal_archive)==M['literals_sha256']
with zipfile.ZipFile(literal_archive) as literals:
    for row in M['programs']:
        wanted=M['assets'][row['name']]
        if row['name'] in assets():
            current=assets()[row['name']];assert current['digest']=='sha256:'+wanted['sha256'] and current['size']==wanted['size'];continue
        base=tmp/('base-'+row['platform']+'.zip');download_asset(row['base_asset_id'],base,{'size':row['base_size'],'sha256':row['base_sha256']})
        literal=literals.read(row['platform']+'.bin');assert hashlib.sha256(literal).hexdigest()==row['literal_sha256']
        target=tmp/row['name']
        with base.open('rb') as old,target.open('wb') as output:
            for kind,start,length in row['segments']:
                if kind=='literal':output.write(literal[start:start+length])
                else:
                    assert kind=='base';old.seek(start)
                    while length:
                        data=old.read(min(length,4*1024*1024));assert data;output.write(data);length-=len(data)
        assert digest(target)==row['sha256'] and target.stat().st_size==row['size']
        with zipfile.ZipFile(target) as z:assert z.testzip() is None
        upload(row['name'],target,wanted);target.unlink();base.unlink()
for name,row in M['assets'].items():
    if row['kind']=='program':continue
    if row['kind']=='optional':
        current=assets().get(name)
        if current:
            assert current['size']==row['size'] and current['digest']=='sha256:'+row['sha256'];continue
        path=tmp/name;download_asset(row['old_asset_id'],path,row);upload(name,path,row);path.unlink()
    else:upload(name,D/'files'/name,row)
actual=assets();assert set(actual)==set(M['assets'])
for name,want in M['assets'].items():assert actual[name]['size']==want['size'] and actual[name]['digest']=='sha256:'+want['sha256']
published=api('releases/'+str(release_id),'PATCH',{'draft':False,'prerelease':False,'make_latest':'true'})
assert not published['draft'] and published['tag_name']==tag
print('PUBLISHED',published['html_url'],flush=True)
# A separate anonymous HTTP read, with no gh/API authorization, verifies public bytes.
verified={}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for name,want in M['assets'].items():
    url='https://github.com/'+repo+'/releases/download/'+tag+'/'+name
    request=urllib.request.Request(url,headers={'User-Agent':'Helene-anonymous-byte-verifier'})
    h=hashlib.sha256();size=0;path=tmp/('public-'+name);stream=path.open('wb') if want['kind']=='program' else None
    for attempt in range(3):
        try:
            with opener.open(request,timeout=90) as response:
                while data:=response.read(4*1024*1024):h.update(data);size+=len(data);stream.write(data) if stream else None
            break
        except (urllib.error.URLError,TimeoutError,ConnectionError,http.client.IncompleteRead):
            if stream:stream.close()
            if attempt==2:raise
            print('RETRY_ANONYMOUS_TRANSPORT',name,flush=True);time.sleep(2*(attempt+1))
            h=hashlib.sha256();size=0;stream=path.open('wb') if want['kind']=='program' else None
    if stream:stream.close()
    assert size==want['size'] and h.hexdigest()==want['sha256'],name
    crc=None
    if stream:
        with zipfile.ZipFile(path) as z:assert z.testzip() is None
        crc=True;path.unlink()
    verified[name]={'size':size,'sha256':h.hexdigest(),'anonymous':True,'crc':crc}
    (D/'public-verification.json').write_text(json.dumps(verified,indent=2));print('ANONYMOUS_BYTES_PASS',name,flush=True)
assert set(verified)==set(M['assets'])
(D/'COMPLETE.json').write_text(json.dumps({'passed':True,'release_id':release_id,'tag':tag,'source_commit':M['source_commit'],'public_assets':verified},indent=2))
