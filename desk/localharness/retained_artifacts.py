"""Read-only archive/member checks recorded by the canonical verification process.

These checks bind retained historical records to actual archive bytes. They do
not execute historical programs or certify a current platform installation.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

FORMAT = 'helene-retained-artifact-inputs.v1'
MAX_MEMBER = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_TEXT = 64 * 1024 * 1024


def _relative(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Invalid retained evidence path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != name:
        raise ValueError('Noncanonical retained evidence path')
    return path


def _file(root, name, *, missing_ok=False):
    path = Path(root).absolute().joinpath(*_relative(name).parts)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Retained evidence file unavailable: ' + name)
    current = path
    while current != Path(root).absolute():
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('Linked retained evidence input: ' + name)
        current = current.parent
    if missing_ok:
        try:
            path.stat()
        except FileNotFoundError:
            return None
    if not path.is_file():
        raise ValueError('Retained evidence file unavailable: ' + name)
    return path


def _at(value, pointer):
    if not isinstance(pointer, list):
        raise ValueError('JSON reference must be a list')
    try:
        for key in pointer:
            if isinstance(value, dict) and isinstance(key, str):
                value = value[key]
            elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
                value = value[key]
            else:
                raise ValueError('Invalid JSON reference')
    except (KeyError, IndexError) as exc:
        raise ValueError('Missing JSON reference') from exc
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'(sha256:)?[0-9a-f]{64}', value):
        raise ValueError('Invalid recorded SHA256')
    return value.removeprefix('sha256:')


def _hash_stream(stream, check, destination=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        check()
        block = stream.read(1024 * 1024)
        if not block:
            break
        size += len(block)
        digest.update(block)
        if destination is not None:
            destination.write(block)
    return {'sha256': digest.hexdigest(), 'size': size}


class _Archive:
    def __init__(self, path, stack, check):
        self.stack, self.check = stack, check
        self.zips = {(): self._open(path)}
        self.hashes = {}
        self.documents = {}

    def _open(self, source):
        archive = self.stack.enter_context(zipfile.ZipFile(source))
        names = set()
        total = 0
        for entry in archive.infolist():
            self.check()
            _relative(entry.filename.rstrip('/') if entry.is_dir() else entry.filename)
            if entry.filename in names or entry.flag_bits & 1:
                raise ValueError('Duplicate or encrypted archive member')
            names.add(entry.filename)
            total += entry.file_size
            if entry.file_size > MAX_MEMBER or total > MAX_EXPANDED:
                raise ValueError('Retained archive exceeds read budget')
        return archive

    def _member(self, chain):
        if not isinstance(chain, list) or not 1 <= len(chain) <= 3:
            raise ValueError('Invalid retained archive member chain')
        for name in chain:
            _relative(name)
        prefix = ()
        for name in chain[:-1]:
            following = (*prefix, name)
            if following not in self.zips:
                spool = self.stack.enter_context(tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024))
                with self.zips[prefix].open(name) as stream:
                    self.hashes[following] = _hash_stream(stream, self.check, spool)
                spool.seek(0)
                self.zips[following] = self._open(spool)
            prefix = following
        return self.zips[prefix], chain[-1]

    def digest(self, chain):
        archive, name = self._member(chain)
        key = tuple(chain)
        if key not in self.hashes:
            with archive.open(name) as stream:
                self.hashes[key] = _hash_stream(stream, self.check)
        return dict(self.hashes[key])

    def document(self, chain):
        archive, name = self._member(chain)
        key = tuple(chain)
        if key not in self.documents:
            if archive.getinfo(name).file_size > MAX_TEXT:
                raise ValueError('Retained JSON member exceeds read budget')
            with archive.open(name) as stream:
                self.documents[key] = json.load(stream)
        return self.documents[key]

    def verify_crc(self):
        for entry in self.zips[()].infolist():
            if not entry.is_dir():
                with self.zips[()].open(entry) as stream:
                    _hash_stream(stream, self.check)


def verify(root, declaration, repository, stopped=lambda: False, *, files=None):
    """Compute evidence from bound bytes; never accept a supplied success flag."""
    root, repository = Path(root), Path(repository)
    if files is None:
        from .source_verification_bundle import inventory
        files = inventory(root)
    input_documents = {}

    def input_file(name):
        if name not in files:
            raise ValueError('Retained input is outside the bound inventory: ' + str(name))
        return _file(root, name)

    def checked_input(name):
        path = input_file(name)
        if path.stat().st_size > MAX_TEXT:
            raise ValueError('Retained document exceeds read budget')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != files[name]['sha256'] or len(raw) != files[name]['size']:
            raise ValueError('Bound retained document changed: ' + name)
        return raw

    def check():
        if stopped():
            raise InterruptedError('Stopped retained artifact verification')

    def file_digest(path):
        with path.open('rb') as stream:
            return _hash_stream(stream, check)

    def document(name):
        if name not in input_documents:
            input_documents[name] = json.loads(checked_input(name))
        return input_documents[name]

    value = document(declaration)
    if value.get('format') != FORMAT or not isinstance(value.get('archives'), list) or not value['archives']:
        raise ValueError('No declared retained artifact checks')
    reports = []
    identities = set()
    for row in value['archives']:
        check()
        name = row['path']
        if name in identities:
            raise ValueError('Duplicate retained artifact declaration')
        identities.add(name)
        reference = row['digest_reference']
        expected = _sha(_at(document(reference['document']), reference['pointer']))
        if not row.get('members') or not (row.get('source_checks') or row.get('source_maps')):
            raise ValueError('Retained archive needs member and composed-source checks')
        with ExitStack() as stack:
            stream = stack.enter_context(input_file(name).open('rb'))
            actual = _hash_stream(stream, check)
            if (actual['sha256'] != expected or actual['sha256'] != files[name]['sha256']
                    or actual['size'] != files[name]['size']):
                raise ValueError('Original artifact SHA256 mismatch: ' + name)
            report = {'path': name, **actual, 'digest_reference': reference,
                      'members': [], 'digest_checks': [], 'source_checks': [], 'input_checks': []}
            stream.seek(0)
            archive = _Archive(stream, stack, check)
            archive.verify_crc()
            report['outer_crc_verified'] = True
            for member in row['members']:
                found = archive.digest(member['chain'])
                current = file_digest(_file(repository, member['repository_path']))
                if found != current:
                    raise ValueError('Retained member differs from composed record: ' + member['repository_path'])
                report['members'].append({**member, **found})
            for binding in row.get('digest_checks', []):
                found = archive.digest(binding['chain'])
                expected = _sha(_at(archive.document(binding['reference_chain']), binding['pointer']))
                if found['sha256'] != expected:
                    raise ValueError('Archived interpreter/binary identity mismatch')
                report['digest_checks'].append({**binding, **found})
            for binding in row.get('input_checks', []):
                expected = _sha(_at(archive.document(binding['reference_chain']), binding['pointer']))
                raw = checked_input(binding['input_path'])
                found = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
                if found['sha256'] != expected:
                    raise ValueError('Historical invocation source differs from archived manifest')
                report['input_checks'].append({**binding, **found})
            checks = list(row.get('source_checks', []))
            for mapping in row.get('source_maps', []):
                values = _at(archive.document(mapping['reference_chain']), mapping['pointer'])
                if not isinstance(values, dict) or not values:
                    raise ValueError('Empty historical source inventory')
                for relative in sorted(values):
                    _relative(relative)
                    checks.append({'repository_path': mapping['repository_prefix'] + '/' + relative,
                        'reference_chain': mapping['reference_chain'], 'pointer': [*mapping['pointer'], relative]})
            for binding in checks:
                expected = _sha(_at(archive.document(binding['reference_chain']), binding['pointer']))
                path = _file(repository, binding['repository_path'], missing_ok=True)
                current = file_digest(path) if path is not None else {'sha256': None, 'size': None}
                # The original archive and its claims remain authenticated.
                # A changed/deleted current source invalidates the historical
                # source match, not the new candidate's ability to be tested.
                report['source_checks'].append({**binding, **current,
                    'historical_sha256': expected, 'current_source_present': path is not None,
                    'matches_current_source': current['sha256'] == expected})
            report['all_current_sources_match'] = all(
                item['matches_current_source'] for item in report['source_checks'])
            # Hash the same open archive after inspecting members. Replacing a
            # pathname cannot change our opened subject; in-place changes fail.
            stream.seek(0)
            if _hash_stream(stream, check) != actual:
                raise ValueError('Original archive changed during verification')
        reports.append(report)
    git_blobs = []
    for binding in value.get('git_blobs', []):
        check()
        raw = checked_input(binding['input_path'])
        actual = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
        reference = binding['reference']
        expected = _at(document(reference['document']), reference['pointer'])
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{40}', expected) or actual != expected:
            raise ValueError('Historical invocation Git blob mismatch')
        git_blobs.append({**binding, 'git_blob_sha1': actual, 'sha256': hashlib.sha256(raw).hexdigest()})
    documents = []
    for name in value.get('documents', []):
        raw = checked_input(name)
        raw.decode('utf-8-sig')
        documents.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)})
    differences = [{'archive': row['path'], **item}
                   for row in reports for item in row['source_checks'] if not item['matches_current_source']]
    return {'format': 'helene-retained-artifact-verification.v2',
            'producer': 'canonical verification process, computed from declared archive bytes',
            'declaration': {'path': declaration, 'sha256': files[declaration]['sha256'], 'size': files[declaration]['size']},
            'archives': reports, 'documents': documents, 'git_blob_checks': git_blobs,
            'source_differences': differences,
            'all_current_sources_match': not differences,
            'source_scope': 'Historical execution cannot qualify changed or removed sources. Differences are evidence for current tests and independent review.',
            'historical_code_executed': False, 'current_platform_or_installation_accepted': False}


def publish(root, declaration, repository, evidence, stopped=lambda: False, *, files=None):
    """Freeze computed checks and invocation documents with the battery receipt."""
    result = verify(root, declaration, repository, stopped, files=files)
    evidence = Path(evidence)
    for index, row in enumerate(result['documents']):
        name = f'retained-invocation-{index:03d}.md'
        raw = _file(root, row['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('Retained invocation document changed before freeze')
        (evidence / name).write_bytes(raw)
        row['evidence_path'] = name
    from .source_verification_bundle import encoded
    (evidence / 'retained-artifact-verification.md').write_bytes(encoded(result))
    return result
