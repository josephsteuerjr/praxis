"""A Python argv and unchanged source on stdin, with the local hand library loaded."""
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile

BOOTSTRAP = (
    "import sys; sys.path.insert(0,sys.argv.pop(1)); "
    "import praxis_hands as h; sys.path.pop(0); "
    "g={'__name__':'__main__','__file__':'<python>','h':h}; "
    "exec(compile(sys.stdin.buffer.read(),'<python>','exec'),g,g)"
)


def argv():
    return [sys.executable, '-B', '-c', BOOTSTRAP,
            str(Path(__file__).resolve().parent / 'pyhands')]


@contextmanager
def source_input(code):
    if not isinstance(code, str):
        raise TypeError('Python code must be a string')
    with tempfile.TemporaryFile() as stream:
        stream.write(code.encode('utf-8'))
        stream.seek(0)
        yield stream
