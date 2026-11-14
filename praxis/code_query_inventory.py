"""Audited inventory for semantic queries; used by Forge's source_files opt-in."""
import os
from pathlib import Path
import subprocess


def collect(root, *, limit, suffixes, budget, audit, skip_dirs, git_root):
    root=Path(root).resolve();out=[];seen=0;stopped=''
    def skip(path,reason):
        try:name=Path(path).relative_to(root).as_posix()
        except ValueError:name=str(path)
        audit.append({'path':name,'reason':reason})
    def admit(path):
        nonlocal seen,stopped
        seen+=1
        if budget.expired():stopped='deadline';return False
        if len(out)>=limit:stopped='file-cap';return False
        try:
            resolved=path.resolve()
            if not resolved.is_relative_to(root):skip(path,'symlink_outside_root');return True
            relative=path.relative_to(root)
            if 'code-query' in relative.parts and '.cache' in relative.parts:skip(path,'generated_query_cache');return True
            if not used_git and any(part in skip_dirs for part in relative.parts):skip(path,'excluded_directory');return True
            ancestor=path if path.is_dir() else path.parent
            while ancestor!=root:
                if (ancestor/'.git').exists():skip(ancestor,'nested_repository');return True
                ancestor=ancestor.parent
            if path.is_file():
                if suffixes is None or path.suffix.lower() in suffixes:out.append(path)
                else:skip(path,'unsupported_suffix')
            elif path.is_dir():skip(path,'directory')
            else:skip(path,'missing_or_unreadable')
        except (OSError,ValueError,RuntimeError):skip(path,'io_error')
        return True

    used_git=False
    if git_root is not None and not budget.expired():
        try:
            cmd=['git','-C',str(git_root),'ls-files','--cached','--others','--exclude-standard','-z']
            q=subprocess.run(cmd,capture_output=True,timeout=max(.01,min(30,budget.left())))
            if q.returncode==0:
                used_git=True
                names=sorted(set(os.fsdecode(x) for x in q.stdout.split(b'\0') if x))
                for name in names:
                    path=git_root/name
                    if not path.is_relative_to(root):continue
                    if not admit(path):break
                if not budget.expired():
                    q=subprocess.run(['git','-C',str(git_root),'ls-files','--others','--ignored','--exclude-standard','--directory','-z'],capture_output=True,timeout=max(.01,min(30,budget.left())))
                    if q.returncode==0:
                        for raw in q.stdout.split(b'\0'):
                            if raw:
                                path=git_root/os.fsdecode(raw).rstrip('/')
                                if path.is_relative_to(root):skip(path,'gitignored')
                    else:audit.append({'path':'.','reason':'gitignore_inventory_failed'})
        except (OSError,subprocess.TimeoutExpired):stopped='deadline' if budget.expired() else 'io-error'
    if not used_git and not stopped:
        def onerror(error):audit.append({'path':str(getattr(error,'filename','') or '.'),'reason':'io_error'})
        for current,dirs,names in os.walk(root,followlinks=False,onerror=onerror):
            base=Path(current);kept=[]
            for name in sorted(dirs):
                child=base/name
                if name in skip_dirs:skip(child,'excluded_directory')
                elif child.is_symlink():
                    try:skip(child,'symlink_outside_root' if not child.resolve().is_relative_to(root) else 'symlink_directory')
                    except (OSError,RuntimeError):skip(child,'io_error')
                elif name=='code-query' and '.cache' in child.relative_to(root).parts:skip(child,'generated_query_cache')
                elif (child/'.git').exists():skip(child,'nested_repository')
                else:kept.append(name)
            dirs[:]=kept
            for name in sorted(names):
                if not admit(base/name):break
            if stopped:break
    if budget.expired():stopped='deadline'
    # Deduplicate directory disclosures (Git may list several descendants).
    unique={(r['path'],r['reason']):r for r in audit};audit[:]=list(unique.values())
    budget.stage('audited source inventory',taken=len(out),seen=seen,exhausted=not stopped,stopped=stopped)
    return sorted(out,key=lambda p:p.relative_to(root).as_posix())
