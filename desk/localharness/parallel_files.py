"""Bounded parallel file work with complete validation and serialized progress."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import os
import shutil
import threading
import time
import uuid
import zipfile

WORKERS=min(12,os.cpu_count() or 1)


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def copytree(source,destination,*,symlinks=True,ignore=None,copy_function=shutil.copy2,workers=WORKERS):
    """Create directories once, copy independent leaves, restore directory modes."""
    source,destination=Path(source),Path(destination)
    futures=[];directories=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def walk(src,dst):
            dst.mkdir(parents=True);directories.append((src,dst))
            paths=list(src.iterdir());ignored=set(ignore(str(src),[p.name for p in paths])) if ignore else set()
            for path in paths:
                if path.name in ignored:continue
                target=dst/path.name
                if symlinks and path.is_symlink():
                    target.symlink_to(os.readlink(path),target_is_directory=path.is_dir())
                    shutil.copystat(path,target,follow_symlinks=False)
                elif path.is_dir():walk(path,target)
                elif workers>1 and path.stat().st_size>=256*1024:
                    futures.append(pool.submit(copy_function,path,target))
                else:copy_function(path,target)
        walk(source,destination)
        for future in futures:future.result()
    for src,dst in reversed(directories):shutil.copystat(src,dst)
    return destination


def verify_pairs(pairs, progress=None, workers=WORKERS):
    pairs=list(pairs)
    def check(pair):
        source,target=pair
        if not target.is_file() or source.stat().st_size!=target.stat().st_size or digest(source)!=digest(target):
            raise IOError('Preserved profile bytes differ: '+str(source))
        return source.stat().st_size
    done=0;total=sum(p.stat().st_size for p,_ in pairs)
    def record(values):
        nonlocal done
        for size in values:
            done+=size
            if progress:progress({'stage':'verifying','bytes':done,'total_bytes':total})
    # Thousands of tiny files mostly cost metadata/GIL/seek work. Keep those
    # sequential while independent large hashes use the available CPU workers.
    if workers==1:return record(map(check,pairs))
    large=[];small=[]
    for pair in pairs:(large if pair[0].stat().st_size>=256*1024 else small).append(pair)
    with ThreadPoolExecutor(max_workers=max(1,workers-1)) as pool:
        pending=[pool.submit(check,pair) for pair in large]
        record(map(check,small));record(future.result() for future in pending)


def extract(archive,destination,progress=None,prefix='Helene/',workers=WORKERS):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(archive) as reader:entries=reader.infolist()
    seen={}
    # Admission is complete before any output. Reject aliases/duplicates and
    # file-directory collisions before workers can race on the same pathname.
    for entry in entries:
        relative=Path(entry.filename);out=(destination/relative).resolve()
        key=os.path.normcase(str(out))
        if (not out.is_relative_to(destination) or '\\' in entry.filename or ':' in entry.filename
                or '..' in relative.parts or relative.is_absolute()
                or not (entry.filename.startswith(prefix) or entry.filename.rstrip('/')==prefix.rstrip('/'))
                or (entry.external_attr>>16)&0o170000==0o120000 or key in seen):
            raise ValueError('Invalid or duplicate archive path (expected Helene/ root)')
        seen[key]=entry.is_dir()
    for name in seen:
        if any(seen.get(os.path.normcase(str(parent))) is False for parent in Path(name).parents):
            raise ValueError('Archive file/directory collision')
    total=sum(e.file_size for e in entries);total_files=sum(not e.is_dir() for e in entries);done=0;files=0;last=0.0
    lock=threading.Lock();local=threading.local();readers=[];verified={}
    def init():
        local.reader=zipfile.ZipFile(archive)
        with lock:readers.append(local.reader)
    def report(size,name,finished=False):
        nonlocal done,files,last
        with lock:
            done+=size;files+=int(finished)
            now=time.monotonic()
            if progress and (now-last>=.1 or done==total):
                progress({'stage':'unpacking','asset':Path(archive).name,'bytes':done,'total_bytes':total,
                          'files':files,'total_files':total_files,'file':name})
                last=now
    def unpack(entry):
        if entry.is_dir():return
        target=destination/entry.filename;target.parent.mkdir(parents=True,exist_ok=True)
        if not target.resolve().is_relative_to(destination):raise ValueError('Archive output escaped destination')
        temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.part')
        try:
            # Each worker has an independent reader, avoiding ZipFile's shared
            # seek lock. Reading to EOF checks CRC for every single member.
            digest=hashlib.sha256()
            with local.reader.open(entry) as source,temporary.open('xb') as output:
                while chunk:=source.read(1024*1024):
                    digest.update(chunk);output.write(chunk);report(len(chunk),entry.filename)
            if os.name!='nt':temporary.chmod((entry.external_attr>>16)&0o777 or 0o644)
            temporary.replace(target);report(0,entry.filename,True)
            from .update_io import stamp, count
            count(progress, 'extracted', entry.file_size)
            # Later archive overlays replace this receipt; later config/code
            # writes invalidate it through the exact file change identity.
            with lock:verified[str(target)]={'sha256':digest.hexdigest(),'stat':stamp(target)}
        finally:temporary.unlink(missing_ok=True)
    try:
        if workers==1:
            init()
            for entry in entries:unpack(entry)
        else:
            # A queue item per tiny Python module creates more overhead than
            # decompression work. One reader streams small entries in archive
            # order while other readers inflate the independent large members.
            init()
            with ThreadPoolExecutor(max_workers=max(1,workers-1),initializer=init) as pool:
                pending=[pool.submit(unpack,e) for e in entries if e.file_size>=256*1024]
                for entry in entries:
                    if entry.file_size<256*1024:unpack(entry)
                for future in pending:future.result()
    finally:
        for reader in readers:reader.close()
    return verified
