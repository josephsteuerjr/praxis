"""Archive identity fixtures; no historical/native executable is run here."""
from pathlib import Path
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import retained_artifacts as retained
from localharness import source_verification_bundle as bundle


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def zipped(values):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, value in values.items():
            archive.writestr(name, value)
    return stream.getvalue()


class RetainedArtifacts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'; self.inputs.mkdir()
        self.repository = self.root / 'repository'; (self.repository / 'live').mkdir(parents=True)
        self.code = b'VALUE = 1\n'
        self.record = b'original recorded output\n'
        self.binary = b'not executable - binary fixture\x00'
        (self.repository / 'live/code.py').write_bytes(self.code)
        (self.repository / 'live/record.txt').write_bytes(self.record)
        self.manifest = {'source': sha(self.code), 'binary': sha(self.binary),
                         'source_files': {'code.py': sha(self.code)}}
        self.make_archive()
        (self.inputs / 'producer.txt').write_bytes(b'Original invocation document fixture.\n')
        self.declaration = {'format': retained.FORMAT, 'documents': ['producer.txt'], 'archives': [{
            'path': 'original.zip', 'digest_reference': {'document': 'provider.json', 'pointer': ['digest']},
            'members': [{'chain': ['record.txt'], 'repository_path': 'live/record.txt'}],
            'digest_checks': [{'chain': ['candidate.zip', 'bin/python'], 'reference_chain': ['manifest.json'],
                               'pointer': ['binary']}],
            'source_checks': [{'reference_chain': ['manifest.json'], 'pointer': ['source'],
                               'repository_path': 'live/code.py'}],
            'source_maps': [{'reference_chain': ['manifest.json'], 'pointer': ['source_files'],
                             'repository_prefix': 'live'}]}]}
        self.save()

    def make_archive(self, extra=None):
        values = {'record.txt': self.record, 'manifest.json': json.dumps(self.manifest),
                  'candidate.zip': zipped({'bin/python': self.binary})}
        values.update(extra or {})
        raw = zipped(values)
        (self.inputs / 'original.zip').write_bytes(raw)
        (self.inputs / 'provider.json').write_text(json.dumps({'digest': 'sha256:' + sha(raw)}))

    def save(self):
        (self.inputs / 'bindings.json').write_text(json.dumps(self.declaration))

    def verify(self, **kwargs):
        return retained.verify(self.inputs, 'bindings.json', self.repository, **kwargs)

    def test_actual_nested_member_and_composed_source_are_checked(self):
        result = self.verify()
        archive, = result['archives']
        self.assertEqual(archive['members'][0]['sha256'], sha(self.record))
        self.assertEqual(archive['digest_checks'][0]['sha256'], sha(self.binary))
        self.assertEqual({row['sha256'] for row in archive['source_checks']}, {sha(self.code)})
        self.assertTrue(result['all_current_sources_match'])
        self.assertEqual(result['source_differences'], [])
        self.assertFalse(result['historical_code_executed'])
        self.assertFalse(result['current_platform_or_installation_accepted'])

    def test_changed_original_archive_cannot_keep_provider_identity(self):
        path = self.inputs / 'original.zip'
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'artifact SHA256 mismatch'):
            self.verify()

    def test_changed_provider_metadata_cannot_redefine_bound_identity(self):
        files = bundle.inventory(self.inputs)
        (self.inputs / 'provider.json').write_text(json.dumps({'digest': sha(b'other')}))
        with self.assertRaisesRegex(ValueError, 'Bound retained document changed'):
            self.verify(files=files)

    def test_excluded_unbound_metadata_is_never_read(self):
        hidden = self.inputs / '.git'; hidden.mkdir()
        (hidden / 'provider.json').write_bytes((self.inputs / 'provider.json').read_bytes())
        self.declaration['archives'][0]['digest_reference']['document'] = '.git/provider.json'
        self.save()
        with self.assertRaisesRegex(ValueError, 'outside the bound inventory'):
            self.verify()

    def test_in_place_archive_change_during_inspection_is_rejected(self):
        from unittest import mock
        original = retained._Archive.verify_crc
        path = self.inputs / 'original.zip'
        def mutate(archive):
            original(archive)
            with path.open('ab') as stream:
                stream.write(b'concurrent change')
        with mock.patch.object(retained._Archive, 'verify_crc', mutate):
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                self.verify()

    def test_rewritten_retained_output_is_not_accepted(self):
        (self.repository / 'live/record.txt').write_bytes(b'authored replacement')
        with self.assertRaisesRegex(ValueError, 'member differs'):
            self.verify()

    def test_old_source_receipt_does_not_qualify_modified_code(self):
        changed = b'VALUE = 2\n'
        (self.repository / 'live/code.py').write_bytes(changed)
        result = self.verify()
        self.assertFalse(result['all_current_sources_match'])
        self.assertFalse(result['current_platform_or_installation_accepted'])
        self.assertFalse(result['archives'][0]['all_current_sources_match'])
        self.assertTrue(result['source_differences'])
        for row in result['source_differences']:
            self.assertEqual(row['historical_sha256'], sha(self.code))
            self.assertEqual(row['sha256'], sha(changed))
            self.assertTrue(row['current_source_present'])
            self.assertFalse(row['matches_current_source'])

    def test_removed_current_source_is_an_explicit_historical_applicability_gap(self):
        (self.repository / 'live/code.py').unlink()
        result = self.verify()
        self.assertFalse(result['all_current_sources_match'])
        self.assertTrue(result['source_differences'])
        for row in result['source_differences']:
            self.assertEqual(row['historical_sha256'], sha(self.code))
            self.assertIsNone(row['sha256'])
            self.assertFalse(row['current_source_present'])
            self.assertFalse(row['matches_current_source'])

    def test_missing_current_source_cannot_turn_escape_into_applicability_gap(self):
        self.declaration['archives'][0]['source_checks'][0]['repository_path'] = '../absent.py'
        self.save()
        with self.assertRaisesRegex(ValueError, 'path'):
            self.verify()

    def test_binary_must_match_receipt_inside_the_actual_archive(self):
        self.manifest['binary'] = sha(b'another binary')
        self.make_archive()
        with self.assertRaisesRegex(ValueError, 'interpreter/binary identity mismatch'):
            self.verify()

    def test_missing_historical_source_entry_is_not_success(self):
        del self.manifest['source']
        self.make_archive()
        with self.assertRaisesRegex(ValueError, 'Missing JSON reference'):
            self.verify()

    def test_producer_source_must_match_historical_manifest(self):
        self.declaration['archives'][0]['input_checks'] = [{'input_path': 'producer.txt',
            'reference_chain': ['manifest.json'], 'pointer': ['source']}]
        self.save()
        with self.assertRaisesRegex(ValueError, 'invocation source differs'):
            self.verify()
        (self.inputs / 'producer.txt').write_bytes(self.code)
        self.assertEqual(self.verify()['archives'][0]['input_checks'][0]['sha256'], sha(self.code))

    def test_git_blob_reference_checks_bytes_with_git_header(self):
        raw = (self.inputs / 'producer.txt').read_bytes()
        expected = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        (self.inputs / 'git.json').write_text(json.dumps({'blob': expected}))
        self.declaration['git_blobs'] = [{'input_path': 'producer.txt',
            'reference': {'document': 'git.json', 'pointer': ['blob']}}]
        self.save()
        self.assertEqual(self.verify()['git_blob_checks'][0]['git_blob_sha1'], expected)
        (self.inputs / 'producer.txt').write_bytes(raw + b'changed')
        with self.assertRaisesRegex(ValueError, 'Git blob mismatch'):
            self.verify()

    def test_archive_and_repository_paths_cannot_escape(self):
        self.declaration['archives'][0]['members'][0]['repository_path'] = '../outside.txt'
        self.save()
        with self.assertRaisesRegex(ValueError, 'path'):
            self.verify()
        self.set_valid_path()
        self.make_archive({'../untrusted': b'outside'})
        with self.assertRaisesRegex(ValueError, 'path'):
            self.verify()

    def set_valid_path(self):
        self.declaration['archives'][0]['members'][0]['repository_path'] = 'live/record.txt'
        self.save()

    def test_duplicate_zip_names_are_ambiguous_even_with_matching_archive_digest(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.inputs / 'original.zip', 'a') as archive:
                archive.writestr('record.txt', b'another entry')
        raw = (self.inputs / 'original.zip').read_bytes()
        (self.inputs / 'provider.json').write_text(json.dumps({'digest': sha(raw)}))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.verify()

    def test_stop_does_not_publish_partial_evidence(self):
        evidence = self.root / 'evidence'; evidence.mkdir()
        with self.assertRaises(InterruptedError):
            retained.publish(self.inputs, 'bindings.json', self.repository, evidence, lambda: True)
        self.assertEqual(list(evidence.iterdir()), [])

    def test_published_invocation_keeps_exact_original_bytes(self):
        evidence = self.root / 'evidence'; evidence.mkdir()
        result = retained.publish(self.inputs, 'bindings.json', self.repository, evidence)
        document, = result['documents']
        self.assertEqual((evidence / document['evidence_path']).read_bytes(),
                         (self.inputs / document['path']).read_bytes())
        self.assertEqual(json.loads((evidence / 'retained-artifact-verification.md').read_bytes()), result)

    def fixture_battery(self):
        import t_source_update_battery_2810 as fixture
        case = fixture.Battery()
        case.setUp(); self.addCleanup(case.doCleanups)
        (case.core / 'code.py').write_bytes(self.code)
        (case.core / 'record.txt').write_bytes(self.record)
        from localharness import source_merge
        case.operation['candidate_sha256'] = source_merge.candidate_signature(case.directory)
        case.bundle = case.root / 'with-retained-inputs'
        case.binding = bundle.build(case.repo, case.bundle, '2.3.0', {sys.platform: case.lock},
                                    native=bundle.NATIVE, retained_inputs=self.inputs)
        case.manifest = json.loads((case.bundle / 'manifest.json').read_bytes())
        case.operation['verification_bundle'] = case.binding
        return case

    def test_bundle_rejects_modified_archive_before_execution(self):
        case = self.fixture_battery()
        bundle.load(case.bundle, case.binding['sha256'], '2.3.0')
        (case.bundle / 'evidence-inputs/original.zip').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Retained artifact verification inputs changed'):
            bundle.load(case.bundle, case.binding['sha256'], '2.3.0')

    def test_packager_preserves_declared_archive_bytes(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
        import portable_refresh
        case = self.fixture_battery()
        entries = portable_refresh._bundle(case.bundle, case.binding['sha256'], '2.3.0')
        self.assertEqual(entries['evidence-inputs/original.zip'].data, (self.inputs / 'original.zip').read_bytes())
        self.assertEqual(entries['evidence-inputs/bindings.json'].data, (self.inputs / 'bindings.json').read_bytes())

    def test_packager_refuses_undeclared_evidence_bytes(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
        import portable_refresh
        case = self.fixture_battery()
        (case.bundle / 'evidence-inputs/unbound.txt').write_text('not declared')
        with self.assertRaises(ValueError):
            portable_refresh._bundle(case.bundle, case.binding['sha256'], '2.3.0')

    def test_real_canonical_fixture_freezes_computed_proof_and_refuses_its_mutation(self):
        # Existing fixture executes Python tests; native result stubs only test
        # receipt plumbing and do not claim actual product/platform acceptance.
        case = self.fixture_battery()
        cache, report, _ = case.green_fixture()
        reference = report['retained_artifact_verification']
        proof = cache / 'evidence' / reference['evidence']
        self.assertEqual(sha(proof.read_bytes()), reference['sha256'])
        receipt = json.loads((cache / 'receipt.json').read_bytes())
        self.assertEqual(receipt['files'][reference['evidence']], reference['sha256'])
        self.assertEqual(json.loads(proof.read_bytes())['archives'][0]['members'][0]['sha256'], sha(self.record))
        proof.write_text('{}')
        from localharness import source_update_battery
        with self.assertRaises(ValueError):
            source_update_battery.validate(case.operation, case.directory)

    def test_current_full_attempt_continues_with_changed_historical_source(self):
        case = self.fixture_battery()
        changed = b'VALUE = 2\n'
        (case.core / 'code.py').write_bytes(changed)
        from localharness import source_merge, source_update_battery
        case.operation['candidate_sha256'] = source_merge.candidate_signature(case.directory)
        cache, report, _ = case.green_fixture()
        reference = report['retained_artifact_verification']
        proof = cache / 'evidence' / reference['evidence']
        result = json.loads(proof.read_bytes())
        self.assertFalse(result['all_current_sources_match'])
        self.assertTrue(result['source_differences'])
        self.assertEqual({row['sha256'] for row in result['source_differences']}, {sha(changed)})
        receipt = json.loads((cache / 'receipt.json').read_bytes())
        self.assertEqual(receipt['files'][reference['evidence']], sha(proof.read_bytes()))
        self.assertTrue(source_update_battery.validate(case.operation, case.directory)['review_evidence_complete'])
        self.assertFalse(result['current_platform_or_installation_accepted'])


if __name__ == '__main__':
    unittest.main()
