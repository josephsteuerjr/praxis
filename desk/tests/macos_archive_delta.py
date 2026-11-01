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
def versioned(base,destination,prefix):
    """Rename only the top folder; retain every compressed payload byte/mode."""
    prefix=str(prefix).encode('ascii')
    assert prefix and b'/' not in prefix and b'\\' not in prefix and prefix not in (b'.',b'..')
    positions={}
    with zipfile.ZipFile(base) as z,Path(base).open('rb') as source,Path(destination).open('xb') as out:
        rows=sorted(z.infolist(),key=lambda row:row.header_offset)
        assert len(rows)<65535 and rows[0].header_offset==0
        for number,row in enumerate(rows):
            assert row.filename.startswith('Helene/') and '..' not in Path(row.filename).parts
            source.seek(row.header_offset);header=bytearray(source.read(30));assert header[:4]==b'PK\x03\x04'
            namesize,extrasize=struct.unpack_from('<HH',header,26);assert extrasize==0
            name=source.read(namesize);assert name.startswith(b'Helene/')
            renamed=prefix+b'/'+name[len(b'Helene/'):];assert len(renamed)<65536
            positions[row.filename]=out.tell();struct.pack_into('<H',header,26,len(renamed))
            out.write(header);out.write(renamed)
            end=rows[number+1].header_offset if number+1<len(rows) else z.start_dir
            remaining=end-source.tell()
            while remaining:
                block=source.read(min(1024*1024,remaining));assert block
                out.write(block);remaining-=len(block)
        central_start=out.tell();source.seek(z.start_dir)
        for row in z.infolist():
            header=bytearray(source.read(46));assert header[:4]==b'PK\x01\x02'
            namesize,extrasize,commentsize=struct.unpack_from('<HHH',header,28)
            assert extrasize==0 and struct.unpack_from('<I',header,42)[0]==row.header_offset
            name=source.read(namesize);extra=source.read(extrasize);comment=source.read(commentsize)
            renamed=prefix+b'/'+name[len(b'Helene/'):]
            struct.pack_into('<H',header,28,len(renamed));struct.pack_into('<I',header,42,positions[row.filename])
            out.write(header);out.write(renamed);out.write(extra);out.write(comment)
        central_size=out.tell()-central_start
        out.write(struct.pack('<4s4H2IH',b'PK\x05\x06',0,0,len(rows),len(rows),central_size,central_start,len(z.comment)))
        out.write(z.comment)
    with zipfile.ZipFile(base) as before,zipfile.ZipFile(destination) as after:
        assert after.testzip() is None and len(before.infolist())==len(after.infolist())
        for row in before.infolist():
            other=after.getinfo(prefix.decode()+'/'+row.filename[len('Helene/'):])
            assert (other.CRC,other.file_size,other.compress_size,other.external_attr,other.flag_bits)==(row.CRC,row.file_size,row.compress_size,row.external_attr,row.flag_bits)
    return {'format':'helene-versioned-manual-archive.v1','canonical_sha256':digest(base),'sha256':digest(destination),
        'size':Path(destination).stat().st_size,'folder':prefix.decode(),'members':len(rows),
        'compressed_payload_bytes_and_unix_modes_unchanged':True}
if __name__=='__main__':
    result={'create':create,'restore':restore,'versioned':versioned}[sys.argv[1]](*map(Path,sys.argv[2:]))
    print(json.dumps({k:v for k,v in result.items() if k!='segments'}))
