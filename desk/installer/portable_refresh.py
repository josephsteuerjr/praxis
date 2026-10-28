"""Refresh a verified distribution ZIP with an explicit current source composition.

No multi-GB extraction, installed-profile donor, native/runtime rebuild or release
publication. Unchanged runtime/wrappers retain their exact compressed local ZIP
records. Build receipts attest bytes/CRC/source closure, not native acceptance.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import sys
import zipfile

from portable_zip import (PREFIX, Entry, Package, copy_records, digest, encoded,
                          mode, record_hashes, relative, sha, snapshot)
from portable_sources import (FRONTS, application, build_frontends, compose_core,
                              checked, identities, public, scan, sealed, verify_frontends)

PLATFORMS = ('windows', 'macos', 'debian', 'fedora')


def _tree_inventory(package, prefix):
    return {n[len(prefix):]: package.get(n) for n in sorted(package.names())
            if n.startswith(prefix) and not n.endswith('/')}


def _bundle(root, expected, version):
    root = Path(root)
    if digest(root / 'manifest.json') != expected:
        raise ValueError('Verification bundle manifest identity mismatch')
    value = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    if value.get('format') != 'helene-verification-bundle.v1' or value.get('version') != version:
        raise ValueError('Verification bundle version/format mismatch')
    # The published bundle's own loader owns full lane completeness. Packaging
    # binds exact manifest/repository/runner bytes without executing source code.
    entries = sealed(root)
    expected_names = {'manifest.json'}
    groups = [('repository', value.get('files')), ('runner', value.get('runners'))]
    for platform, dependency in value.get('dependencies', {}).items():
        if platform not in ('win32', 'linux', 'darwin'):
            raise ValueError('Invalid verification dependency platform')
        groups.append(('dependencies/' + platform, dependency.get('files')))
    for folder, rows in groups:
        if not isinstance(rows, dict) or not rows:
            raise ValueError('Verification bundle sources/runner missing')
        for name, row in rows.items():
            relative(name)
            dest = folder + '/' + name
            expected_names.add(dest)
            entry = entries.get(dest)
            if entry is None or sha(entry.data) != row['sha256'] or len(entry.data) != row['size']:
                raise ValueError('Verification bundle input changed: ' + dest)
            entries[dest] = Entry(entry.data, row['mode'])
    if set(entries) != expected_names:
        raise ValueError('Verification bundle has undeclared files')
    return entries


def _bundle_source_closure(source, bundle, core, core_origins, app, app_origins):
    """A same-version stale suite/source bundle is not the candidate's material."""
    manifest = json.loads(bundle['manifest.json'].data)
    for name, row in manifest['files'].items():
        if name.startswith('live/'):
            continue
        current = checked(source, name)
        if not current.is_file() or digest(current) != row['sha256'] or current.stat().st_size != row['size']:
            raise ValueError('Verification bundle source/test is stale: ' + name)
    for name, entry in core.items():
        for origin in ('live/' + name, core_origins[name]):
            if manifest['files'].get(origin, {}).get('sha256') != sha(entry.data):
                raise ValueError('Verification bundle does not bind packaged core: ' + name)
    for name, entry in app.items():
        if manifest['files'].get(app_origins[name], {}).get('sha256') != sha(entry.data):
            raise ValueError('Verification bundle does not bind packaged application: ' + name)


