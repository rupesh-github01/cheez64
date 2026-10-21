"""On-demand composition only. No engine, queue, cloud or chat execution imports."""
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import chess
from .library import GameLibrary
from .positions import reconstruct_understanding_position, canonical_position, MAX_PGN_BYTES
from .position_features import extract_understanding_features, compare_understanding_features
from .tactical_analysis import understanding_motifs, understanding_transition
from .explanations import explain_understanding_finding, explain_understanding_position
from .understanding_models import UnderstandingBudget, BudgetExceeded, UnderstandingResponse


class SourceChanged(ValueError):pass
class SourceMissing(ValueError):pass


def read_source(path, game_id):
    with GameLibrary(str(path), read_only=True) as lib:
        lib._conn.execute('PRAGMA busy_timeout=250')
        size = lib._conn.execute('SELECT length(CAST(pgn AS BLOB)) FROM games WHERE game_id=?', (game_id,)).fetchone()
        if size is None:raise SourceMissing('GAME_NOT_FOUND')
        if size[0] > MAX_PGN_BYTES:raise ValueError('PGN_TOO_LARGE')
        game = lib.get_game(game_id)
        if game is None:raise SourceMissing('GAME_NOT_FOUND')
        return game.pgn


def pgn_digest(pgn):return hashlib.sha256(pgn.encode('utf-8')).hexdigest()


def accepted_evidence(path, game_id, sha, selected, budget, config_hash=None):
    from .evaluation_models import PositionContext, Lookup, CacheLimits
    from .evaluation_cache import EvaluationCache, CacheUnavailable
    from .progressive_store import ProgressiveStore, ProgressiveError
    from .progressive_models import ProgressiveLimits
    output = {'requested': True, 'status': 'UNAVAILABLE', 'contexts': [], 'accepted': []}
    if selected['total_plies'] > 1000 or not selected['root_history_complete']:
        output['status'] = 'UNSUPPORTED_HISTORY' if selected['total_plies'] > 1000 else 'UNKNOWN_ROOT_HISTORY'
        return output
    try:
        for stage, board in (('before', selected['before']), ('after', selected['after'])):
            if board is None:continue
            budget.check(evidence=True)
            moves = selected['moves'][:-1] if stage == 'before' else selected['moves']
            context = PositionContext(fen=board.fen(en_passant='legal'), root_fen=selected['root_fen'], moves=moves, root_history_complete=True)
            item = {'stage': stage, 'context_compatibility': 'UNAVAILABLE', 'understanding_admission': 'UNAVAILABLE'}
            try:
                cache = EvaluationCache(os.environ.get('CHESS_COACH_EVAL_CACHE_DB'), CacheLimits.model_validate_json(os.environ.get('CHESS_COACH_EVAL_LIMITS', '{}')))
                with cache.reader() as reader:
                    reader.set_progress_handler(lambda: int(budget.clock()-budget.started >= budget.total_seconds), 1000)
                    hit = cache.lookup(Lookup(context=context, require_trusted=True, config_hash=config_hash), reader)
                item.update(cache_status=hit['status'], context_compatibility=hit['context_compatibility'], cache_read_at=datetime.now(timezone.utc).isoformat())
                if hit['evaluation'] is not None:item['understanding_admission'] = 'PRODUCER_UNATTESTED'
                elif hit['status'] == 'CONFIG_REQUIRED':
                    item.update(understanding_admission='CONFIG_REQUIRED', available_configs=[x['config_hash'] for x in hit['available']])
            except (ValueError, KeyError, TypeError, AttributeError, CacheUnavailable, sqlite3.DatabaseError):
                item['cache_status'] = 'UNAVAILABLE'
            try:
                store = ProgressiveStore(os.environ.get('CHESS_COACH_PROGRESSIVE_DB'), path,
                                         ProgressiveLimits.model_validate_json(os.environ.get('CHESS_COACH_PROGRESSIVE_LIMITS', '{}')))
                admission = store.understanding_evidence(game_id, sha, context, budget, config_hash)
                item['progressive_status'] = admission['status']
                item['progressive_read_at'] = datetime.now(timezone.utc).isoformat()
                item['rejected_reasons'] = admission.get('rejected_reasons', [])
                item['available_configs'] = sorted(set(item.get('available_configs', []) + admission.get('available', [])))
                if admission['accepted']:
                    item['understanding_admission'] = 'ACCEPTED_NON_SIMULATED'
                    for record in admission['accepted']:
                        # Full PVs already validated and charged before any display prefix.
                        record['stage'] = stage
                        record['evaluation']['pvs_available'] = len(record['evaluation']['pvs'])
                        record['evaluation']['pvs'] = [{**pv, 'available_plies': len(pv['moves']), 'moves': pv['moves'][:8]} for pv in record['evaluation']['pvs'][:2]]
                        output['accepted'].append(record)
                elif item['understanding_admission'] not in ('PRODUCER_UNATTESTED', 'CONFIG_REQUIRED'):
                    item['understanding_admission'] = admission['status']
            except BudgetExceeded:raise
            except (ValueError, KeyError, TypeError, AttributeError, CacheUnavailable, ProgressiveError, sqlite3.DatabaseError):
                item['progressive_status'] = 'UNAVAILABLE'
            output['contexts'].append(item)
        statuses = [x['understanding_admission'] for x in output['contexts']]
        output['status'] = 'ACCEPTED_NON_SIMULATED' if output['accepted'] else ('PRODUCER_UNATTESTED' if 'PRODUCER_UNATTESTED' in statuses else (statuses[-1] if statuses else 'UNAVAILABLE'))
        if len(output['accepted']) == 2:
            a, b = output['accepted']; x, y = a['evaluation'], b['evaluation']
            if x['config'] == y['config'] and x['achieved_depth'] == y['achieved_depth'] and x['pvs'][0]['kind'] == y['pvs'][0]['kind'] == 'cp':
                output['score_change_cp_white'] = y['pvs'][0]['cp'] - x['pvs'][0]['cp']
                output['score_change_limitations'] = 'Raw searched score change; not CPL, accuracy or proof of motif causation.'
    except BudgetExceeded as exc:
        output.update(status='PARTIAL_BUDGET', reason=str(exc))
    return output


