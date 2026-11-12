"""Root-side, namespace-bound transfer retirement. No data is removed.

Executed over the newly pinned SSH session, with no credentials in argv. The
same flock as install.sh fences an installer still alive after a network loss.
The durable stop fence prevents a delayed retry from starting after recovery.
"""
from pathlib import Path
import fcntl
import json
import os
import re
import subprocess
import sys


def command(args):
    return subprocess.check_output(['docker',*args],text=True,timeout=90)


def canonical(row):
    root=Path(row['deployment']['directory'])
    if root.resolve()!=root or root.is_symlink():raise ValueError('noncanonical target')
    return root


def claimed(row,root):
    """The installation was created by exactly this transfer and archive."""
    try:claim=json.loads((root/'.window-transfer.json').read_text())
    except FileNotFoundError:claim={}
    return claim.get('id')==row['id'] and claim.get('archive_sha')==row['archive_sha']


def owned(row,root):
    """Immutable IDs of this deployment's containers, after label/mount checks."""
    target=row['deployment'];result=[]
    for name,project in ((target['container']+'-updater',target['project']+'-updater'),(target['container'],target['project'])):
        present=command(['container','ls','-a','--filter','name=^/'+name+'$','--format','{{.ID}}'])
        if not present.strip():continue
        info=json.loads(command(['inspect',name]))[0]
        if (info.get('Config',{}).get('Labels') or {}).get('com.docker.compose.project')!=project:
            raise ValueError('foreign compose project')
        if not any(m.get('Source') in (str(root),str(root/'data')) for m in info.get('Mounts',[])):
            raise ValueError('foreign data mount')
        if not re.fullmatch('[a-f0-9]{64}',info.get('Id','')):raise ValueError('missing immutable identity')
        result.append(info['Id'])
    return result


def stopped(identities):
    for identity in identities:
        info=json.loads(command(['inspect',identity]))[0]
        if info['State']['Running'] is not False:return False
    return True


def fenced(row,root):
    try:value=json.loads((root/'.window-transfer-stopped').read_text())
    except FileNotFoundError:return False
    return value.get('id')==row['id'] and value.get('archive_sha')==row['archive_sha']


def recover(row):
    root=canonical(row)
    if not root.exists():
        if row['phase']!='archive':raise ValueError('missing installation ownership')
        return {'ok':True,'never_installed':True}
    fd=os.open(root/'.install.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not claimed(row,root):
            if row['phase']=='archive' and not (root/'helene.json').exists():
                # No install command can precede the durable installing phase.
                return {'ok':True,'never_installed':True}
            raise ValueError('installation ownership mismatch')
        owned_ids=owned(row,root)
        fence=root/'.window-transfer-stopped'
        if not fence.exists():
            with fence.open('x',encoding='utf-8') as out:
                json.dump({'id':row['id'],'archive_sha':row['archive_sha']},out);out.flush();os.fsync(out.fileno())
            parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(parent)
            finally:os.close(parent)
        for identity in owned_ids:
            command(['stop','--time','30',identity])
        if not stopped(owned_ids):raise ValueError('stop not confirmed')
        return {'ok':True,'stopped':owned_ids,'data_kept':True}
    finally:os.close(fd)


if __name__=='__main__':
    try:result=recover(json.loads(sys.argv[1]))
    except Exception:result={'ok':False,'message':'Установка ещё выполняется или принадлежность/остановка не подтверждена. Местный Stop сохранён; повтори после завершения установки.'}
    print(json.dumps(result))