def _overlay(root, expected, platform):
    root = Path(root)
    if digest(root / 'manifest.json') != expected:
        raise ValueError('Runtime overlay manifest identity mismatch')
    value = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    if (value.get('format') != 'helene-portable-runtime-overlay.v1' or value.get('platform') != platform
            or not value.get('inputs') or not value.get('files')):
        raise ValueError('Runtime overlay needs explicit platform/dependency inputs')
    entries = sealed(root)
    if set(entries) != {'manifest.json', *value['files']}:
        raise ValueError('Runtime overlay has undeclared files')
    result = {}
    for name, row in value['files'].items():
        if not name.startswith('runtime/') or identities({name: entries[name]})[name]['sha256'] != row['sha256']:
            raise ValueError('Runtime overlay outside declared dependency bytes: ' + name)
        if len(entries[name].data) != row['size'] or row['mode'] not in ('100644', '100755'):
            raise ValueError('Runtime overlay size/mode mismatch')
        result[name] = Entry(entries[name].data, row['mode'])
    removals = value.get('remove', {})
    if not isinstance(removals, dict):
        raise ValueError('Runtime overlay removals must name exact prior file hashes')
    for name, prior_sha in removals.items():
        relative(name)
        if (not name.startswith('runtime/') or name in result
                or not isinstance(prior_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', prior_sha)):
            raise ValueError('Runtime overlay removal outside exact dependency bytes: ' + name)
    return result, value


def _metadata(p, source, core, origins, version, platform, donor_sha, front, bundle_sha, legacy):
    upstream = snapshot(core)
    stock = {n: {'sha256': sha(e.data), 'mode': e.mode} for n, e in sorted(core.items())}
    upstream_meta = {'format': 'helene-code-upstream.v1', 'version': version,
                     'files': stock, 'sha256': sha(upstream)}
    p.put(PREFIX + 'code-upstream.zip', Entry(upstream))
    p.put_value('code-upstream.json', upstream_meta)
    view = core
    if legacy:
        declaration = p.value('source-admission.json')
        if not declaration or declaration.get('format') != 'helene-source-admission.v1':
            raise ValueError('Legacy admission requires the donor explicit custody-view declaration')
        view = _tree_inventory(p, PREFIX + 'tree/')
        if any(not public(name) for name in view):
            raise ValueError('Legacy source admission view contains private/generated state')
        actual = {n: {'sha256': sha(e.data), 'mode': e.mode} for n, e in sorted(view.items())}
        if actual != declaration.get('view_files'):
            raise ValueError('Donor legacy admission view differs from its declaration')
        p.put(PREFIX + 'source-admission-upstream.zip', Entry(upstream))
        p.put_value('source-admission-upstream.json', upstream_meta)
        declaration.update(target_version=version, upstream_sha256=sha(upstream),
                           pristine={'manifest_sha256': sha(encoded(upstream_meta))})
        p.put_value('source-admission.json', declaration)
    else:
        for name in list(p.names()):
            if name.startswith(PREFIX + 'source-admission'):
                p.put(name, None)
        p.replace_tree(PREFIX + 'tree/', core)
    for name in ('edition-inventory.json', 'selected-source.json'):
        old = p.value(name, {})
        old.update(version=version, files={n: {'sha256': sha(e.data), 'mode': e.mode} for n, e in sorted(view.items())},
                   current_tree_scope='declared custody view; pristine B in code-upstream.zip' if legacy else 'exact packaged pristine stock')
        p.put_value(name, old)
    for name in ('python-app.json', 'helene-build.json'):
        value = p.value(name)
        if not isinstance(value, dict):
            raise ValueError('Missing distribution carrier: ' + name)
        value.update(version=version, local_candidate=True)
        if name == 'python-app.json':
            value.update(delivery='portable', verification_bundle_sha256=bundle_sha)
        p.put_value(name, value)
    downloads = p.value('app/component-downloads.json')
    if not isinstance(downloads, dict):
        raise ValueError('Donor has no target-specific component-download map')
    downloads['app_version'] = version
    p.put_value('app/component-downloads.json', downloads)
    source_manifest = {'format': 'helene-portable-source-composition.v1', 'version': version,
        'platform': platform, 'selection': 'current praxis then current helene/core overlay; exact bytes, no LF normalization',
        'donor_sha256': donor_sha, 'previous_manifest_sha256': digest(Path(source) / 'HELENE-SOURCE.json')
            if (Path(source) / 'HELENE-SOURCE.json').is_file() else None,
        'legacy_admission_view': bool(legacy), 'upstream_sha256': sha(upstream),
        'sources': origins, 'frontends': front, 'verification_bundle_sha256': bundle_sha,
        'native_runtime_rebuilt': False, 'native_qualified': False}
    p.put_value('source-composition.json', source_manifest)
    # source-inputs is the existing coordinator's app-relative exact-byte ABI.
    app = _tree_inventory(p, PREFIX + 'app/')
    app.pop('desk.json', None)
    files = {n: sha(e.data) for n, e in sorted(app.items())}
    p.put_value('source-inputs.json', {'version': version, 'files': files,
                                    'composition': 'source-composition.json'})
    desk = p.value('app/desk.json', {})
    desk.update(version=version, files=files,
                digest=sha('\n'.join(f'{n} {h}' for n, h in sorted(files.items())).encode()))
    static = {n[len('static/'):]: h for n, h in files.items() if n.startswith('static/')}
    desk['static'] = sha('\n'.join(f'{n} {h}' for n, h in sorted(static.items())).encode())
    p.put_value('app/desk.json', desk)
    old_build = p.value('build-source.json')
    if old_build is not None:
        old_build.update(version=version, app_files=files, native_runtime_rebuilt=False,
                         current_app_scope='exact refreshed app; original native build provenance retained')
        p.put_value('build-source.json', old_build)
    full = {n[len(PREFIX):]: h for n, h in p.hashes().items() if n != PREFIX + 'python-package-files.json'}
    p.put_value('python-package-files.json', {'format': 'helene-python-program-files.v1',
        'version': version, 'files': full, 'source_admission': {'pristine_B_separate': bool(legacy)}})
    return downloads


def _requirements(source, core, platform):
    """The packaging declaration remains the dependency SSOT."""
    declaration = Path(source) / 'desk/deskpkg.py'
    spec = importlib.util.spec_from_file_location('_helene_portable_deskpkg', declaration)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        flavor = 'linux' if platform in ('debian', 'fedora') else platform
        channel = ('\n'.join(module.requirements(flavor)) + '\n').encode()
    finally:
        sys.modules.pop(spec.name, None)
    if 'requirements.txt' not in core:
        raise ValueError('Core dependency declaration is missing')
    return channel, core['requirements.txt'].data + b'\n# H\xc3\xa9l\xc3\xa8ne channel/runtime\n' + channel


def plan(donor, source, version, platform, *, expected_sha256, frontend_receipt,
         verification_bundle, verification_sha256, legacy_admission=False,
         runtime_overlay=None, runtime_overlay_sha256=''):
    donor, source = Path(donor).resolve(), Path(source).resolve()
    if platform not in PLATFORMS or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?', version):
        raise ValueError('Explicit platform and product semver release version required')
    if not expected_sha256 or digest(donor) != expected_sha256:
        raise ValueError('Verified distribution donor SHA256 mismatch')
    front = verify_frontends(source, frontend_receipt)
    core, core_origins = compose_core(source)
    core_source_identity = identities(core)
    if platform == 'windows':
        # Match the existing source_merge.inventory Windows ABI (no POSIX x bit).
        core = {n: Entry(e.data) for n, e in core.items()}
    app, app_origins = application(source)
    app_source_identity = identities(app)
    dependency_declaration = digest(source / 'desk/deskpkg.py')
    bundle = _bundle(verification_bundle, verification_sha256, version)
    _bundle_source_closure(source, bundle, core, core_origins, app, app_origins)
    with zipfile.ZipFile(donor) as reader:
        p = Package(reader)
        for name in p.infos:
            rel = name[len(PREFIX):]
            top = rel.split('/')[0]
            if (top in {'data', 'agents', 'backups', 'logs', 'downloads', '.git', 'profile', 'memory', 'code', 'workspace'}
                    or top in {'auth.json', 'credentials.json', '.env', 'local_auth'}
                    or top.startswith('.env.') and top != '.env.example'):
                raise ValueError('Distribution donor contains profile state: ' + rel)
        if not any(n.startswith(PREFIX + 'runtime/') for n in p.infos):
            raise ValueError('Distribution runtime is missing')
        downloads = p.get(PREFIX + 'app/component-downloads.json')
        # Keep target-specific immutable component identities, never Windows refs
        # substituted for a Mac/Linux donor's download map.
        for name in ('app/', 'server/', 'verification/'):
            p.replace_tree(PREFIX + name, {})
        origins = {}
        for name, entry in app.items():
            old = p.infos.get(PREFIX + name)
            if old and mode(old) == '100755':
                entry = Entry(entry.data, '100755')
            p.put(PREFIX + name, entry)
            origins[name] = {'source': app_origins[name], **entry.identity()}
        if downloads is None:
            raise ValueError('Missing target component-downloads map')
        p.put(PREFIX + 'app/component-downloads.json', downloads)
        channel, combined = _requirements(source, core, platform)
        p.put(PREFIX + 'app/requirements-desk.txt', Entry(channel))
        p.put(PREFIX + 'requirements.txt', Entry(combined))
        worker = core.get('xtts_tts_worker.py')
        if worker:
            p.put(PREFIX + 'app/localharness/xtts_tts_worker.py', worker)
            origins['app/localharness/xtts_tts_worker.py'] = {'source': core_origins['xtts_tts_worker.py'], **worker.identity()}
        for n, entry in core.items():
            origins['upstream/' + n] = {'source': core_origins[n], **entry.identity()}
        for n, entry in bundle.items():
            p.put(PREFIX + 'verification/' + n, entry)
        overlay_identity = None
        if runtime_overlay:
            replacements, overlay_identity = _overlay(runtime_overlay, runtime_overlay_sha256, platform)
            # A dependency upgrade/downgrade must not leave the old dist-info
            # or obsolete modules beside the new wheel. Never glob-delete a
            # runtime subtree: each removed donor member has an exact old hash.
            for name, prior_sha in overlay_identity.get('remove', {}).items():
                old = p.get(PREFIX + name)
                if old is None or sha(old.data) != prior_sha:
                    raise ValueError('Runtime overlay prior file differs: ' + name)
                p.put(PREFIX + name, None)
            for name, entry in replacements.items():
                p.put(PREFIX + name, entry)
            p.put_value('runtime-refresh.json', overlay_identity)
        # Preconfigured distribution defaults are not the selected user's state.
        for name in ('helene.json', 'source-admission-state.json', 'python-migration.json', 'owner-stop.json'):
            p.put(PREFIX + name, None)
        optional = _metadata(p, source, core, origins, version, platform, expected_sha256,
                             front, verification_sha256, legacy_admission)
        if (identities(compose_core(source)[0]) != core_source_identity
                or identities(application(source)[0]) != app_source_identity
                or digest(source / 'desk/deskpkg.py') != dependency_declaration):
            raise ValueError('Source composition changed during planning; rebuild the frozen plan')
        verify_frontends(source, frontend_receipt)
        full_files = p.value('python-package-files.json')['files']
        runtime_files = {n: h for n, h in full_files.items() if n.startswith('runtime/')}
        wrappers = {n: h for n, h in full_files.items()
                    if '.app/' in n or n.endswith(('.exe', '.dll', '.dylib', '.so')) and not n.startswith(('runtime/', 'tree/', 'verification/'))}
        report = {'format': 'helene-portable-refresh-plan.v1', 'version': version, 'platform': platform,
            'input_sha256': expected_sha256, 'input_size': donor.stat().st_size,
            'source_composition_sha256': sha(p.get(PREFIX + 'source-composition.json').data),
            'verification_bundle_sha256': verification_sha256,
            'frontend_receipt_sha256': digest(frontend_receipt),
            'runtime_overlay_sha256': runtime_overlay_sha256 if runtime_overlay else None,
            'runtime_files_sha256': sha(encoded(runtime_files)), 'runtime_files': len(runtime_files),
            'preserved_native_wrappers': wrappers,
            'optional_artifacts': optional.get('artifacts', {}),
            'replacements': {n: e.identity() for n, e in sorted(p.replacements.items())},
            'removed': sorted(p.removed), 'native_runtime_rebuilt': False,
            'native_qualified': False, 'runtime_changed': bool(overlay_identity)}
        report['sha256'] = sha(encoded(report))
        return report, dict(p.replacements), set(p.removed)


def verify_archive(path, report):
    with zipfile.ZipFile(path) as reader:
        p = Package(reader)
        hashes = p.hashes()  # stream all bytes through CRC + independent SHA
        full = p.value('python-package-files.json')
        actual = {n[len(PREFIX):]: h for n, h in hashes.items() if n != PREFIX + 'python-package-files.json'}
        if full.get('version') != report['version'] or full.get('files') != actual:
            raise ValueError('Full package inventory mismatch')
        app = {n[len(PREFIX + 'app/'):]: h for n, h in hashes.items()
               if n.startswith(PREFIX + 'app/') and n != PREFIX + 'app/desk.json'}
        if p.value('source-inputs.json')['files'] != app or p.value('app/desk.json')['files'] != app:
            raise ValueError('Application/source input inventory mismatch')
        stock = p.value('code-upstream.json')
        raw = p.get(PREFIX + 'code-upstream.zip').data
        if stock['sha256'] != sha(raw):
            raise ValueError('Pristine upstream ZIP identity mismatch')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if len(z.namelist()) != len(set(z.namelist())) or set(z.namelist()) != set(stock['files']):
                raise ValueError('Pristine upstream member set mismatch')
            for name, row in stock['files'].items():
                if sha(z.read(name)) != row['sha256'] or mode(z.getinfo(name)) != row['mode']:
                    raise ValueError('Pristine upstream source/mode mismatch: ' + name)
        declaration = p.value('source-admission.json')
        view = declaration['view_files'] if declaration else stock['files']
        tree = _tree_inventory(p, PREFIX + 'tree/')
        if {n: {'sha256': sha(e.data), 'mode': e.mode} for n, e in tree.items()} != view:
            raise ValueError('Shipped source/custody view mismatch')
        if declaration and (declaration['target_version'] != report['version'] or declaration['upstream_sha256'] != sha(raw)):
            raise ValueError('Admission target identity mismatch')
        if p.value('app/component-downloads.json')['artifacts'] != report['optional_artifacts']:
            raise ValueError('Optional component identities changed')
        for name, row in report['replacements'].items():
            if hashes.get(name) != row['sha256'] or mode(reader.getinfo(name)) != row['mode']:
                raise ValueError('Frozen replacement mismatch: ' + name)
        if any(n in p.names() for n in report['removed']):
            raise ValueError('Removed member survives refresh')
        for carrier in ('python-app.json', 'helene-build.json', 'app/desk.json', 'source-inputs.json', 'code-upstream.json'):
            if p.value(carrier).get('version') != report['version']:
                raise ValueError('Version carrier mismatch: ' + carrier)
    return hashes


def build(donor, output, report, replacements, removed, *, expected_plan):
    donor, output = Path(donor), Path(output)
    frozen = {k: v for k, v in report.items() if k != 'sha256'}
    if (expected_plan != report['sha256'] or sha(encoded(frozen)) != expected_plan
            or digest(donor) != report['input_sha256']):
        raise ValueError('Frozen plan/donor changed')
    if any(p.exists() for p in (output, Path(str(output) + '.sha256'), Path(str(output) + '.artifact.json'))):
        raise FileExistsError('Existing candidate/receipt preserved: ' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < report['input_size'] + 16 * 1024 * 1024:
        raise ValueError('Insufficient disk headroom for one compressed candidate')
    fd, name = tempfile.mkstemp(prefix=output.name + '.', suffix='.partial', dir=output.parent)
    os.close(fd)
    temporary, installed = Path(name), []
    try:
        copied = copy_records(donor, temporary, replacements, removed)
        hashes = verify_archive(temporary, report)
        if record_hashes(temporary, set(copied)) != copied or digest(donor) != report['input_sha256']:
            raise ValueError('Unchanged compressed records/input changed')
        result = {'format': 'helene-portable-refresh-artifact.v1', 'version': report['version'],
            'platform': report['platform'], 'sha256': digest(temporary), 'size': temporary.stat().st_size,
            'plan_sha256': expected_plan, 'verified_members': len(hashes),
            'unchanged_local_records': len(copied), 'unchanged_local_records_sha256': sha(encoded(copied)),
            'native_qualified': False, 'runtime_changed': report['runtime_changed'], 'public_url': None}
        for path, data in ((Path(str(output) + '.sha256'), (result['sha256'] + '  ' + output.name + '\n').encode()),
                           (Path(str(output) + '.artifact.json'), encoded(result))):
            with path.open('xb') as stream:
                installed.append(path)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        with temporary.open('rb+') as stream:
            os.fsync(stream.fileno())
        os.link(temporary, output)  # exclusive installation, no overwrite race
        return result
    except BaseException:
        for path in installed:
            path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    frontend = commands.add_parser('build-frontends')
    frontend.add_argument('--source', type=Path, required=True)
    frontend.add_argument('--receipt', type=Path, required=True)
    refresh = commands.add_parser('refresh')
    for name in ('donor', 'source', 'frontend-receipt', 'verification-bundle'):
        refresh.add_argument('--' + name, type=Path, required=True)
    for name in ('version', 'platform', 'expected-sha256', 'verification-sha256'):
        refresh.add_argument('--' + name, required=True)
    refresh.add_argument('--legacy-admission', action='store_true')
    refresh.add_argument('--runtime-overlay', type=Path)
    refresh.add_argument('--runtime-overlay-sha256', default='')
    refresh.add_argument('--output', type=Path)
    refresh.add_argument('--expect-plan', default='')
    refresh.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.command == 'build-frontends':
        value = build_frontends(args.source, args.receipt)
        print(json.dumps({'sha256': digest(args.receipt), 'outputs': value['outputs']}))
        return
    report, replacements, removed = plan(args.donor, args.source, args.version, args.platform,
        expected_sha256=args.expected_sha256, frontend_receipt=args.frontend_receipt,
        verification_bundle=args.verification_bundle, verification_sha256=args.verification_sha256,
        legacy_admission=args.legacy_admission, runtime_overlay=args.runtime_overlay,
        runtime_overlay_sha256=args.runtime_overlay_sha256)
    value = build(args.donor, args.output, report, replacements, removed, expected_plan=args.expect_plan) if args.output else report
    if args.report:
        with args.report.open('xb') as stream:
            stream.write(encoded(value))
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    main()
