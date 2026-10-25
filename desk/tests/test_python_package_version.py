"""Python package identity remains readable by preserved legacy agents."""
import ast
import importlib.util
import json
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_notice', DESK/'localharness/package_notice.py')
notice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notice)


def test_python_manifest_not_a_qualification_passport(tmp_path):
    program = tmp_path/'Helene'
    program.mkdir()
    (program/'python-app.json').write_text(json.dumps({'format':'helene-python-app.v1','version':'1.9.0'}))
    notice.observe(tmp_path/'data', program, {'installed':{'version':'1.8.0'}})
    receipt = notice.pending(tmp_path/'data')
    assert receipt['from_version'] == '1.8.0'
    assert receipt['to_version'] == '1.9.0'
    assert not (program/'helene-build.json').exists()


def test_legacy_incomplete_passport_does_not_gain_authority(tmp_path):
    (tmp_path/'helene-build.json').write_text(json.dumps({'version':'1.8.0'}))
    notice.observe(tmp_path/'data', tmp_path, {})
    assert not (tmp_path/'data/memory/.state/package-version.json').exists()


def test_legacy_runner_reads_compatibility_metadata(tmp_path):
    # Execute the unchanged reader, not a second implementation; importing the
    # whole runner would initialize unrelated runtime dependencies.
    source = ast.parse((DESK/'localharness/runner.py').read_text(encoding='utf-8'))
    reader = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_read_passport')
    namespace = {'Path':Path, 'json':json}
    exec(compile(ast.Module(body=[reader], type_ignores=[]), 'legacy-reader', 'exec'), namespace)
    (tmp_path/'helene-build.json').write_text(json.dumps({'format':'helene-python-version.v1','version':'1.9.0','version_source':'python-app.json'}))
    assert namespace['_read_passport'](tmp_path) == '1.9.0'
