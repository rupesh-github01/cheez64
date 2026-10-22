"""Bounded selected-position reasoning from existing evidence; never acquires analysis."""
from datetime import datetime, timezone
import os
import sqlite3
import chess
from .evaluation_models import PositionContext, digest
from .reasoning_models import Budget, ReasoningResponse, replay_context
from .reasoning_import import ArtifactStore, EvidenceUnavailable
from .understanding import read_source, pgn_digest, SourceChanged
from .positions import reconstruct_understanding_position
from .understanding_models import UnderstandingBudget, BudgetExceeded
from .position_features import extract_understanding_features, compare_understanding_features
from .tactical_analysis import understanding_motifs, understanding_transition


def context_for(board, root_fen, complete):
    return PositionContext(fen=board.fen(en_passant='legal'), root_fen=root_fen,
                           moves=[m.uci() for m in board.move_stack], root_history_complete=complete)


def selected_source(path, game_id, ply, expected_hash, budget):
    pgn = read_source(path, game_id)
    if pgn_digest(pgn) != expected_hash:
        raise SourceChanged('PGN_CHANGED_REFRESH_GAME')
    selected = reconstruct_understanding_position(pgn, ply, budget)
    return pgn, selected


def availability(path, game_id, ply, expected_hash, directory=None):
    budget = Budget()
    _, selected = selected_source(path, game_id, ply, expected_hash, budget)
    result = {'schema_version': 'reasoning-availability/1', 'bundles': [], 'status': 'UNAVAILABLE',
              'local_search': 'DISABLED', 'cloud': 'DISABLED', 'source_freshness': 'PENDING_CHECK'}
    if selected['total_plies'] > 1000 or not selected['root_history_complete']:
        result['status'] = 'UNSUPPORTED_TRUSTED_HISTORY'
    else:
        ctx = context_for(selected['after'], selected['root_fen'], True)
        try:
            catalog = ArtifactStore(directory).catalog()
            result['bundles'] = [x for x in catalog['entries'] if x['context_id'] == digest(ctx.identity())]
            result['status'] = 'AVAILABLE' if result['bundles'] else 'NO_MATCHING_BUNDLE'
        except (EvidenceUnavailable, ValueError, OSError):
            result['status'] = 'UNAVAILABLE_OR_INVALID_CATALOG'
    if pgn_digest(read_source(path, game_id)) != expected_hash:
        raise SourceChanged('PGN_CHANGED_DURING_READ')
    result['source_freshness'] = 'CURRENT_MATCH_AT_FINAL_CHECK'
    return result


def quality(record):
    if record.settings.search_scope != 'unrestricted':
        return 'RESTRICTED_SEARCH'
    if record.achieved_depth < 16:
        return 'DEPTH_BELOW_POLICY_MINIMUM'
    if len(record.lines) != record.settings.multipv:
        return 'PARTIAL_MULTIPV'
    if any(line.bound != 'exact' for line in record.lines):
        return 'BOUND_SCORE'
    return 'QUALIFIED_RECORDED_SEARCH'


def score_value(score):
    return {k: getattr(score, k) for k in ('kind', 'cp', 'mate', 'winner')}


