from pathlib import Path
import hashlib,json,subprocess,sys,tarfile,zipfile
target=sys.argv[1];assert target in ('macos','macos-intel');arch='x86_64' if target.endswith('intel') else 'arm64'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
O=Path(target);O.mkdir();E=O/'export';E.mkdir()
subprocess.run(['gh','release','download','v2.6.1','--pattern','r2-native-'+target+'.tar.gz','--dir',str(O)],check=True)
subprocess.run(['gh','release','download','v2.6.0','--pattern','Helene-2.6.0-macos-'+arch+'.zip','--dir',str(O)],check=True)
with tarfile.open(O/('r2-native-'+target+'.tar.gz')) as z:
 for m in z:
  if not m.isfile() or not m.name.startswith('evidence/'):continue
  rel=m.name.removeprefix('evidence/');p=Path(rel);assert '..' not in p.parts and not p.is_absolute()
  if rel=='COMPLETE.json' or rel.startswith('native/native-gui/') or rel in ('native/native-final.json','native/native-core.log','native/core-work/observation.json'):
   d=E/rel;d.parent.mkdir(parents=True,exist_ok=True);d.write_bytes(z.extractfile(m).read())
  elif rel.startswith('package/Helene-') and rel.endswith('.zip'):
   new=O/p.name;new.write_bytes(z.extractfile(m).read())
proof=json.loads((E/'COMPLETE.json').read_bytes());assert proof['passed'] and proof['source_commit']=='3b57d7eadd61c454768fe940914915b961de6f9c' and sha(new)==proof['sha256']
old=O/('Helene-2.6.0-macos-'+arch+'.zip');delta=E/'records.bin';recipe=[]
with zipfile.ZipFile(new) as nz,zipfile.ZipFile(old) as oz,new.open('rb') as nf,old.open('rb') as of,delta.open('xb') as df:
 def spans(z):
  items=sorted(z.infolist(),key=lambda i:i.header_offset)
  return {item.filename:(item.header_offset,(items[k+1].header_offset if k+1<len(items) else z.start_dir)-item.header_offset) for k,item in enumerate(items)}
 before=spans(oz)
 for name,(start,length) in sorted(spans(nz).items(),key=lambda x:x[1][0]):
  nf.seek(start);raw=nf.read(length);origin=before.get(name);equal=False
  if origin and origin[1]==length:of.seek(origin[0]);equal=of.read(length)==raw
  if equal:recipe.append({'source':'donor','offset':origin[0],'length':length})
  else:offset=df.tell();df.write(raw);recipe.append({'source':'delta','offset':offset,'length':length})
 nf.seek(nz.start_dir);raw=nf.read();offset=df.tell();df.write(raw);recipe.append({'source':'delta','offset':offset,'length':len(raw)})
(E/'RECIPE.json').write_text(json.dumps({'archive':new.name,'sha256':sha(new),'size':new.stat().st_size,'donor_sha256':sha(old),'delta_sha256':sha(delta),'segments':recipe},separators=(',',':')))
archive=Path('delta-'+target+'.tar.gz')
with tarfile.open(archive,'x:gz') as z:
 for p in sorted(E.rglob('*')):
  if p.is_file():z.add(p,p.relative_to(E).as_posix(),recursive=False)
subprocess.run(['gh','release','upload','v2.6.1',str(archive)],check=True)
print('Delta export',target,archive.stat().st_size,sha(archive))
