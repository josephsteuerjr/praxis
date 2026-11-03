"""Small complete verification input for protocol tests, never product approval."""
from pathlib import Path
import sys
from localharness import source_verification_bundle as bundle
from localharness.package_notice import read,write

BOOTSTRAP='# Synthetic hermetic protocol fixture, no owner state.\n'

def bind(parent,program,version,*,desk_test=None):
    source=Path(parent)/'synthetic-verification-source'
    test='import unittest\nclass Fixture(unittest.TestCase):\n def test_fixture(self):self.assertTrue(True)\n'
    for name,text in {'praxis/praxis_test.py':BOOTSTRAP,
        'praxis/test_fixture.py':test,'helene/core/fixture.py':'VALUE=True\n',
        'desk/tests/t_fixture.py':desk_test or test,'desk/app/test/fixture.mjs':'import assert from "node:assert/strict";assert.equal(1,1);\n'}.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf8')
    program=Path(program)
    result=bundle.build(source,program/'verification',version,{sys.platform:bundle.executor_lock()})
    passport=read(program/'python-package-files.json') or {'files':{}}
    passport['files']['verification/manifest.json']=result['sha256']
    write(program/'python-package-files.json',passport)
    return result