def comparison_candidates(bundle, root, alternative, profile_id, admitted):
    records = bundle.records
    profiles = sorted({digest(r.settings.model_dump()) for r in records if r.edge_uci is not None or bundle.mode == 'ROOT_MULTIPV_COHORT'})
    roots = [r for r in records if r.edge_uci is None]
    # root proposal is at most three candidates; child records provide a proposal when no root is present
    proposals = [l.score.moves[0] for l in roots[0].lines if l.score.moves][:3] if roots else [r.edge_uci for r in records if r.edge_uci][:3]
    moves = list(dict.fromkeys(proposals + ([alternative] if alternative else [])))[:4]
    candidates = [{'uci': uci, 'san': root.san(chess.Move.from_uci(uci)),
                   'origin': 'USER_ALTERNATIVE' if uci == alternative else 'RECORDED_PROPOSAL',
                   'score': None, 'quality': 'MISSING_EVIDENCE', 'record_id': None} for uci in moves]
    evidence_by_move = {}
    if not admitted:
        return candidates, {'status': 'INSUFFICIENT_EVIDENCE', 'method': bundle.mode, 'available_profiles': profiles}, evidence_by_move
    if len(profiles) > 1 and not profile_id:
        return candidates, {'status': 'CONFIG_REQUIRED', 'method': bundle.mode, 'available_profiles': profiles}, evidence_by_move
    selected = [r for r in records if profile_id is None or digest(r.settings.model_dump()) == profile_id]
    if not selected:
        return candidates, {'status': 'INCOMPATIBLE', 'reason': 'PROFILE_NOT_AVAILABLE', 'available_profiles': profiles}, evidence_by_move
    if bundle.mode == 'ROOT_MULTIPV_COHORT':
        eligible_roots = [r for r in selected if r.edge_uci is None]
        if len(eligible_roots) != 1:
            return candidates, {'status': 'INCOMPATIBLE', 'reason': 'ROOT_COHORT_REQUIRED', 'available_profiles': profiles}, evidence_by_move
        record = eligible_roots[0]
        for line in record.lines:
            if line.score.moves:
                evidence_by_move[line.score.moves[0]] = (record, line)
    else:
        for record in selected:
            if record.edge_uci:
                evidence_by_move[record.edge_uci] = (record, record.lines[0])
    cohort_keys = set()
    for candidate in candidates:
        pair = evidence_by_move.get(candidate['uci'])
        if not pair:
            continue
        record, line = pair
        state = quality(record)
        candidate.update(quality=state, record_id=record.id)
        if state == 'QUALIFIED_RECORDED_SEARCH':
            candidate.update(score=score_value(line.score), achieved_depth=record.achieved_depth,
                requested_depth=record.settings.requested_depth, requested_depth_met=record.settings.requested_depth is None or record.achieved_depth >= record.settings.requested_depth,
                profile_id=digest(record.settings.model_dump()), cohort=record.cohort, session=record.session)
            cohort_keys.add((digest(record.settings.model_dump()), record.achieved_depth, record.cohort, record.session))
    compared = [c for c in candidates if c['score'] is not None]
    comparison = {'status': 'INSUFFICIENT_EVIDENCE', 'method': bundle.mode, 'available_profiles': profiles,
                  'compared_moves': [], 'preferred_recorded_uci': None, 'cp_differences': [],
                  'limitation': 'Comparison covers this recorded subset, not all legal moves. Equal depth is not equal certainty.'}
    if len(cohort_keys) > 1:
        comparison.update(status='INCOMPATIBLE', reason='COHORT_DEPTH_SESSION_OR_CONFIGURATION_MISMATCH')
        return candidates, comparison, evidence_by_move
    if len(compared) < 2:
        comparison['reason'] = 'AT_LEAST_TWO_QUALIFIED_CANDIDATES_REQUIRED'
        return candidates, comparison, evidence_by_move
    comparison.update(status='AVAILABLE' if len(compared) == len(candidates) else 'PARTIAL', compared_moves=[c['uci'] for c in compared])
    if all(c['score']['kind'] == 'cp' for c in compared):
        sign = 1 if root.turn else -1
        ordered = sorted(compared, key=lambda c: (-sign*c['score']['cp'], c['uci']))
        best = ordered[0]
        leaders = [c['uci'] for c in ordered if c['score']['cp'] == best['score']['cp']]
        comparison['leading_recorded_ucis'] = leaders
        comparison['preferred_recorded_uci'] = leaders[0] if len(leaders) == 1 else None
        comparison['cp_differences'] = [{'a': best['uci'], 'b': c['uci'], 'for_root_mover': sign*(best['score']['cp']-c['score']['cp']),
                                         'recorded_scores_close': abs(best['score']['cp']-c['score']['cp']) <= 30} for c in ordered[1:]]
        comparison['score_relation'] = 'RECORDED_CP_ORDER_NOT_CPL_OR_CAUSAL_EFFECT'
    else:
        comparison['score_relation'] = 'TYPED_QUALITATIVE_ONLY_NO_CP_MATE_ARITHMETIC'
    return candidates, comparison, evidence_by_move


