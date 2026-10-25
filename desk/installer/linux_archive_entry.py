"""Visible shell entry and a standard desktop launcher for extracted archives."""
from pathlib import Path

LAUNCHER='''#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -d "$here/runtime/system-libs" ]; then
    export LD_LIBRARY_PATH="$here/runtime/system-libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
exec "$here/runtime/bin/python3" -s "$here/app/localharness/desktop_entry.py" --root "$here" "$@"
'''

def write(root):
    root=Path(root)
    for name in ('helene','Start Helene.sh'):
        p=root/name;p.write_text(LAUNCHER,encoding='utf-8');p.chmod(0o755)
    from localharness.desktop_entry import quote
    shell='entry=$1; here=$(CDPATH= cd -- "$(dirname -- "$entry")" && pwd); exec "$here/Start Helene.sh"'
    desktop='[Desktop Entry]\nType=Application\nName=Hélène\nComment=Open Hélène\nExec='+quote('/bin/sh')+' -c '+quote(shell)+' helene %k\nIcon=applications-utilities\nTerminal=false\nCategories=Utility;\n'
    p=root/'Helene.desktop';p.write_text(desktop,encoding='utf-8');p.chmod(0o755)
    (root/'START-HERE.md').write_text('''# Hélène

Extract the whole folder to a permanent, writable location. Open **Start Helene.sh** using your file manager's **Run as a program** action. The window opens without a terminal. Your desktop may ask you to trust the launcher once; this is an operating-system rule. Hélène adds itself to the applications menu after the first launch.

If an installed Hélène package and profile are found, the window offers to move the profile, memory, and authored code into this folder. The old package is removed only after the new window and profile are confirmed. An administrator prompt may be needed for package removal. Cancelling that prompt keeps the old package and the working new application; removal can be retried on the next launch.

Keep the whole folder together. Updates run from Settings → Program and preserve your profile and code. A failed update restores the previous working program. Do not extract a new download over a running folder.

For diagnostics, run `sh ./Start\\ Helene.sh --diagnostic`. Launch logs are in `~/.local/state/helene/logs/` (or XDG_STATE_HOME). No system Python or pip is needed.
''',encoding='utf-8')
