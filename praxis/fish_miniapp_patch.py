"""Pure additive Fish assets for the currently serving phone UI.

Deployment separately verifies/backups exact installed bytes. Existing minified
entry content is preserved; its new alias lets the existing version watcher see
the newly installed module instead of silently retaining stale HTML.
"""
import hashlib
from pathlib import PurePosixPath
import re


def assets(index,base_js,fish_js,fish_css):
    if 'data-praxis-fish=' in index:raise ValueError('Fish extension already installed; reconcile before retry')
    matches=list(re.finditer(r'<script\b[^>]*\btype="module"[^>]*\bsrc="([^"]*assets/index-[A-Za-z0-9_-]+\.js)"[^>]*>',index))
    if len(matches)!=1:raise ValueError('expected one installed entrypoint')
    old=matches[0].group(1)
    if '..' in PurePosixPath(old).parts:raise ValueError('invalid entrypoint')
    fingerprint=hashlib.sha256(fish_js+b'\0'+fish_css).hexdigest()[:16]
    name=PurePosixPath(old).name;prefix=old[:-len(name)]
    alias=name[:-3]+'-fish-'+fingerprint+'.js'
    script='fish-voice-'+fingerprint+'.js';style='fish-voice-'+fingerprint+'.css'
    updated=index.replace('src="'+old+'"','src="'+prefix+alias+'"',1)
    updated=updated.replace('</head>','<link rel="stylesheet" href="'+prefix+style+'"></head>')
    updated=updated.replace('</body>','<script type="module" src="'+prefix+script+'" data-praxis-fish="v1"></script></body>')
    return updated,{alias:base_js,script:fish_js,style:fish_css}


def upgrade(index,base_js,fish_js,fish_css):
    """Replace one known extension, retaining the exact original application JS."""
    script=r'<script type="module" src="[^"]*assets/fish-voice-[a-f0-9]{16}\.js" data-praxis-fish="v1"></script>'
    style=r'<link rel="stylesheet" href="[^"]*assets/fish-voice-[a-f0-9]{16}\.css">'
    index,scripts=re.subn(script,'',index)
    index,styles=re.subn(style,'',index)
    if scripts!=1 or styles!=1:raise ValueError('expected one existing Fish extension')
    index,entries=re.subn(r'(assets/index-[A-Za-z0-9_-]+?)-fish-[a-f0-9]{16}(\.js)',r'\1\2',index)
    if entries!=1:raise ValueError('expected one prior alias')
    return assets(index,base_js,fish_js,fish_css)