def inspect_checkpoint(board, identifier, budget):
    budget.check()
    child = UnderstandingBudget(board_seconds=max(.001, min(1, budget.seconds-(budget.clock()-budget.started))),
                                max_pushes=min(512, budget.max_pushes-budget.pushes))
    try:
        features = extract_understanding_features(board, child, development=None)
        findings, coverage, _, omitted = understanding_motifs(board, child, 'after')
    finally:
        budget.pushes += child.pushes
    return {'id': identifier, 'fen': board.fen(en_passant='legal'), 'features': features,
            'findings': findings[:4], 'coverage': coverage, 'omitted_findings': omitted+max(0, len(findings)-4),
            'development': 'UNKNOWN_HYPOTHETICAL_PIECE_HISTORY'}


def build_lines(root, candidates, evidence_by_move, comparison, budget, claims, synthetic):
    chosen = list(dict.fromkeys([comparison.get('preferred_recorded_uci')]+[c['uci'] for c in candidates if c['origin'] == 'USER_ALTERNATIVE']+[c['uci'] for c in candidates]))
    chosen = [x for x in chosen if x][:2]
    checkpoints = [inspect_checkpoint(root, 'root', budget)]
    lines = []
    record_ids = set()
    def claim(kind, text, refs):
        if len(claims) < 16:
            claims.append({'id': f'claim-{len(claims)+1}', 'class': kind, 'text': text, 'references': refs})
    for uci in chosen:
        budget.check()
        candidate = next(c for c in candidates if c['uci'] == uci)
        pair = evidence_by_move.get(uci)
        # Unqualified records do not silently supply an engine-supported defense.
        qualified = pair is not None and candidate['score'] is not None
        if qualified:
            record, pv = pair
            full = ([uci] if record.edge_uci else []) + pv.score.moves
            record_ids.add(record.id)
        else:
            full = [uci]
        line_id = 'line-'+uci
        line = {'id': line_id, 'candidate_uci': uci, 'moves': [], 'sans': [], 'fens': [root.fen(en_passant='legal')],
                'full_validated_plies': len(full), 'display_truncated': len(full) > 8, 'record_id': pair[0].id if qualified else None,
                'opponent_response': None, 'checkpoints': [], 'steps': [], 'origin': 'SYNTHETIC_CONTRACT_ONLY' if synthetic and qualified else ('QUALIFIED_RECORDED_LINE' if qualified else 'LEGAL_USER_OR_PROPOSED_MOVE_ONLY')}
        board = root.copy(stack=True)
        for index, text in enumerate(full[:8], 1):
            budget.push()
            move = board.parse_uci(text)
            if board.outcome(claim_draw=False) or not move:
                raise ValueError('INVALID_DISPLAY_CONTINUATION')
            before = board.copy(stack=True)
            san = board.san(move); board.push(move)
            line['moves'].append(text); line['sans'].append(san); line['fens'].append(board.fen(en_passant='legal'))
            events, _ = understanding_transition(before, board, move, budget)
            outcome = board.outcome(claim_draw=False)
            line['steps'].append({'ply': index, 'mover': 'white' if before.turn else 'black', 'san': san,
                'events': events, 'check': board.is_check(), 'terminal': outcome.termination.name if outcome else None,
                'provenance': 'BOARD_FACT'})
            if index == 2:
                line['opponent_response'] = {'uci': text, 'san': san, 'scope': 'RECORDED_PRINCIPAL_REPLY_NOT_EXHAUSTIVE_DEFENSE'}
            if index in {1, 2, min(8, len(full))}:
                cid = f'{line_id}-ply-{index}'
                point = inspect_checkpoint(board, cid, budget)
                point['events'] = events
                point['before_fen'] = before.fen(en_passant='legal')
                point['changed_features'] = [{k: x.get(k) for k in ('color', 'feature', 'attribution')} for x in
                    compare_understanding_features(checkpoints[0]['features'] if index == 1 else
                        extract_understanding_features(before, budget), point['features']) if x['feature'] != 'development']
                checkpoints.append(point); line['checkpoints'].append(cid)
                event = events[0]
                capture = event.get('capture')
                description = f'{san} ({text[:2]} to {text[2:4]})'
                if capture:
                    description += f' captures {capture["piece"]} on {capture["square"]}'
                if board.is_checkmate(): description += ' gives actual checkmate'
                elif board.is_check(): description += ' gives check'
                claim('BOARD_FACT', f'At {cid}, {description}. This is a fact about the shown line, not a forced-outcome claim.', [cid, line_id])
        if line['opponent_response']:
            claim('SYNTHETIC_OBSERVATION' if synthetic else 'ENGINE_OBSERVATION',
                  f'After {candidate["san"]}, the recorded principal reply is {line["opponent_response"]["san"]}. Other defenses are not exhaustively established.', [line_id, pair[0].id])
        lines.append(line)
    # Cross-line checkpoint differences align the same relative ply, never unequal endpoints.
    if len(lines) == 2:
        for offset in (1, 2):
            ids = [f'{line["id"]}-ply-{offset}' for line in lines]
            points = [next((p for p in checkpoints if p['id'] == cid), None) for cid in ids]
            if all(points):
                changes = [c for c in compare_understanding_features(points[0]['features'], points[1]['features'])
                           if c['feature'] != 'development']
                if changes:
                    labels = ', '.join(dict.fromkeys(x['feature'].replace('_', ' ') for x in changes))
                    claim('INTERPRETATION', f'At the same relative ply {offset}, the lines differ in {labels}. These descriptors may guide examination; they do not establish the cause of a recorded score difference.', ids)
    return lines, checkpoints


