"""Generated attachment state is separate from user-provided filename space.

The ingress writer creates DIRECTORY before assigning payload filenames; normal
filename conflict allocation therefore also protects the directory itself.
"""
from pathlib import Path
import hashlib
DIRECTORY = '.helene-media-state'

def directory(source):return Path(source).parent / DIRECTORY

def path_for(source,kind):
    source=Path(source)
    key=hashlib.sha256(source.name.encode('utf-8')).hexdigest()
    return directory(source)/(key+'-'+kind)