def relation_changes(before, after, events):
    mapping = {e['from']: e['to'] for e in events if 'from' in e}
    def key(f, translate=False):
        return (f['motif'], f['perspective'], tuple(mapping.get(s, s) if translate else s for s in f['squares']))
    old = {key(f, True): f for f in before}; new = {key(f): f for f in after}
    return {'created': [f['id'] for k, f in new.items() if k not in old],
            'removed': [f['id'] for k, f in old.items() if k not in new],
            'persisted': [f['id'] for k, f in new.items() if k in old]}


def understand_game(path, game_id, ply, expected_hash, *, evidence='none', config_hash=None, budget=None):
    budget = budget or UnderstandingBudget()
    pgn = read_source(path, game_id); sha = pgn_digest(pgn)
    if sha != expected_hash:raise SourceChanged('PGN_CHANGED_REFRESH_GAME')
    now = datetime.now(timezone.utc).isoformat()
    result = {'generated_at': now, 'source': {'game_id': game_id, 'exact_pgn_sha256': sha, 'snapshot_at': now, 'source_freshness': 'PENDING_CHECK'},
              'selection': {'ply': ply, 'mode': 'position' if ply == 0 else 'move_transition'},
              'positions': {'before': None, 'after': None}, 'features': {'before': None, 'after': None},
              'changes': {'created': [], 'removed': [], 'persisted': [], 'changed': [], 'events': []}, 'findings': [], 'explanations': [],
              'coverage': {'partial': False, 'budget_exhausted': False, 'detectors': {}, 'no_finding_is_conclusive': False},
              'engine_evidence': {'requested': evidence == 'accepted_local', 'status': 'NOT_REQUESTED', 'accepted': []},
              'limitations': ['Board geometry is not a forced gain or objective evaluation. Heuristic prompts are not engine judgments.',
                              'Reads of separate stores are independent snapshots; source freshness is checked again before response.']}
    selected = None; before_f = []; after_f = []; discoveries = []
    try:
        selected = reconstruct_understanding_position(pgn, ply, budget)
        result['selection'].update(root_history_complete=selected['root_history_complete'], total_plies=selected['total_plies'],
                                   mover=('white' if selected['before'].turn else 'black') if selected['before'] else None)
        for stage in ('before', 'after'):
            board = selected[stage]
            if board is not None:
                result['positions'][stage] = {'fen': board.fen(en_passant='legal'), 'exploration_identity': canonical_position(board)}
                result['features'][stage] = extract_understanding_features(board, budget, selected[stage+'_development'])
        result['coverage']['features'] = 'COMPLETE'
        events, discoveries = understanding_transition(selected['before'], selected['after'], selected['move'], budget)
        result['changes']['events'] = events
        # Current position gets first access to bounded optional probes.
        after_f, coverage, legal, omitted = understanding_motifs(selected['after'], budget, 'after')
        result['coverage']['detectors']['after'] = coverage
        result['coverage']['omitted_findings'] = omitted
        side = 'white' if selected['after'].turn else 'black'
        for item in result['features']['after']['colors'][side]['piece_activity']['items']:
            item['legal_mobility'] = sum(chess.square_name(m.from_square) == item['square'] for m in legal) if coverage['legal_moves']['status'] == 'COMPLETE' else None
        result['features']['after']['colors'][side]['piece_activity']['legal_mobility_status'] = coverage['legal_moves']['status']
        if selected['before'] is not None:
            before_f, coverage, before_legal, omitted = understanding_motifs(selected['before'], budget, 'before')
            side = 'white' if selected['before'].turn else 'black'
            activity = result['features']['before']['colors'][side]['piece_activity']
            activity['legal_mobility_status'] = coverage['legal_moves']['status']
            for item in activity['items']:
                item['legal_mobility'] = sum(chess.square_name(m.from_square) == item['square'] for m in before_legal) if coverage['legal_moves']['status'] == 'COMPLETE' else None
            result['coverage']['detectors']['before'] = coverage
            result['coverage']['omitted_findings'] += omitted
        changes = relation_changes(before_f, after_f, events)
        result['changes'].update(changes)
        result['changes']['changed'] = compare_understanding_features(result['features']['before'], result['features']['after'])
    except BudgetExceeded as exc:
        result['coverage'].update(partial=True, budget_exhausted=True, reason=str(exc))
        result['coverage'].setdefault('features', 'PARTIAL_BUDGET')
    all_findings = discoveries + after_f + before_f
    priority = {'checkmate': 0, 'mate_in_one': 1, 'discovered_check': 2, 'discovered_attack': 3, 'loose_piece': 4, 'fork': 5, 'absolute_pin': 6}
    all_findings.sort(key=lambda f: (priority.get(f['motif'], 10), f['stage'] == 'before', f['id']))
    result['findings'] = all_findings[:32]
    result['coverage']['omitted_findings'] = result['coverage'].get('omitted_findings', 0) + max(0, len(all_findings)-32)
    result['explanations'] = [explain_understanding_finding(f) for f in result['findings'][:4]]
    color = result['selection'].get('mover') or ('white' if selected and selected['after'].turn else 'black')
    result['explanations'] = (result['explanations'] + explain_understanding_position(result['features']['after'], color))[:8]
    returned_ids = {f['id'] for f in result['findings']}
    for name in ('created', 'removed', 'persisted'):
        result['changes'][name] = [fid for fid in result['changes'][name] if fid in returned_ids]
    if not selected or result['positions']['after'] is None:
        result['engine_evidence']['status'] = 'NOT_ASSESSED' if evidence == 'accepted_local' else 'NOT_REQUESTED'
    elif evidence == 'accepted_local':
        result['engine_evidence'] = accepted_evidence(path, game_id, sha, selected, budget, config_hash)
    states = [v['status'] for group in result['coverage']['detectors'].values() for v in group.values()]
    result['coverage']['partial'] |= any(s == 'PARTIAL_BUDGET' for s in states) or bool(result['coverage']['omitted_findings'])
    result['coverage']['child_pushes'] = budget.pushes; result['coverage']['opportunity_probes'] = budget.probes
    result['coverage']['budget_exhausted'] |= any(v.get('reason') in ('TIME_BUDGET', 'CHILD_PUSH_BUDGET') for g in result['coverage']['detectors'].values() for v in g.values())
    if selected and not selected['root_history_complete']:
        result['limitations'].append('Setup root: prior repetition/draw history and original-piece development are unknown.')
    if pgn_digest(read_source(path, game_id)) != sha:raise SourceChanged('PGN_CHANGED_DURING_READ')
    result['source'].update(source_freshness='CURRENT_MATCH_AT_FINAL_CHECK', final_checked_at=datetime.now(timezone.utc).isoformat())
    return UnderstandingResponse.model_validate(result)