def local_observations(path, game_id, sha, selected, context, budget):
    """Reuse existing acceptance authority; never invent missing cohort receipts."""
    from .progressive_store import ProgressiveStore, ProgressiveError
    from .progressive_models import ProgressiveLimits
    from .evaluation_cache import CacheUnavailable
    try:
        store = ProgressiveStore(os.environ.get('CHESS_COACH_PROGRESSIVE_DB'), path,
            ProgressiveLimits.model_validate_json(os.environ.get('CHESS_COACH_PROGRESSIVE_LIMITS', '{}')))
        admitted = store.understanding_evidence(game_id, sha, context, budget)
        return {'status': admitted['status'], 'records': [{'work_id': r['work_id'], 'config_hash': r['config_hash'],
                'achieved_depth': r['evaluation']['achieved_depth'], 'origin': r['origin']} for r in admitted['accepted']],
                'comparison_admission': 'NO_REASONING_COHORT_RECEIPT', 'rejected_reasons': admitted.get('rejected_reasons', [])}
    except (CacheUnavailable, ProgressiveError, ValueError, sqlite3.DatabaseError):
        return {'status': 'UNAVAILABLE', 'records': [], 'comparison_admission': 'NO_REASONING_COHORT_RECEIPT'}


def reason_game(path, game_id, ply, expected_hash, *, alternative=None, directory=None, bundle_id=None,
                profile_id=None, allow_synthetic=False, budget=None):
    budget = budget or Budget()
    _, selected = selected_source(path, game_id, ply, expected_hash, budget)
    root = selected['after']
    if alternative:
        move = root.parse_uci(alternative)
        if not move or move.uci() != alternative:
            raise ValueError('ILLEGAL_ALTERNATIVE')
    from .fast_profile import profile_reader
    with profile_reader(path) as reader:
        fingerprint = reader.execute('SELECT fingerprint FROM games WHERE game_id=?', (game_id,)).fetchone()
    if fingerprint is None:
        from .understanding import SourceMissing
        raise SourceMissing('GAME_NOT_FOUND')
    now = datetime.now(timezone.utc).isoformat()
    result = {'generated_at': now, 'status': 'INSUFFICIENT_EVIDENCE',
        'source': {'game_id': game_id, 'game_fingerprint': fingerprint[0], 'exact_pgn_sha256': expected_hash, 'root_ply': ply,
                   'recorded_next_move_ply': ply+1 if ply < selected['total_plies'] else None, 'source_freshness': 'PENDING_CHECK'},
        'root': {'fen': root.fen(en_passant='legal'), 'side_to_move': 'white' if root.turn else 'black',
                 'root_history_complete': selected['root_history_complete'], 'alternative_uci': alternative,
                 'automatic_terminal': root.outcome(claim_draw=False).termination.name if root.outcome(claim_draw=False) else None},
        'evidence': {'status': 'UNAVAILABLE', 'context_compatibility': 'UNAVAILABLE', 'origin': None, 'is_genuine_engine_evidence': False},
        'candidates': [], 'comparison': {'status': 'INSUFFICIENT_EVIDENCE', 'preferred_recorded_uci': None},
        'lines': [], 'checkpoints': [], 'claims': [], 'explanations': [],
        'coverage': {'partial': False, 'engine_launches': 0, 'cloud_calls': 0, 'candidate_scope': 'BOUNDED_RECORDED_SUBSET', 'child_pushes': 0},
        'limitations': ['A principal variation is not proof against every defense. No global best move or positional causality is established.',
                         'Operator attestation is not cryptographic proof that a genuine engine ran.',
                         'Read-side source and artifact snapshots are independent; freshness is checked again before response.']}
    legal = []
    for move in root.legal_moves:
        budget.check()
        legal.append(move)
        if len(legal) > 256:
            break
    result['root'].update(legal_move_count=len(legal) if len(legal) <= 256 else None, legal_move_count_complete=len(legal) <= 256)
    if len(legal) == 1:
        result['claims'].append({'id': 'claim-1', 'class': 'BOARD_FACT',
            'text': f'{root.san(legal[0])} is the only legal move in this exact position; this is not an engine-strength claim.', 'references': ['root']})
    bundle = None; synthetic = False; evidence_by_move = {}
    complete = selected['root_history_complete'] and selected['total_plies'] <= 1000
    result['root'].update(trusted_history_eligible=complete,
        context_id=digest(context_for(root, selected['root_fen'], True).identity()) if complete else None,
        history_plies=len(root.move_stack))
    terminal = root.outcome(claim_draw=False)
    try:
        if terminal:
            result['evidence']['status'] = 'TERMINAL_ROOT'
        elif not complete:
            result['evidence']['status'] = 'UNSUPPORTED_TRUSTED_HISTORY'
        elif bundle_id:
            context = context_for(root, selected['root_fen'], True)
            envelope = ArtifactStore(directory).load(bundle_id, budget)
            bundle = envelope.bundle
            synthetic = envelope.acceptance.origin == 'SYNTHETIC_TEST'
            result['evidence'].update(origin=envelope.acceptance.origin, bundle_id=bundle_id,
                acceptance=envelope.acceptance.model_dump(), producer=bundle.producer.model_dump(),
                status='SYNTHETIC_DISABLED' if synthetic and not allow_synthetic else 'ADMITTED',
                is_genuine_engine_evidence=False if synthetic else None, producer_claims_real_engine=not synthetic,
                execution_independently_verified=False,
                context_compatibility='TRUSTED_CONTEXT_MATCH' if context.identity() == bundle.root.identity() else 'INCOMPATIBLE')
            if context.identity() != bundle.root.identity():
                result.update(status='INCOMPATIBLE'); result['evidence']['status'] = 'CONTEXT_MISMATCH'
                bundle = None
            else:
                candidates, comparison, evidence_by_move = comparison_candidates(bundle, root, alternative, profile_id,
                    admitted=not synthetic or allow_synthetic)
                result.update(candidates=candidates, comparison=comparison, status=comparison['status'])
                result['evidence']['profiles'] = [{'id': digest(r.settings.model_dump()), 'settings': r.settings.model_dump()} for r in bundle.records]
                result['evidence']['record_ids'] = [r.id for r in bundle.records]
                result['evidence']['interpretation'] = 'SYNTHETIC CONTRACT VALIDATION, NOT CHESS STRENGTH' if synthetic else 'EXTERNALLY ATTESTED SEARCH, NOT INDEPENDENT NUMERICAL PROOF'
        else:
            if complete and not terminal:
                ctx = context_for(root, selected['root_fen'], True)
                result['evidence']['retained_local'] = local_observations(path, game_id, expected_hash, selected, ctx, budget)
                result['evidence']['cache_only'] = 'NOT_USED_PRODUCER_LINEAGE_INSUFFICIENT'
        if alternative and not any(c['uci'] == alternative for c in result['candidates']):
            result['candidates'].append({'uci': alternative, 'san': root.san(chess.Move.from_uci(alternative)),
                'origin': 'USER_ALTERNATIVE', 'score': None, 'record_id': None, 'quality': 'MISSING_EVIDENCE'})
        for candidate in result['candidates']:
            budget.push()
            child = root.copy(stack=True); child.push_uci(candidate['uci'])
            outcome = child.outcome(claim_draw=False)
            candidate['terminal_outcome'] = ({'kind': 'terminal_checkmate' if child.is_checkmate() else 'terminal_draw',
                'winner': ('white' if outcome.winner else 'black') if outcome.winner is not None else None,
                'termination': outcome.termination.name, 'provenance': 'BOARD_FACT'} if outcome else None)
        if result['comparison'].get('preferred_recorded_uci'):
            best = result['comparison']['preferred_recorded_uci']
            result['claims'].append({'id': f'claim-{len(result["claims"])+1}', 'class': 'SYNTHETIC_OBSERVATION' if synthetic else 'ENGINE_OBSERVATION',
                'text': f'{root.san(chess.Move.from_uci(best))} has the preferable recorded cp result within the compared subset; this is not a global best-move claim.',
                'references': list(dict.fromkeys(c['record_id'] for c in result['candidates'] if c.get('score') is not None))})
        elif len(result['comparison'].get('leading_recorded_ucis', [])) > 1:
            result['claims'].append({'id': f'claim-{len(result["claims"])+1}',
                'class': 'SYNTHETIC_OBSERVATION' if synthetic else 'ENGINE_OBSERVATION',
                'text': 'Several compared candidates share the leading recorded cp value. No distinct preference or objective equivalence is established.',
                'references': list(dict.fromkeys(c['record_id'] for c in result['candidates'] if c.get('score') is not None))})
        if not terminal:
            result['lines'], result['checkpoints'] = build_lines(root, result['candidates'], evidence_by_move,
                result['comparison'], budget, result['claims'], synthetic)
        else:
            result['checkpoints'] = [inspect_checkpoint(root, 'root', budget)]
        if root.can_claim_draw():
            result['limitations'].append('A draw is claimable; play is not assumed to continue against the claimant’s choice.')
    except BudgetExceeded as exc:
        result.update(status='RESOURCE_LIMIT')
        result['coverage'].update(partial=True, reason=str(exc))
        # No partly built line or dangling claim references leave the service.
        result.update(lines=[], checkpoints=[], claims=[])
    except (EvidenceUnavailable, ValueError, OSError) as exc:
        result.update(status='INCOMPATIBLE' if bundle_id else 'INSUFFICIENT_EVIDENCE')
        result['evidence']['status'] = 'INVALID_OR_UNAVAILABLE_EVIDENCE'
        reason = str(exc) if isinstance(exc, EvidenceUnavailable) else 'BUNDLE_VALIDATION_FAILED'
        result['evidence']['reason'] = reason if reason.isupper() and len(reason) < 100 else 'BUNDLE_VALIDATION_FAILED'
        result.update(lines=[], checkpoints=[], claims=[])
        # Evidence failure must not invent a candidate score; retain only explicit alternative.
        result['candidates'] = []
        if alternative:
            result['candidates'] = [{'uci': alternative, 'san': root.san(chess.Move.from_uci(alternative)), 'origin': 'USER_ALTERNATIVE',
                                     'score': None, 'record_id': None, 'quality': 'MISSING_EVIDENCE'}]
        result['comparison'] = {'status': result['status'], 'preferred_recorded_uci': None}
        try:
            result['lines'], result['checkpoints'] = build_lines(root, result['candidates'], {}, result['comparison'], budget, result['claims'], False)
        except BudgetExceeded:
            result.update(lines=[], checkpoints=[], claims=[]); result['coverage']['partial'] = True
    references = {'root', *(r.id for r in bundle.records)} if bundle else {'root'}
    references.update(x['id'] for x in result['lines']); references.update(x['id'] for x in result['checkpoints'])
    if any(not set(c['references']) <= references for c in result['claims']):
        raise ValueError('INVALID_EXPLANATION_REFERENCE')
    result['explanations'] = [{'claim_id': c['id'], 'class': c['class'], 'text': c['text']} for c in result['claims']]
    if not result['comparison'].get('compared_moves'):
        result['limitations'].append('Insufficient compatible candidate evidence to rank moves; absent evaluations are unavailable, not zero.')
    result['coverage'].update(child_pushes=budget.pushes, candidates_returned=len(result['candidates']),
        evaluated_candidates=sum(c.get('score') is not None for c in result['candidates']),
        checkpoints=len(result['checkpoints']), findings=sum(len(p['findings']) for p in result['checkpoints']))
    if pgn_digest(read_source(path, game_id)) != expected_hash:
        raise SourceChanged('PGN_CHANGED_DURING_REASONING')
    result['source'].update(source_freshness='CURRENT_MATCH_AT_FINAL_CHECK', checked_at=datetime.now(timezone.utc).isoformat())
    return ReasoningResponse.model_validate(result)
