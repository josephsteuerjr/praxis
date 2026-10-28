"""Hélène setup and recovery: create, connect, migrate, update or restore code.

Run with the application's bundled Python. The normal entry opens the same
wizard as first launch; --restore-upstream --confirmed is the emergency entry.
"""
import argparse
from pathlib import Path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--restore-upstream',action='store_true')
    parser.add_argument('--confirmed',action='store_true')
    parser.add_argument('--operation',default='')
    parser.add_argument('--old',default='',help='Previous installation to migrate')
    parser.add_argument('--target',default='',help='Permanent program directory')
    parser.add_argument('--source-mode',choices=('preserve','clean'),default='preserve')
    parser.add_argument('--force-extensions',action='store_true',help='Explicitly continue despite extension compatibility failures')
    parser.add_argument('--admission',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.restore_upstream:
        if args.old or args.target or args.admission or args.force_extensions:parser.error('Unexpected recovery arguments')
        if not args.confirmed:parser.error('Confirm restoring upstream code; existing data stays connected')
        from localharness.upstream_recovery import restore
        return restore(args.root,operation=args.operation)
    if args.old and args.target and Path(args.old).resolve()==Path(args.target).resolve():
        old=Path(args.old).resolve();root=args.root.resolve()
        if root==root.parent/'Helene' and root.parent.parent==old.parent and root.parent.name.startswith('.helene-update-'):
            from localharness.migration_job import legacy_update
            legacy_update(old,root,source_mode=args.source_mode,admission=args.admission,
                          force_extensions=args.force_extensions)
            return 0
    from python_migration_window import main as wizard
    arguments=['--root',str(args.root)]
    for flag,value in (('--old',args.old),('--target',args.target),('--admission',args.admission)):
        if value:arguments.extend([flag,str(value)])
    if args.source_mode!='preserve':arguments.extend(['--source-mode',args.source_mode])
    if args.force_extensions:arguments.append('--force-extensions')
    return wizard(arguments)


if __name__=='__main__':raise SystemExit(main())
