"""Transport only: reproduce exact ZIP bytes from a verified local base and delta."""
from pathlib import Path
import hashlib,json,struct,sys,zipfile
def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def data_offset(stream,row):
    stream.seek(row.header_offset);header=stream.read(30)
    assert header[:4]==b'PK\x03\x04'
    a,b=struct.unpack_from('<HH',header,26)
    return row.header_offset+30+a+b
def create(base,target,destination):
    literals=bytearray();segments=[];cursor=0;reused=0
    def literal(raw):
        if raw:segments.append({'literal':len(literals),'length':len(raw)});literals.extend(raw)
    with zipfile.ZipFile(base) as oldzip,zipfile.ZipFile(target) as newzip,Path(base).open('rb') as old,Path(target).open('rb') as new:
        for row in sorted(newzip.infolist(),key=lambda x:x.header_offset):
            start=data_offset(new,row);new.seek(cursor);literal(new.read(start-cursor))
            new.seek(start);raw=new.read(row.compress_size);previous=oldzip.NameToInfo.get(row.filename)
            same=False
            if previous and (previous.compress_type,previous.compress_size,previous.CRC)==(row.compress_type,row.compress_size,row.CRC):
                offset=data_offset(old,previous);old.seek(offset);same=old.read(previous.compress_size)==raw
            if same:
                segments.append({'base_offset':offset,'length':len(raw),'sha256':hashlib.sha256(raw).hexdigest()});reused+=len(raw)
            else:literal(raw)
            cursor=start+row.compress_size
        new.seek(cursor);literal(new.read())
    plan={'format':'helene-exact-zip-delta.v1','base_sha256':digest(base),'sha256':digest(target),
        'size':Path(target).stat().st_size,'segments':segments,'reused_bytes':reused}
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as z:
        z.writestr('plan.json',json.dumps(plan,separators=(',',':')));z.writestr('literal.bin',literals)
    return plan
def restore(base,patch,destination):
    with zipfile.ZipFile(patch) as z:
        assert z.testzip() is None and set(z.namelist())=={'plan.json','literal.bin'}
        plan=json.loads(z.read('plan.json'));literal=z.read('literal.bin')
    assert plan['format']=='helene-exact-zip-delta.v1' and digest(base)==plan['base_sha256']
    whole=hashlib.sha256();size=0
    with Path(base).open('rb') as previous,Path(destination).open('xb') as out:
        for row in plan['segments']:
            length=row['length'];assert 0<=length<=512*1024*1024
            if 'base_offset' in row:
                previous.seek(row['base_offset']);raw=previous.read(length)
                assert hashlib.sha256(raw).hexdigest()==row['sha256']
            else:raw=literal[row['literal']:row['literal']+length]
            assert len(raw)==length;out.write(raw);whole.update(raw);size+=length
    assert whole.hexdigest()==plan['sha256'] and size==plan['size']
    return {k:plan[k] for k in ('base_sha256','sha256','size','reused_bytes')}
if __name__=='__main__':
    result={'create':create,'restore':restore}[sys.argv[1]](*map(Path,sys.argv[2:]))
    print(json.dumps({k:v for k,v in result.items() if k!='segments'}))
