"""Durable byte-bound source reviews, executed by the existing Forge units.

Requests and author decisions live here; process admission, execution, events and
Stop remain Forge's. A review never gates application startup or package health.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid

TRIGGERS = ("after_update", "contested_overlay", "after_self_edit", "on_request")
DEPTHS = ("quick", "normal", "deep")
DEFAULTS = {"after_update": False, "after_self_edit": False, "depth": "normal",
            "models": [], "max_parallel": 1, "max_iters": 20}
TERMINAL = {"done", "error", "failed", "stalled", "stopped", "cancelled", "lost"}
LIVE = {"starting", "running", "finishing", "unknown"}
EXCLUDED = {"memory", "data", "workspace", "agents", "backups", "logs", "sessions",
            "local_auth", "credentials", "secrets", "soul", "runtime", "components",
            "node_modules", "target", "dist", "build", "__pycache__", "tree",
            ".git", ".venv", "venv", ".state", "_state", ".forge", ".self-review"}
PRIVATE_NAMES = {"agents.md", "claude.md", "identity.md", "scratchpad.md", "soul.md",
                 "helene.json", "llm.json", "auth.json", "settings.json", "local-build.json"}
EXTENSIONS = {".py", ".pyi", ".rs", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
              ".html", ".css", ".sql", ".sh", ".ps1", ".cmd", ".bat", ".toml",
              ".yml", ".yaml", ".md", ".c", ".h", ".cpp", ".go", ".swift"}
PUBLIC_JSON = {"package.json", "tsconfig.json", "desk.json", "update-inspection.json"}
SECRET = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH)|"
                    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})|"
                    r"(?im:^\s*(?:[\"']?(?:api_key|api_secret|access_token|refresh_token|password)"
                    r"[\"']?)\s*[:=]\s*[\"'][^\"'\n]{8,}[\"'])")
PUBLISHED_SECRET = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH)[^-]*-----\s+[A-Za-z0-9+/=\s]{80,}|"
                              r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{32,}")


def source_credential(text, *, published=False):
    # Distribution provenance disambiguates short test placeholders, not full
    # private-key material or token-shaped credentials accidentally published.
    return bool(SECRET.search(text) and (not published or PUBLISHED_SECRET.search(text)))
_LOCK = threading.RLock()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError("self-review record must be an object")
    return value


def _write(path, value):
    # A previous release's clean updater can import this incoming module while
    # its old package_notice is still cached. Only the incoming review process
    # writes review state, so resolve its writer when it is actually needed.
    if __package__:
        from .package_notice import replace_with_retry
    else:  # self_review_worker.py is also an executable adapter script
        from package_notice import replace_with_retry
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(_json(value))
            sink.flush()
            os.fsync(sink.fileno())
        replace_with_retry(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def settings(config=None):
    block = config or {}
    if not isinstance(block, dict):
        raise ValueError("self_review settings must be an object")
    if "self_review" in block:
        block = block["self_review"]
        if not isinstance(block, dict):
            raise ValueError("self_review settings must be an object")
    value = {**DEFAULTS, **block}
    for key in ("after_update", "after_self_edit"):
        if not isinstance(value[key], bool):
            raise ValueError(key + " must be a boolean")
    if value["depth"] not in DEPTHS:
        raise ValueError("depth must be quick, normal or deep")
    for key, maximum in (("max_parallel", 8), ("max_iters", 200)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ValueError(f"{key} must be between 1 and {maximum}")
    if (not isinstance(value["models"], list) or len(value["models"]) > 8
            or any(not isinstance(model, str) or not model.strip() or len(model) > 200
                   or "://" in model or "::" in model for model in value["models"])):
        raise ValueError("models must name models of the current voice route, not provider routes")
    value["models"] = list(dict.fromkeys(model.strip() for model in value["models"]))
    return value


def contested(overlay):
    if not isinstance(overlay, dict):
        return bool(overlay)
    return any(overlay.get(key) for key in
               ("merged", "conflicts", "no_base", "materials_error", "skipped_code", "skipped", "required_review"))


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _code_file(path):
    name = path.name.lower()
    return (name not in PRIVATE_NAMES and not name.startswith(".env")
            and not any(token in name for token in ("credential", "secret", "session", "token.json"))
            and (path.suffix.lower() in EXTENSIONS or name in PUBLIC_JSON
                 or name in {"dockerfile", "makefile", "cargo.lock", "uv.lock"}))


def _relative_code_name(value):
    raw = str(value)
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or ":" in raw or "\\" in raw:
        raise ValueError("side input must be relative to its declared material root")
    if any(part.startswith(".") or part.lower() in EXCLUDED or part.lower() in PRIVATE_NAMES for part in path.parts):
        raise ValueError("private side input omitted")
    return path


def _source_roots(sources, *, opaque_published=False, composition_sha256=''):
    result = {}
    for label, source in sources.items():
        if isinstance(source, dict):
            if 'merge_files' in source:
                if not isinstance(source['merge_files'], dict):
                    raise ValueError('merge projection requires the complete inventory')
                result[label] = {'root': str(Path(source['root']).absolute()),
                                 'merge_files': source['merge_files'],
                                 'published_files': source.get('published_files', {})}
                if source.get('inventory_scope'):
                    if label != 'harness' or source['inventory_scope'] not in {'python-context-v1', 'full-composed-app-v1'}:
                        raise ValueError('Only declared incoming/composed harness context may select an inventory scope')
                    result[label]['inventory_scope'] = source['inventory_scope']
                    evidence = source.get('release_evidence')
                    if evidence is not None:
                        hashes = ('composition_sha256', 'package_manifest_sha256', 'source_inputs_sha256')
                        if (source['inventory_scope'] != 'full-composed-app-v1' or not isinstance(evidence, dict)
                                or evidence.get('format') != 'helene-distribution-evidence.v1'
                                or any(not re.fullmatch(r'[0-9a-f]{64}', str(evidence.get(key, ''))) for key in hashes)
                                or evidence.get('package_manifest_ref') != 'distribution/python-package-files.json'
                                or evidence.get('source_inputs_ref') != 'distribution/source-inputs.json'):
                            raise ValueError('Invalid frozen distribution evidence')
                        result[label]['release_evidence'] = {key: evidence[key] for key in
                            ('format', *hashes, 'package_manifest_ref', 'source_inputs_ref')}
                        if opaque_published and evidence['composition_sha256'] == composition_sha256:
                            result[label]['opaque_published'] = True
                continue
            if not isinstance(source.get("files"), dict):
                raise ValueError("explicit side projection requires a files mapping")
            result[label] = {"root": str(Path(source["root"]).absolute()),
                             "files": {str(logical): str(actual) for logical, actual in source["files"].items()}}
            if label == 'distribution' and opaque_published:
                names = {'python-package-files.json', 'source-inputs.json'}
                hashes = source.get('published_files', {})
                if (result[label]['files'] != {name: name for name in names} or
                        any(not isinstance(hashes.get(name), list) or len(hashes[name]) != 1 or
                            not re.fullmatch(r'[0-9a-f]{64}', str(hashes[name][0])) for name in names)):
                    raise ValueError('Distribution context needs its two exact published manifest hashes')
                result[label]['distribution_files'] = {name: hashes[name][0] for name in names}
        else:
            result[label] = str(Path(source).absolute())
    harness = result.get('harness', {})
    if isinstance(harness, dict) and harness.get('opaque_published'):
        wanted = harness['release_evidence']
        distribution = result.get('distribution', {}).get('distribution_files', {})
        if distribution != {'python-package-files.json': wanted['package_manifest_sha256'],
                            'source-inputs.json': wanted['source_inputs_sha256']}:
            harness.pop('opaque_published')  # No resolvable frozen release proof: binaries stay gaps.
    return result


def _overlay_gaps(overlay, manifest):
    conflicts = overlay.get("conflicts")
    if not conflicts:
        return []
    labels = {}
    for row in manifest:
        label, logical = row["path"].split("/", 1)
        labels.setdefault(label, set()).add(logical)
    if all(label in labels for label in ("base", "mine", "release", "resolved")):
        sides = [labels[label] for label in ("base", "mine", "release", "resolved")]
        required = {name for name in conflicts if isinstance(name, str)} if isinstance(conflicts, list) else set()
        if all(paths == sides[0] for paths in sides) and required <= sides[0]:
            return []
    if isinstance(conflicts, list) and conflicts:
        complete = True
        for index in range(len(conflicts)):
            sides = [labels.get(f"{side}-{index}", set()) for side in ("base", "mine", "release", "resolved")]
            if not sides[0] or any(paths != sides[0] for paths in sides):
                complete = False
        if complete:
            return []
    return [{"path": "overlay", "reason": "conflicting overlay lacks matching frozen base/mine/release/resolved sides"}]


def _has_content(row):
    return row.get('exists', True) and not row.get('withheld') and not row.get('opaque')


def _merge_projection(label, source, destination, used):
    """Inventory-complete merge input, including absence and withheld-byte gaps.

    This is not the ordinary private-home collector. Callers supply a complete
    source_merge inventory; any changed bytes, binary/private input or budget
    shortfall stays an explicit gap and cannot produce a clear review.
    """
    from . import source_merge
    root = Path(source['root']).absolute()
    declared = source['merge_files']
    manifest, gaps = [], []
    full_app = label == 'harness' and source.get('inventory_scope') == 'full-composed-app-v1'
    if full_app:
        from .update_composition import app_inventory
        actual = app_inventory(root)
    else:
        actual = source_merge.inventory(root)
    if source.get('inventory_scope') == 'python-context-v1':
        if label != 'harness' or any(not name.endswith('.py') for name in declared):
            raise ValueError('Invalid Python harness context inventory')
        actual = {name: entry for name, entry in actual.items() if name.endswith('.py')}
    if actual != {name: entry for name, entry in declared.items() if entry is not None}:
        raise ValueError('Frozen merge inventory changed: ' + label)
    for name, entry in sorted(declared.items()):
        source_merge.relative(name)
        if not full_app and not source_merge.allowed(name):
            raise ValueError('Non-code path in merge material')
        logical = label + '/' + name
        if entry is None:
            manifest.append({'path': logical, 'exists': False, 'size': 0, 'sha256': None, 'mode': None})
            continue
        path = root / name
        raw = path.read_bytes()
        row = {'path': logical, 'exists': True, 'size': len(raw),
               'sha256': _sha(raw), 'mode': entry['mode']}
        public = row['sha256'] in source.get('published_files', {}).get(name, [])
        reason = ''
        try:
            factory_text = full_app and (name.startswith('resources/soul/') or
                                         name in {'resources/SOUL.md', 'resources/self-day-zero.md'})
            if not factory_text and any(part.lower() in PRIVATE_NAMES for part in Path(name).parts):
                reason = 'private input withheld; inventory retained'
            elif len(raw) > 16 * 1024 * 1024 or used + len(raw) > 64 * 1024 * 1024:
                reason = 'source exceeds projection byte budget'
            else:
                text = raw.decode('utf-8-sig')
                if '\x00' in text:
                    raise UnicodeError('binary input')
                # Exact A/B distribution bytes are already published source.
                # Test credential literals and public protocol constants must
                # not make every complete release impossible to review. This
                # exception never applies to a changed authored/candidate blob
                # or a private path, and does not affect ordinary home reviews.
                if source_credential(text, published=public):
                    reason = 'possible embedded credential; entire file withheld'
                elif SECRET.search(text):
                    row['published_source'] = True
        except UnicodeError:
            if (label == 'harness' and source.get('inventory_scope') == 'full-composed-app-v1'
                    and source.get('opaque_published') is True and public
                    and Path(name).suffix.lower() in {'.ico', '.png', '.jpg', '.jpeg', '.gif', '.webp',
                                                      '.woff', '.woff2', '.ttf', '.otf'}):
                # The strict coordinator binds this exact unchanged B digest.
                # Opaque means identity evidence, never synthetic text reading.
                row.update(opaque='unchanged published application asset; bytes not decoded',
                           published_source=True, distribution_path=name,
                           release_evidence=source['release_evidence'])
            else:
                reason = 'binary or non-UTF-8 input; inventory retained'
        if row['sha256'] != entry['sha256']:
            raise ValueError('Frozen merge bytes changed: ' + logical)
        if reason:
            row['withheld'] = reason
            gaps.append({'path': logical, 'reason': reason})
        else:
            used += len(raw)
            if destination is not None and not row.get('opaque'):
                target = Path(destination) / logical
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                if os.name != 'nt':
                    target.chmod(0o755 if entry['mode'] == '100755' else 0o644)
        manifest.append(row)
    return manifest, gaps, used


def _distribution_projection(source, destination):
    root = Path(source['root']).absolute()
    rows = []
    for name, digest in sorted(source['distribution_files'].items()):
        path = root / name
        if (_linked(root) or _linked(path) or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024):
            raise ValueError('Invalid frozen distribution manifest')
        raw = path.read_bytes()
        if _sha(raw) != digest:
            raise ValueError('Frozen distribution manifest changed: ' + name)
        text = raw.decode('utf-8-sig')
        if SECRET.search(text) or not isinstance(json.loads(text), dict):
            raise ValueError('Private or invalid distribution manifest')
        logical = 'distribution/' + name
        if destination is not None:
            target = Path(destination) / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        rows.append({'path': logical, 'size': len(raw), 'sha256': digest})
    return rows


def collect(sources, destination=None):
    manifest, gaps = [], []
    total = 0
    for label, raw_root in sorted(sources.items()):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", label):
            raise ValueError("invalid logical source name")
        if label == 'distribution' and isinstance(raw_root, dict) and raw_root.get('distribution_files'):
            rows = _distribution_projection(raw_root, destination)
            manifest.extend(rows)
            total += sum(row['size'] for row in rows)
            continue
        if isinstance(raw_root, dict) and 'merge_files' in raw_root:
            rows, missing, total = _merge_projection(label, raw_root, destination, total)
            manifest.extend(rows)
            gaps.extend(missing)
            continue
        explicit = raw_root.get("files") if isinstance(raw_root, dict) else None
        root = Path(raw_root["root"] if isinstance(raw_root, dict) else raw_root).absolute()
        before_count = len(manifest)
        if not root.is_dir() or _linked(root):
            gaps.append({"path": label, "reason": "source root unavailable or linked"})
            continue
        aliases = {}
        if explicit is not None:
            for logical, actual in explicit.items():
                logical_path, actual_path = _relative_code_name(logical), _relative_code_name(actual)
                actual_program = actual_path.with_suffix("") if actual_path.suffix in {".base", ".mine", ".theirs", ".merged", ".resolved"} else actual_path
                if not _code_file(logical_path) or not _code_file(actual_program):
                    gaps.append({"path": label + "/" + logical, "reason": "side is not a declared program input"})
                    continue
                path = root / actual_path
                if path in aliases:
                    raise ValueError("side input has duplicate logical identities")
                aliases[path] = logical_path
            pending = [None]
        else:
            pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = sorted(aliases if directory is None else directory.iterdir(), key=lambda entry: entry.name)
            except OSError:
                gaps.append({"path": label + "/" + directory.relative_to(root).as_posix(),
                             "reason": "directory unreadable"})
                continue
            for path in entries:
                relative = aliases.get(path, path.relative_to(root))
                logical = label + "/" + relative.as_posix()
                if path.name.lower() in EXCLUDED or path.name.lower() in PRIVATE_NAMES or path.name.startswith("."):
                    continue
                try:
                    if any(_linked(parent) for parent in (path, *path.parents) if parent == root or root in parent.parents):
                        gaps.append({"path": logical, "reason": "link or reparse point omitted"})
                    elif path.is_dir() and explicit is None:
                        pending.append(path)
                    elif _code_file(relative):
                        info = path.stat()
                        if not stat.S_ISREG(info.st_mode):
                            gaps.append({"path": logical, "reason": "not a regular file"})
                            continue
                        if info.st_size > 16 * 1024 * 1024 or total + info.st_size > 64 * 1024 * 1024:
                            gaps.append({"path": logical, "reason": "source exceeds projection byte budget"})
                            continue
                        raw = path.read_bytes()
                        text = raw.decode("utf-8-sig")
                        if "\x00" in text:
                            raise UnicodeError("binary input")
                        if SECRET.search(text):
                            gaps.append({"path": logical, "reason": "possible embedded credential; entire file withheld"})
                            continue
                        manifest.append({"path": logical, "sha256": _sha(raw), "size": len(raw)})
                        total += len(raw)
                        if destination is not None:
                            target = Path(destination) / logical
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(raw)
                except (OSError, UnicodeError):
                    gaps.append({"path": logical, "reason": "source unreadable or not UTF-8 text"})
        if len(manifest) == before_count:
            gaps.append({"path": label, "reason": "no program inputs captured"})
    return sorted(manifest, key=lambda row: row["path"]), sorted(gaps, key=lambda row: row["path"])


def validate_snapshot(root, expected_sha256):
    root = Path(root)
    subject_path = root / "subject.json"
    if _linked(root) or _linked(subject_path):
        raise ValueError("linked self-review subject")
    raw = subject_path.read_bytes()
    if _sha(raw) != expected_sha256:
        raise ValueError("self-review subject identity changed")
    subject = json.loads(raw)
    wanted = {"subject.json"}
    for row in subject["manifest"]:
        logical = row["path"]
        relative = Path(logical)
        if relative.is_absolute() or ".." in relative.parts or ":" in logical or "\\" in logical:
            raise ValueError("invalid snapshot path")
        path = root / relative
        if not _has_content(row):
            if path.exists() or path.is_symlink():
                raise ValueError('Unexpected bytes for absent/withheld snapshot input: ' + logical)
            continue
        if any(_linked(parent) for parent in (path, *path.parents) if parent != root.parent and root in (parent, *parent.parents)):
            raise ValueError("linked snapshot input")
        raw = path.read_bytes()
        if len(raw) != row["size"] or _sha(raw) != row["sha256"]:
            raise ValueError("snapshot input changed: " + logical)
        wanted.add(logical)
    present = set()
    for directory, names, files in os.walk(root, followlinks=False):
        for name in names + files:
            path = Path(directory) / name
            if _linked(path):
                raise ValueError("linked snapshot entry")
        present.update((Path(directory) / name).relative_to(root).as_posix() for name in files)
    if present != wanted:
        raise ValueError("snapshot contains unexpected or missing inputs")
    return subject


def _parse_verdict(raw, subject_sha256):
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        return {"verdict": "insufficient", "reason": "critic did not return the structured verdict"}
    if (not isinstance(result, dict) or result.get("subject_sha256") != subject_sha256
            or result.get("verdict") not in {"clear", "findings", "insufficient"}
            or not isinstance(result.get("summary"), str) or not result["summary"].strip()
            or not isinstance(result.get("findings"), list) or not isinstance(result.get("gaps"), list)):
        return {"verdict": "insufficient", "reason": "malformed or unbound critic verdict"}
    for finding in result["findings"]:
        if not isinstance(finding, dict) or not all(isinstance(finding.get(key), str) and finding[key].strip()
                                                  for key in ("path", "scenario", "detail")):
            return {"verdict": "insufficient", "reason": "finding lacks path, scenario or detail"}
    if result["verdict"] == "clear" and (result["findings"] or result["gaps"]):
        return {**result, "verdict": "insufficient", "reason": "clear verdict includes findings or gaps"}
    if result["verdict"] == "findings" and not result["findings"]:
        return {**result, "verdict": "insufficient", "reason": "empty findings verdict"}
    return result


def _validate_material_text(intent, criteria):
    if not isinstance(intent, str) or not isinstance(criteria, list) or any(not isinstance(item, str) for item in criteria):
        raise ValueError("review intent and criteria must be text")
    if SECRET.search(intent) or any(SECRET.search(item) for item in criteria):
        raise ValueError("review intent or criteria contains a possible credential")


def _critics(policy):
    """Same configured voice models/Forge authority; author chooses ordinary count."""
    models = policy['models'] or ['']
    count = policy.get('critic_count') or (2 if policy['depth'] == 'deep' and len(models) == 1 else len(models))
    if type(count) is not int or not 1 <= count <= 8:
        raise ValueError('critic count must be 1..8')
    return [{'unit_id': 'agent-' + uuid.uuid4().hex[:8], 'model': models[index % len(models)],
             'focus': 'integration' if index == 0 else 'adversarial', 'execution': 'queued'}
            for index in range(count)]


class Service:
    def __init__(self, tree, code_dir, app_root, forge, config=None, *, coordinator=None):
        self.tree, self.code_dir, self.app_root = map(Path, (tree, code_dir, app_root))
        self.forge = forge
        self.config = settings(config)
        self.coordinator = coordinator
        # Old211 maintenance ignores coordinator identity and scans every record
        # directly under .self-review. A field fence alone cannot stop that old
        # process corrupting a new snapshot while its window remains open.
        isolated = isinstance(coordinator, dict) and coordinator.get('review_store') == 'isolated-v1'
        self.root = self.tree / "memory" / ('.source-update-reviews' if isolated else '.self-review')
        self._terminal_cache = {}

    def settings(self, config=None):
        if config is not None:
            self.config = settings(config)
        return dict(self.config)

    def _path(self, review_id):
        if not re.fullmatch(r"review-[0-9a-f]{16}", str(review_id)):
            raise ValueError("invalid review id")
        return self.root / review_id / "request.json"

    def status(self, review_id=""):
        if review_id:
            return _read(self._path(review_id)) or {}
        if not self.root.is_dir():
            return []
        import copy
        rows = []
        present = set()
        for path in self.root.glob("review-*/request.json"):
            present.add(path)
            info = path.stat()
            signature = (info.st_mtime_ns, info.st_size)
            cached = self._terminal_cache.get(path)
            if cached and cached[0] == signature:
                row = copy.deepcopy(cached[1])
            else:
                row = _read(path)
                # Cache only history with no remaining execution/cancel duty.
                # Reads still stat the exact file; a new disposition invalidates it.
                if row.get('execution') not in {'queued','running','unknown'} and (
                        not row.get('cancel_requested') or all(c.get('cancel_confirmed') for c in row.get('critics',[]))):
                    self._terminal_cache[path] = signature, copy.deepcopy(row)
                else:
                    self._terminal_cache.pop(path, None)
            rows.append(row)
        for path in self._terminal_cache.keys() - present:
            del self._terminal_cache[path]
        return sorted(rows, key=lambda row: row["created"], reverse=True)

    def request(self, trigger="on_request", operation_id="", depth=None, intent="", criteria=None,
                overlay=None, sources=None, retry=False, subject=None, critics=None):
        if trigger not in TRIGGERS:
            raise ValueError("unknown self-review trigger")
        if critics is not None and (type(critics) is not int or not 1 <= critics <= 8 or
                                   trigger != 'on_request' or self.coordinator is not None or overlay):
            raise ValueError('critics must be 1..8 for an explicit ordinary review request')
        if subject is not None:
            if trigger != "on_request" or overlay or sources or self.coordinator is not None or intent or criteria:
                raise ValueError("typed subjects are explicit ordinary requests, not source-update reviews")
            return self._request_typed(subject, operation_id, depth, retry, critics)
        criteria = criteria or []
        _validate_material_text(intent, criteria)
        overlay = overlay or {}
        if not isinstance(overlay, dict):
            raise ValueError("overlay must be an object")
        if overlay.get("skipped"):
            overlay = {**overlay, "skipped_code": overlay["skipped"]}
        required = trigger == "contested_overlay" or contested(overlay)
        if required:
            trigger = "contested_overlay"
        if trigger in {"after_update", "after_self_edit"} and not self.config[trigger]:
            return {"state": "disabled", "trigger": trigger, "required": False}
        selected_depth = depth or self.config["depth"]
        if selected_depth not in DEPTHS:
            raise ValueError("unknown self-review depth")
        merge_identity = overlay.get('merge_identity') or {}
        composition_sha256 = str(merge_identity.get('composition_sha256', '')) if isinstance(merge_identity, dict) else ''
        opaque_published = (required and isinstance(self.coordinator, dict)
                            and self.coordinator.get('kind') == 'incoming-update-runtime.v1'
                            and bool(re.fullmatch(r'[0-9a-f]{64}', composition_sha256)))
        source_roots = _source_roots(sources or {"agent": self.code_dir,
                                  "app": self.app_root / "app" if (self.app_root / "app").is_dir() else self.app_root},
                                    opaque_published=opaque_published, composition_sha256=composition_sha256)
        policy = {**self.config, "depth": selected_depth}
        if critics is not None:
            policy['critic_count'] = critics
        with _LOCK:
            if operation_id:
                for prior in self.status():
                    if str(operation_id) in prior["operations"] and prior.get('coordinator') == self.coordinator:
                        if prior.get('subject_kind', 'source') != 'source':
                            raise ValueError('operation already belongs to a different review subject')
                        prior["required"] = prior["required"] or required
                        prior["triggers"] = list(dict.fromkeys(prior["triggers"] + [trigger]))
                        _write(self._path(prior["id"]), prior)
                        return prior
            self.root.mkdir(parents=True, exist_ok=True)
            review_id = "review-" + uuid.uuid4().hex[:16]
            folder = self.root / review_id
            snapshot = folder / "subject"
            snapshot.mkdir(parents=True)
            manifest, gaps = collect(source_roots, snapshot)
            for key in ("no_base", "materials_error", "skipped_code"):
                if overlay.get(key):
                    gaps.append({"path": "overlay", "reason": key + ": overlay evidence incomplete"})
            gaps.extend(_overlay_gaps(overlay, manifest))
            gaps.sort(key=lambda gap: (gap["path"], gap["reason"]))
            earlier = self.status()
            from .self_review_merge import overview, index_text
            merge_review = overview(manifest, overlay)
            # A source update has its own A/L/B/M baseline. Ordinary historical
            # reviews often have different labels/roots and would manufacture
            # whole-tree additions/deletions unrelated to the actual update.
            previous = next((row for row in earlier if row.get("subject_sha256")
                             and row.get('sources') == source_roots
                             and row.get('coordinator') == self.coordinator), None)
            changes = []
            if merge_review is not None:
                changes.append(index_text(merge_review))
            elif previous:
                old_root = Path(previous["snapshot"])
                try:
                    old = validate_snapshot(old_root, previous["subject_sha256"])
                    old_files = {row["path"]: row for row in old["manifest"] if row["path"] != "changes.diff"}
                    new_files = {row["path"]: row for row in manifest}
                    for logical in sorted(set(old_files) | set(new_files)):
                        if old_files.get(logical) == new_files.get(logical):
                            continue
                        before = ((old_root / logical).read_text(encoding="utf-8-sig").splitlines(True)
                                  if logical in old_files and _has_content(old_files[logical]) else [])
                        after = ((snapshot / logical).read_text(encoding="utf-8-sig").splitlines(True)
                                 if logical in new_files and _has_content(new_files[logical]) else [])
                        changes.extend(difflib.unified_diff(before, after, fromfile="before/" + logical,
                                                            tofile="after/" + logical))
                except (OSError, ValueError):
                    gaps.append({"path": "changes.diff", "reason": "previous frozen subject unavailable"})
            else:
                changes.append("No prior frozen subject: inspect the complete source projection; no baseline diff is claimed.\n")
            diff = "".join(changes).encode("utf-8")
            (snapshot / "changes.diff").write_bytes(diff)
            manifest.append({"path": "changes.diff", "size": len(diff), "sha256": _sha(diff)})
            subject = {"schema": "helene.self-review.subject.v1", "manifest": manifest, "gaps": gaps,
                       "intent": str(intent), "criteria": list(criteria or []),
                       "overlay": {key: bool(overlay.get(key)) for key in
                                   ("merged", "conflicts", "no_base", "materials_error", "skipped_code")},
                       "scope": "source-only; memory/config/credentials excluded; code is never executed"}
            if overlay.get('merge_identity'):
                subject['merge_identity'] = overlay['merge_identity']
            if merge_review is not None:
                from .self_review_merge import coverage_units
                merge_review['coverage_units'] = coverage_units(merge_review, snapshot)
                subject['merge_review'] = merge_review
            raw = _json(subject)
            subject_sha = _sha(raw)
            code_identity = _sha(_json({"manifest": [row for row in manifest if row["path"] != "changes.diff"],
                                       "gaps": gaps, "intent": subject["intent"], "criteria": subject["criteria"],
                                       "overlay": subject["overlay"], "policy": policy,
                                       "sources": source_roots, "coordinator": self.coordinator}))
            for prior in earlier:
                if prior.get("identity") == code_identity:
                    if retry:
                        if (trigger != "on_request" or prior["execution"] in {"queued", "running", "unknown"}
                                or (prior.get("cancel_requested") and not all(
                                    c.get("cancel_confirmed") for c in prior["critics"]))):
                            raise ValueError("retry requires an explicit request and a settled previous attempt")
                        continue
                    prior["required"] = prior["required"] or required
                    prior["triggers"] = list(dict.fromkeys(prior["triggers"] + [trigger]))
                    prior["operations"] = list(dict.fromkeys(prior["operations"] + ([str(operation_id)] if operation_id else [])))
                    _write(self._path(prior["id"]), prior)
                    shutil.rmtree(folder)
                    return prior
            (snapshot / "subject.json").write_bytes(raw)
            critics = _critics(policy)
            if merge_review is not None:
                critics = [{**critic, 'unit_id': 'agent-' + uuid.uuid4().hex[:8], 'coverage': unit}
                           for unit in merge_review['coverage_units'] for critic in critics]
            record = {"schema": "helene.self-review.request.v1", "id": review_id,
                      "created": time.time(), "trigger": trigger, "triggers": [trigger],
                      "operations": [str(operation_id)] if operation_id else [], "required": required,
                      "policy": policy, "identity": code_identity, "subject_sha256": subject_sha,
                      "snapshot": str(snapshot.absolute()),
                      "sources": source_roots, "coordinator": self.coordinator,
                      "task_id": "code-" + uuid.uuid4().hex[:8], "critics": critics,
                      "execution": "queued", "state": "pending", "verdict": "insufficient",
                      "dispositions": [], "gaps": gaps}
            _write(self._path(review_id), record)
            return record

    def _request_typed(self, subject, operation_id, depth, retry, critics):
        from . import self_review_subjects as typed
        selected_depth = depth or self.config['depth']
        if selected_depth not in DEPTHS:
            raise ValueError('unknown self-review depth')
        policy = {**self.config, 'depth': selected_depth}
        if critics is not None:
            policy['critic_count'] = critics
        # Verify hashes even for retries: never silently select old material.
        value = typed.capture(subject, typed.roots(self))
        identity = _sha(_json({'subject': value, 'policy': policy}))
        with _LOCK:
            earlier = self.status()
            for prior in earlier:
                same_operation = bool(operation_id) and str(operation_id) in prior['operations']
                if same_operation and prior.get('identity') != identity:
                    raise ValueError('operation already belongs to a different review subject')
                if prior.get('identity') != identity:
                    continue
                if retry:
                    if (prior['execution'] in {'queued', 'running', 'unknown'} or
                            (prior.get('cancel_requested') and not all(c.get('cancel_confirmed') for c in prior['critics']))):
                        raise ValueError('retry requires a settled previous attempt')
                    if not same_operation:
                        continue
                    raise ValueError('retry needs a new operation id')
                prior['operations'] = list(dict.fromkeys(prior['operations'] + ([str(operation_id)] if operation_id else [])))
                _write(self._path(prior['id']), prior)
                return prior
            review_id = 'review-' + uuid.uuid4().hex[:16]
            snapshot = self.root / review_id / 'subject'
            snapshot.mkdir(parents=True)
            try:
                captured = typed.capture(subject, typed.roots(self), snapshot)
                if captured != value:
                    raise ValueError('typed material changed during capture')
                raw = _json(captured)
                (snapshot / 'subject.json').write_bytes(raw)
            except Exception:
                shutil.rmtree(snapshot.parent)
                raise
            critics = _critics(policy)
            record = {'schema': 'helene.self-review.request.v1', 'id': review_id, 'created': time.time(),
                      'subject_kind': value['subject_kind'], 'audience': 'owner',
                      'trigger': 'on_request', 'triggers': ['on_request'], 'required': False,
                      'operations': [str(operation_id)] if operation_id else [],
                      'policy': policy, 'identity': identity, 'subject_sha256': _sha(raw),
                      'snapshot': str(snapshot.absolute()), 'sources': {}, 'coordinator': None,
                      'task_id': 'code-' + uuid.uuid4().hex[:8], 'critics': critics,
                      'execution': 'queued', 'state': 'pending', 'verdict': 'insufficient',
                      'dispositions': [], 'gaps': []}
            _write(self._path(review_id), record)
            return record

    def _brief(self, record, critic):
        if record.get('subject_kind') in ('plan', 'change', 'task_result'):
            from .self_review_subjects import brief
            return brief(record)
        merge_subject = all(isinstance(record.get('sources', {}).get(side), dict)
                            and 'merge_files' in record['sources'][side]
                            for side in ('base', 'mine', 'release', 'resolved'))
        navigation = ("Start with inspect status and changes.diff (the compact A/L/B/M index). "
                      "Use inspect diff with path and left/right sides for each changed/conflicted file; "
                      "page all relevant hunks, then inspect readers/writers and tests. "
                      "Preexisting local-only deletions are context, not newly introduced deletions. "
                      if merge_subject else "Read subject.json, changes.diff and relevant source. ")
        return (f"Review subject {record['subject_sha256']}. Depth={record['policy']['depth']}; "
                f"focus={critic['focus']}. " +
                ("This panel partitions mandatory coverage. Your exclusive assignment is " +
                 json.dumps(critic['coverage'], ensure_ascii=False) +
                 ". Fully review these paths, all three A->L/A->B/L->M comparisons and their dependencies/tests. "
                 "Other units review other paths. Do not report their unassigned coverage as your gap; "
                 "do report any actual coupling defect you find. Full snapshot context remains available. "
                 if critic.get('coverage') else "") + navigation +
                ("Read battery/report.md and the excerpts relevant to your assigned seams. "
                 "Use failures/skips as attention cues, investigate their cause and relevance; "
                 "do not mechanically map test status to your verdict or treat green tests as proof. "
                 if 'battery' in record.get('sources', {}) else "") +
                ("Unchanged published binary app assets may be declared opaque by exact frozen-distribution digest. "
                 "Inspect their metadata/locator and coupling; they have NOT been decoded or read as source. "
                 "Changed/unknown binary material stays a gap. Report insufficient when identity evidence cannot answer your question. "
                 if any(isinstance(source, dict) and source.get('opaque_published') for source in record.get('sources', {}).values()) else "") +
                "quick: changed files and concrete regressions; normal: also readers/writers and tests; "
                "deep: also integration and adversarial failure paths. No execution or live memory. "
                "Return ONLY JSON: {subject_sha256, verdict: clear|findings|insufficient, summary, "
                "findings:[{path,scenario,detail}], gaps:[]}. Missing evidence is insufficient, not clear. "
                "Source-only clear is not installed/runtime verification.")

    def tick(self, paused=False, *, review_ids=None):
        with _LOCK:
            changed = []
            for record in self.status():
                if record.get('coordinator') != self.coordinator:
                    continue
                if review_ids is not None and record['id'] not in review_ids:
                    continue
                if record.get("cancel_requested"):
                    if not all(c.get("cancel_confirmed") for c in record["critics"]):
                        self._reconcile_cancel(record)
                        changed.append(record)
                    continue
                if paused:
                    continue
                if record["execution"] not in {"queued", "running", "unknown"}:
                    continue
                try:
                    validate_snapshot(record["snapshot"], record["subject_sha256"])
                except (OSError, ValueError, KeyError) as exc:
                    record.update(execution="error", state="needs_attention", verdict="insufficient",
                                  error="snapshot integrity: " + str(exc))
                    _write(self._path(record["id"]), record)
                    changed.append(record)
                    continue
                if getattr(self.forge, "SELF_REVIEW_API", 0) != 1:
                    record.update(execution="unavailable", state="needs_attention", verdict="insufficient",
                                  error="executing preserved Forge lacks self-review adapter API v1")
                    _write(self._path(record["id"]), record)
                    changed.append(record)
                    continue
                try:
                    self.forge.self_review_task(record["task_id"], record["snapshot"],
                                                record["subject_sha256"],
                                                str(Path(__file__).with_name("self_review_worker.py")),
                                                record["policy"]["max_parallel"])
                    drain = getattr(self.forge, 'tick_queued', None)
                    if callable(drain):
                        drain(record['task_id'])
                    for critic in record["critics"]:
                        directory = self.forge._unit_dir(record["task_id"], "agents", critic["unit_id"])
                        if not directory.exists() and critic["execution"] == "queued":
                            critic["admission"] = self.forge.agent(record["task_id"], "spawn",
                                agent_id=critic["unit_id"], role="reviewer", brief=self._brief(record, critic),
                                model=critic["model"], max_iters=record["policy"]["max_iters"])
                            if not directory.exists():
                                if str(critic["admission"]).startswith("Active worker slots exhausted"):
                                    continue
                                critic.update(execution="unavailable", verdict="insufficient")
                                continue
                        if not directory.exists():
                            critic.update(execution="unknown", verdict="insufficient")
                            continue
                        result_path = directory / "result.json"
                        if result_path.is_file():
                            raw = result_path.read_bytes()
                            result = json.loads(raw)
                            unit_state = self.forge._unit_state(directory)
                            critic.update(execution=str(unit_state.get("status") or "unknown"),
                                          result_path=str(result_path), result_sha256=_sha(raw),
                                          model_actual=str(result.get("model") or ""))
                            if (critic['execution'] == 'done' and result.get("status") == "done"
                                    and result.get("complete") is True):
                                if result.get("subject_sha256") == record["subject_sha256"] and result.get("snapshot_verified") is True:
                                    critic.update(_parse_verdict(result.get("result"), record["subject_sha256"]))
                                    if result.get("tool_errors"):
                                        critic.update(verdict="insufficient", reason="inspection tools failed; retained findings require attention")
                                else:
                                    critic.update(verdict="insufficient", reason="worker result is not bound to verified snapshot")
                            else:
                                critic.update(verdict="insufficient", reason="critic execution incomplete or inspection tools failed")
                        else:
                            critic["execution"] = self.forge._unit_state(directory).get("status") or "unknown"
                    self._aggregate(record)
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    record.update(execution="error", state="needs_attention", verdict="insufficient",
                                  error=f"{type(exc).__name__}: {exc}")
                _write(self._path(record["id"]), record)
                changed.append(record)
            return changed

    def _aggregate(self, record):
        critics = record["critics"]
        if any(critic["execution"] in {"queued", *LIVE} for critic in critics):
            record.update(execution="running", state="pending", verdict="insufficient")
            return
        verdicts = [critic.get("verdict", "insufficient") for critic in critics]
        verdict = ("findings" if "findings" in verdicts or any(c.get("findings") for c in critics) else
                   "clear" if verdicts and all(value == "clear" for value in verdicts) and not record["gaps"]
                   else "insufficient")
        record.update(execution="done" if all(critic["execution"] == "done" for critic in critics) else "error",
                      state="reviewed" if verdict == "clear" else "needs_attention", verdict=verdict)

    def freshness(self, review_id):
        record = self.status(review_id)
        frozen = validate_snapshot(record["snapshot"], record["subject_sha256"])
        if record.get('subject_kind') in ('plan', 'change', 'task_result'):
            from .self_review_subjects import stale
            return {'subject_sha256': record['subject_sha256'], 'stale': stale(self, frozen)}
        current, gaps = collect(record["sources"])
        subject = _read(Path(record["snapshot"]) / "subject.json")
        original = [row for row in subject["manifest"] if row["path"] != "changes.diff"]
        source_gaps = [gap for gap in subject["gaps"] if gap["path"] not in {"overlay", "changes.diff"}]
        return {"subject_sha256": record["subject_sha256"], "stale": current != original or gaps != source_gaps}

    def disposition(self, review_id, decision, reason, expected_subject_sha256):
        if decision not in {"accepted", "fix", "deferred", "continue-with-reason"} or not str(reason).strip():
            raise ValueError("explicit disposition and reason required")
        with _LOCK:
            record = self.status(review_id)
            if record["subject_sha256"] != expected_subject_sha256:
                raise ValueError("review subject changed")
            for critic in record["critics"]:
                if critic.get("result_sha256") and _sha(Path(critic["result_path"]).read_bytes()) != critic["result_sha256"]:
                    raise ValueError("canonical critic result changed; previous observation retained")
            if decision == "accepted" and (record["execution"] != "done" or record["verdict"] != "clear"):
                raise ValueError("accepted requires a clear completed review; use continue-with-reason for an explicit override")
            freshness = self.freshness(review_id)
            if freshness["stale"] and decision == "accepted":
                raise ValueError("current code differs from reviewed bytes")
            entry = {"at": time.time(), "decision": decision, "reason": str(reason).strip(), **freshness}
            record["dispositions"].append(entry)
            _write(self._path(review_id), record)
            return record

    def cancel(self, review_id):
        with _LOCK:
            record = self.status(review_id)
            # Intent is durable before any Stop effect, never a claim of exit.
            record.update(cancel_requested=True, execution="unknown", state="needs_attention")
            _write(self._path(review_id), record)
            return self._reconcile_cancel(record)

    def _reconcile_cancel(self, record):
        """Stop existing units only; retry unresolved custody even while paused."""
        for critic in record["critics"]:
            if critic.get("cancel_confirmed"):
                continue
            critic.update(execution="unknown", cancel_confirmed=False)
            try:
                directory = self.forge._unit_dir(record["task_id"], "agents", critic["unit_id"])
                try:
                    directory.stat()
                except FileNotFoundError:
                    # A positively absent, never-admitted queued unit has no
                    # process to stop. Loss of an admitted unit stays unknown.
                    if "admission" not in critic:
                        critic.update(execution="cancelled", cancel_confirmed=True)
                    continue
                receipt = _read(directory / "stop.json") or {}
                if receipt.get("state") != "settled":
                    critic["stop_receipt"] = self.forge.agent(record["task_id"], "stop", agent_id=critic["unit_id"])
                    receipt = _read(directory / "stop.json") or {}
                confirmed = receipt.get("state") == "settled"
                critic.update(execution="cancelled" if confirmed else "unknown", cancel_confirmed=confirmed)
                critic.pop("stop_error", None)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                critic["stop_error"] = f"{type(exc).__name__}: {exc}"
        record["execution"] = "cancelled" if all(c.get("cancel_confirmed") for c in record["critics"]) else "unknown"
        _write(self._path(record["id"]), record)
        return record
