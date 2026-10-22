"""Offline immutable evidence acceptance. No engine, API startup or SQLite writes."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
try:
    import fcntl
except ImportError:
    fcntl = None
from .evaluation_cache import safe_path, CacheUnavailable
from .evaluation_models import canonical_json, digest
from .reasoning_models import Bundle, Envelope, Acceptance, validate_bundle

MAX_FILE = 2*1024**2
MAX_BUNDLES = 20
MAX_TOTAL = 40*1024**2
ID = re.compile(r'^[a-f0-9]{64}$')


class EvidenceUnavailable(ValueError):
    pass


def safe_artifact_path(path):
    if not path:
        raise EvidenceUnavailable('ARTIFACT_PATH_UNCONFIGURED')
    raw = Path(path).absolute()
    try:
        # SQLite's existing guard intentionally requires nlink=1 for files.
        # A real directory normally has nlink>=2; check its components via a
        # non-created child instead, retaining the file guard for all payloads.
        if raw.is_dir():
            safe_path(raw/'.reasoning-location-check')
            result = raw.resolve()
        else:
            result = safe_path(raw)
    except CacheUnavailable:
        raise EvidenceUnavailable('UNSAFE_ARTIFACT_PATH') from None
    root = Path(__file__).resolve().parents[1]
    if result == root or any(folder == result or folder in result.parents for folder in
                             (root/'.git', root/'src', root/'api', root/'tests', root/'web', root/'plan_report')):
        raise EvidenceUnavailable('PROTECTED_ARTIFACT_PATH')
    for key in ('CHESS_COACH_PRACTICE_DB', 'CHESS_COACH_EVAL_CACHE_DB', 'CHESS_COACH_PROGRESSIVE_DB'):
        if os.environ.get(key):
            p = Path(os.environ[key]).resolve()
            if result == p or str(result) in {str(p)+s for s in ('-wal', '-shm', '-journal')}:
                raise EvidenceUnavailable('STORE_ALIAS_FORBIDDEN')
    return result


def bounded_read(path, limit=MAX_FILE):
    path = safe_artifact_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        raise EvidenceUnavailable('EVIDENCE_UNAVAILABLE') from None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise EvidenceUnavailable('UNSAFE_OR_OVERSIZED_EVIDENCE')
        data = stream.read(limit+1)
    if len(data) > limit:
        raise EvidenceUnavailable('EVIDENCE_TOO_LARGE')
    return data


def decode_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('DUPLICATE_JSON_FIELD')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('NONFINITE_JSON_NUMBER')
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)


def load_input(path):
    bundle = Bundle.model_validate(decode_json(bounded_read(path)))
    return validate_bundle(bundle)


class ArtifactStore:
    """Readers never create the directory, lock, catalog or acceptance receipt."""
    def __init__(self, directory):
        if not directory:
            raise EvidenceUnavailable('ARTIFACT_DIRECTORY_UNCONFIGURED')
        self.directory = safe_artifact_path(directory)

    def catalog(self):
        value = decode_json(bounded_read(self.directory/'catalog.json', 32768))
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'entries'} or value['schema_version'] != 'reasoning-catalog/1'
            or not isinstance(value['entries'], list) or len(value['entries']) > MAX_BUNDLES):
            raise EvidenceUnavailable('CATALOG_INCOMPATIBLE')
        ids = set()
        for entry in value['entries']:
            if (not isinstance(entry, dict) or set(entry) != {'id', 'context_id', 'origin', 'bytes', 'profiles'} or
                not isinstance(entry['id'], str) or not isinstance(entry['context_id'], str) or
                not ID.fullmatch(entry['id']) or not ID.fullmatch(entry['context_id']) or entry['id'] in ids or
                entry['origin'] not in ('EXTERNAL_ATTESTED', 'SYNTHETIC_TEST') or
                type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_FILE or
                not isinstance(entry['profiles'], list) or len(entry['profiles']) > 5 or
                any(not isinstance(p, str) or not ID.fullmatch(p) for p in entry['profiles'])):
                raise EvidenceUnavailable('CATALOG_INCOMPATIBLE')
            ids.add(entry['id'])
        return value

    def load(self, bundle_id, budget=None):
        if not ID.fullmatch(bundle_id):
            raise ValueError('INVALID_BUNDLE_ID')
        entry = next((x for x in self.catalog()['entries'] if x['id'] == bundle_id), None)
        if entry is None:
            raise EvidenceUnavailable('BUNDLE_NOT_IN_CATALOG')
        payload = bounded_read(self.directory/(bundle_id+'.json'))
        envelope = Envelope.model_validate(decode_json(payload))
        if envelope.acceptance.payload_hash != bundle_id or digest(envelope.bundle.root.identity()) != entry['context_id']:
            raise EvidenceUnavailable('CATALOG_CONTENT_MISMATCH')
        if len(payload) != entry['bytes'] or sorted({digest(r.settings.model_dump()) for r in envelope.bundle.records}) != entry['profiles']:
            raise EvidenceUnavailable('CATALOG_METADATA_MISMATCH')
        if envelope.acceptance.origin != entry['origin']:
            raise EvidenceUnavailable('CATALOG_ORIGIN_MISMATCH')
        validate_bundle(envelope.bundle, budget)
        return envelope

    @contextmanager
    def lock(self):
        if fcntl is None:
            raise EvidenceUnavailable('IMPORT_UNSUPPORTED_PLATFORM')
        safe_artifact_path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.directory/'.import.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            info = os.fstat(fd)
            if info.st_nlink != 1 or info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                raise EvidenceUnavailable('UNSAFE_IMPORT_LOCK')
            deadline = time.monotonic()+1
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise EvidenceUnavailable('IMPORT_BUSY') from None
                    time.sleep(.01)
            yield
        finally:
            os.close(fd)

    def _publish(self, path, data, immutable=False):
        fd, temporary = tempfile.mkstemp(prefix='.staging-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if immutable:
                os.link(temporary, path)  # atomic no-clobber; remove staging before catalog publication
                os.unlink(temporary)
            else:
                os.replace(temporary, path)
            folder = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def accept(self, bundle, *, acknowledge_external=False, synthetic=False):
        validate_bundle(bundle)
        if bundle.producer.uncertain:
            raise ValueError('UNCERTAIN_PRODUCER')
        if bundle.producer.simulation:
            if not synthetic or acknowledge_external:
                raise ValueError('SYNTHETIC_FLAG_REQUIRED_NO_EXTERNAL_PROMOTION')
        elif not acknowledge_external or synthetic:
            raise ValueError('EXPLICIT_OPERATOR_ATTESTATION_REQUIRED')
        bundle_id = digest(bundle.model_dump())
        receipt = Acceptance(origin='SYNTHETIC_TEST' if synthetic else 'EXTERNAL_ATTESTED',
            acknowledgement='synthetic_contract_only' if synthetic else 'operator_reviewed_export_not_cryptographic_proof',
            accepted_at=time.time(), payload_hash=bundle_id)
        envelope = Envelope(schema_version='reasoning-envelope/1', bundle=bundle, acceptance=receipt)
        data = canonical_json(envelope.model_dump()).encode()
        if len(data) > MAX_FILE:
            raise ValueError('ENVELOPE_TOO_LARGE')
        with self.lock():
            catalog_path = self.directory/'catalog.json'
            catalog = self.catalog() if catalog_path.exists() else {'schema_version': 'reasoning-catalog/1', 'entries': []}
            if any(e['id'] == bundle_id for e in catalog['entries']):
                existing = self.load(bundle_id)
                if canonical_json(existing.bundle.model_dump()) != canonical_json(bundle.model_dump()):
                    raise ValueError('IDENTITY_COLLISION')
                return bundle_id
            # Count orphan envelopes as well, so crashes cannot bypass capacity.
            sizes = []
            for count, path in enumerate(self.directory.iterdir(), 1):
                if count > MAX_BUNDLES+3:
                    raise EvidenceUnavailable('ARTIFACT_DIRECTORY_ENTRY_BOUND')
                if ID.fullmatch(path.stem) and path.suffix == '.json':
                    sizes.append(path.stat().st_size)
                    if len(sizes) > MAX_BUNDLES:
                        raise EvidenceUnavailable('ARTIFACT_CAPACITY_REACHED')
            target = self.directory/(bundle_id+'.json')
            if target.exists():
                existing = Envelope.model_validate(decode_json(bounded_read(target)))
                if canonical_json(existing.bundle.model_dump()) != canonical_json(bundle.model_dump()):
                    raise ValueError('IDENTITY_COLLISION')
                data = bounded_read(target)  # interrupted catalog publication can be retried explicitly
            else:
                if len(sizes) >= MAX_BUNDLES or sum(sizes)+len(data) > MAX_TOTAL:
                    raise EvidenceUnavailable('ARTIFACT_CAPACITY_REACHED')
                self._publish(target, data, immutable=True)
            if len(catalog['entries']) >= MAX_BUNDLES:
                raise EvidenceUnavailable('ARTIFACT_CAPACITY_REACHED')
            catalog['entries'].append({'id': bundle_id, 'context_id': digest(bundle.root.identity()),
                'origin': receipt.origin, 'bytes': len(data), 'profiles': sorted({digest(r.settings.model_dump()) for r in bundle.records})})
            self._publish(catalog_path, canonical_json(catalog).encode())
        return bundle_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'accept', 'status'])
    parser.add_argument('file', nargs='?')
    parser.add_argument('--artifact-dir')
    parser.add_argument('--acknowledge-external-engine', action='store_true')
    parser.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(ArtifactStore(args.artifact_dir).catalog(), indent=2))
        return
    bundle = load_input(args.file)
    if args.command == 'validate':
        print(json.dumps({'status': 'STRUCTURALLY_VALID_NOT_ATTESTED', 'simulation': bundle.producer.simulation,
                          'id': digest(bundle.model_dump())}))
    else:
        identifier = ArtifactStore(args.artifact_dir).accept(bundle,
            acknowledge_external=args.acknowledge_external_engine, synthetic=args.synthetic)
        print(json.dumps({'id': identifier, 'origin': 'SYNTHETIC_TEST' if bundle.producer.simulation else 'EXTERNAL_ATTESTED',
                          'limitation': 'Operator attestation is not cryptographic proof of engine execution.'}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, CacheUnavailable):
        print(json.dumps({'status': 'REFUSED', 'reason': 'INVALID_UNSAFE_UNATTESTED_OR_UNAVAILABLE_EVIDENCE'}))
        raise SystemExit(2)
