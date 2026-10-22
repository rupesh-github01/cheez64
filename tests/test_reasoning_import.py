import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from src.reasoning_import import ArtifactStore, EvidenceUnavailable, load_input
from src.reasoning_models import Bundle, Envelope, validate_bundle
from tests.reasoning_fixtures import bundle, bundle_data


def test_immutable_idempotent_concurrent_acceptance(tmp_path):
    store = ArtifactStore((tmp_path/'artifacts').resolve())
    with pytest.raises(EvidenceUnavailable): store.catalog()
    assert not store.directory.exists()
    b = bundle()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: store.accept(b, synthetic=True), range(2)))
    assert ids[0] == ids[1] and len(store.catalog()['entries']) == 1
    path = store.directory/(ids[0]+'.json'); before = path.read_bytes()
    assert store.accept(b, synthetic=True) == ids[0] and path.read_bytes() == before
    assert store.load(ids[0]).acceptance.origin == 'SYNTHETIC_TEST'


def test_attestation_is_explicit_and_synthetic_cannot_be_promoted(tmp_path):
    store = ArtifactStore((tmp_path/'artifacts').resolve())
    with pytest.raises(ValueError): store.accept(bundle(), acknowledge_external=True)
    raw = bundle_data(); raw['producer'].update(simulation=False, method='external_export', name='Declared exporter')
    declared = Bundle.model_validate(raw)
    with pytest.raises(ValueError): store.accept(declared)
    identifier = store.accept(declared, acknowledge_external=True)
    envelope = store.load(identifier)
    assert envelope.acceptance.origin == 'EXTERNAL_ATTESTED'
    assert envelope.acceptance.acknowledgement == 'operator_reviewed_export_not_cryptographic_proof'
    # Invented identity data in this test only proves declaration/acceptance mechanics.


@pytest.mark.parametrize('mutation', ['options', 'requested', 'nnue', 'extra', 'null_pv', 'illegal_pv', 'child', 'duplicate', 'iteration', 'uncertain'])
def test_bad_import_contracts(tmp_path, mutation):
    raw = bundle_data('SIBLING_CHILD_COHORT' if mutation == 'child' else 'ROOT_MULTIPV_COHORT')
    record = raw['records'][0]
    if mutation == 'options': record['settings']['option_inventory'].pop()
    elif mutation == 'requested': record['settings']['applied_options']['Threads'] = 2
    elif mutation == 'nnue': record['settings']['engine']['nnue'] = 'embedded:sha256:'+'b'*64
    elif mutation == 'extra': raw['unexpected'] = True
    elif mutation == 'null_pv': record['lines'][0]['score']['moves'] = ['0000']
    elif mutation == 'illegal_pv': record['lines'][0]['score']['moves'] += ['h8h1']
    elif mutation == 'child': record['edge_uci'] = 'f1c4'
    elif mutation == 'duplicate': record['lines'][1]['score']['moves'] = record['lines'][0]['score']['moves']
    elif mutation == 'iteration': record['lines'][0]['iteration'] = 2
    elif mutation == 'uncertain': raw['producer']['uncertain'] = True
    with pytest.raises(ValueError):
        ArtifactStore((tmp_path/'artifacts').resolve()).accept(Bundle.model_validate(raw), synthetic=True)


def test_safe_paths_size_and_integrity(tmp_path, monkeypatch):
    store = ArtifactStore((tmp_path/'artifacts').resolve())
    identifier = store.accept(bundle(), synthetic=True)
    path = store.directory/(identifier+'.json')
    alias = tmp_path/'alias'; alias.symlink_to(store.directory)
    with pytest.raises(ValueError): ArtifactStore(alias)
    hard = tmp_path/'hard.json'; hard.hardlink_to(path)
    with pytest.raises(ValueError): load_input(hard)
    hard.unlink()
    large = tmp_path/'large.json';large.write_bytes(b' '* (2*1024**2+1))
    with pytest.raises(ValueError): load_input(large)
    value = json.loads(path.read_text());value['bundle']['records'][0]['lines'][0]['score']['cp'] += 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): store.load(identifier)
    assert not (tmp_path/'not-created').exists()


def test_interrupted_catalog_publication_is_explicitly_resumable(tmp_path, monkeypatch):
    store = ArtifactStore((tmp_path/'artifacts').resolve());original = store._publish
    def interrupt(path, *a, **k):
        if path.name == 'catalog.json': raise OSError('synthetic interruption')
        return original(path, *a, **k)
    monkeypatch.setattr(store, '_publish', interrupt)
    with pytest.raises(OSError): store.accept(bundle(), synthetic=True)
    assert len(list(store.directory.glob('*.json'))) == 1
    with pytest.raises(EvidenceUnavailable): store.catalog()
    monkeypatch.setattr(store, '_publish', original)
    identifier = store.accept(bundle(), synthetic=True)
    assert store.load(identifier).bundle == bundle()


def test_capacity_refuses_without_deleting_existing(tmp_path, monkeypatch):
    import src.reasoning_import as module
    store = ArtifactStore((tmp_path/'artifacts').resolve());identifier = store.accept(bundle(), synthetic=True)
    before = (store.directory/(identifier+'.json')).read_bytes()
    monkeypatch.setattr(module, 'MAX_BUNDLES', 1)
    raw = bundle_data();raw['producer']['exporter_version'] = '2'
    with pytest.raises(EvidenceUnavailable): store.accept(Bundle.model_validate(raw), synthetic=True)
    assert (store.directory/(identifier+'.json')).read_bytes() == before
    assert len(store.catalog()['entries']) == 1


def test_duplicate_json_fields_and_boolean_option_assertions_refused(tmp_path):
    raw = bundle_data(); raw['records'][0]['settings']['applied_options_complete'] = 1
    with pytest.raises(ValueError): Bundle.model_validate(raw)
    path = tmp_path/'duplicate.json';path.write_text('{"schema_version":"reasoning-evidence/1","schema_version":"other"}')
    with pytest.raises(ValueError, match='DUPLICATE_JSON_FIELD'): load_input(path)


def test_terminal_line_score_sign_and_continuation_consistency():
    from pathlib import Path
    corpus = json.loads((Path(__file__).parent/'data/reasoning/corpus.json').read_text())
    raw = next(c['synthetic_bundle'] for c in corpus['cases'] if c['id'] == 'M2')
    raw['records'][0]['lines'][0]['score']['mate'] = 1  # false White winner, actual Black mates
    with pytest.raises(ValueError, match='PERSPECTIVE_MISMATCH'): validate_bundle(Bundle.model_validate(raw))
    raw['records'][0]['lines'][0]['score']['mate'] = -1
    raw['records'][0]['lines'][0]['score']['moves'].append('b1c3')
    with pytest.raises(ValueError, match='AFTER_TERMINATION'): validate_bundle(Bundle.model_validate(raw))
