"""Offline verification of unattested browser receipts; never accepts artifacts or runs engines."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from .evaluation_models import PositionContext, digest
from .reasoning_models import Bundle, Budget, replay_context, validate_bundle
from .reasoning import quality
from .reasoning_import import decode_json, safe_artifact_path

JS_SHA = 'ccb130d7ff29408531c47d859a49f91d59d4f482d05a53eb1a3231a4f624c91a'
WASM_SHA = 'ee4f115bdb2d32cadf96cada104a05b5ec257d7ad6f57582190b49bc71f096de'
OPTIONS = {'Threads': 1, 'Hash': 16, 'Ponder': False, 'MultiPV': 3, 'Skill Level': 20,
           'Move Overhead': 10, 'nodestime': 0, 'UCI_Chess960': False, 'UCI_LimitStrength': False,
           'UCI_Elo': 1320, 'UCI_ShowWDL': False, 'EvalFile': 'nn-61e7af4bb97d.nnue'}
MAX_BYTES = 2 * 1024**2


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def verify_package(package):
    """Validate chronological receipts then use unchanged reasoning Bundle validation.

    Hashes bind supplied content, not a trusted browser/engine signature. Explicit
    operator attestation remains mandatory in the existing offline importer.
    """
    require(isinstance(package, dict) and set(package) <= {'schema_version', 'session', 'context', 'artifacts', 'events', 'truncated', 'result', 'error', 'trust'}, 'UNKNOWN_PACKAGE_FIELD_OR_ATTESTATION')
    require(package.get('schema_version') == 'browser-reasoning-receipts/1' and package.get('trust') == 'BROWSER_OBSERVED_NOT_ATTESTED', 'UNATTESTED_RECEIPTS_REQUIRED')
    require(package.get('truncated') is False and not package.get('error'), 'TRUNCATED_OR_FAILED_COLLECTION')
    session = package.get('session')
    require(isinstance(session, str) and re.fullmatch(r'[a-f0-9-]{36}', session), 'INVALID_SESSION')
    require(package.get('artifacts') == {'js_sha256': JS_SHA, 'wasm_sha256': WASM_SHA}, 'PINNED_ASSET_IDENTITY_REQUIRED')
    context = PositionContext.model_validate(package['context'])
    require(context.root_history_complete, 'COMPLETE_ROOT_HISTORY_REQUIRED')
    budget = Budget(max_pushes=8192)
    root = replay_context(context, budget)
    events = package.get('events')
    require(isinstance(events, list) and 10 <= len(events) <= 8192, 'TRANSCRIPT_EVENT_BOUND')
    require(len(json.dumps(package).encode()) <= MAX_BYTES, 'PACKAGE_SIZE_BOUND')
    stage = 'start'; elapsed = 0; text_size = 0; inventory = {}; applied = {}; name = None
    cohort = {}; depth = 0; iteration = 0; bestmove = None
    for seq, event in enumerate(events):
        budget.check()
        require(isinstance(event, dict) and set(event) == {'seq', 'elapsed_ms', 'worker', 'session', 'kind', 'text'}, 'INVALID_EVENT')
        require(type(event['seq']) is int and event['seq'] == seq and event['session'] == session and event['worker'] == session+':1', 'EVENT_SESSION_OR_ORDER_MISMATCH')
        ms = event['elapsed_ms']; text = event['text']; kind = event['kind']
        require(type(ms) in (int, float) and math.isfinite(ms) and elapsed <= ms <= 46000, 'INVALID_EVENT_TIME')
        elapsed = ms
        require(isinstance(text, str) and len(text) <= 8192 and '\n' not in text and '\r' not in text and '\0' not in text, 'INVALID_EVENT_TEXT')
        text_size += len(text); require(text_size <= 1024**2, 'TRANSCRIPT_SIZE_BOUND')
        if kind == 'worker_started':
            require(stage == 'start' and not text, 'WORKER_REPLACEMENT_NOT_EXPORTABLE'); stage = 'uci_command'; continue
        if kind == 'terminated':
            require(stage == 'finished' and not text and seq == len(events)-1, 'MISSING_COMPLETED_SEARCH_OR_EARLY_TERMINATION'); stage = 'closed'; continue
        require(kind in ('send', 'recv'), 'UNKNOWN_EVENT_KIND')
        if kind == 'send':
            if stage == 'uci_command':
                require(text == 'uci', 'UCI_COMMAND_REQUIRED'); stage = 'uci'
            elif stage == 'configure':
                if text == 'isready':
                    require(set(applied) == set(OPTIONS), 'COMPLETE_APPLIED_OPTION_RECEIPT_REQUIRED'); stage = 'config_wait'
                else:
                    match = re.fullmatch(r'setoption name (.+) value (.*)', text)
                    require(match is not None, 'INVALID_OPTION_COMMAND')
                    key, value = match.groups(); require(key in OPTIONS and key not in applied, 'UNKNOWN_OR_DUPLICATE_OPTION')
                    expected = OPTIONS[key]; rendered = str(expected).lower() if type(expected) is bool else str(expected)
                    require(value == rendered, 'NONCONSERVATIVE_OPTION_OR_VALUE_MISMATCH'); applied[key] = expected
            elif stage == 'newgame':
                require(text == 'ucinewgame', 'NEWGAME_RECEIPT_REQUIRED'); stage = 'clear'
            elif stage == 'clear':
                require(text == 'setoption name Clear Hash', 'CLEAR_HASH_RECEIPT_REQUIRED'); stage = 'reset_ready'
            elif stage == 'reset_ready':
                require(text == 'isready', 'RESET_READINESS_REQUIRED'); stage = 'reset_wait'
            elif stage == 'position':
                command = 'position fen '+context.root_fen+(' moves '+' '.join(context.moves) if context.moves else '')
                require(text == command, 'EXACT_HISTORY_COMMAND_REQUIRED'); stage = 'go'
            elif stage == 'go':
                require(text == 'go depth 16 movetime 20000', 'BOUNDED_SEARCH_COMMAND_REQUIRED'); stage = 'search'
            else:
                raise ValueError('UNEXPECTED_COMMAND_OR_INCOMPLETE_SEARCH')
            continue
        if stage == 'uci':
            if text.startswith('id name '):
                require(name is None and text == 'id name Stockfish 19 Lite WASM', 'ENGINE_IDENTITY_MISMATCH'); name = text[8:]
            elif text.startswith('option '):
                match = re.fullmatch(r'option name (.+?) type (spin|check|string|button)(?: (.*))?', text)
                require(match is not None, 'INVALID_UCI_OPTION_DECLARATION')
                key, typ, tail = match.groups(); require(key not in inventory and len(inventory) < 64, 'DUPLICATE_OPTION_DECLARATION')
                inventory[key] = (typ, tail or '')
            elif text == 'uciok':
                require(name and set(inventory) == set(OPTIONS) | {'Clear Hash'}, 'COMPLETE_UCI_INVENTORY_REQUIRED')
                for key, value in OPTIONS.items():
                    typ, tail = inventory[key]
                    if type(value) is bool:
                        require(typ == 'check' and tail in ('default true', 'default false'), 'OPTION_TYPE_MISMATCH')
                    elif type(value) is int:
                        m = re.fullmatch(r'default (-?\d+) min (-?\d+) max (-?\d+)', tail)
                        require(typ == 'spin' and m is not None and int(m[2]) <= value <= int(m[3]), 'OPTION_RANGE_MISMATCH')
                    else:
                        require(typ == 'string' and tail == 'default '+value, 'NNUE_INVENTORY_MISMATCH')
                require(inventory['Clear Hash'] == ('button', ''), 'CLEAR_HASH_CAPABILITY_REQUIRED'); stage = 'configure'
            else:
                require(text.startswith(('id author ', 'info string ')) or text == 'Stockfish 19 Lite WASM by the Stockfish developers (see AUTHORS file)' or not text, 'UNEXPECTED_UCI_RESPONSE')
        elif stage in ('config_wait', 'reset_wait'):
            if text == 'readyok':stage = 'newgame' if stage == 'config_wait' else 'position'
            else:require(text.startswith('info string ') or not text, 'UNEXPECTED_READINESS_RESPONSE')
        elif stage == 'search':
            if text.startswith('bestmove '):
                tokens = text.split(); require(len(tokens) in (2, 4) and (len(tokens) == 2 or tokens[2] == 'ponder'), 'INVALID_BESTMOVE')
                bestmove = tokens[1]; require(cohort and cohort.get(1, {}).get('score', {}).get('moves', [None])[0] == bestmove, 'BESTMOVE_COHORT_MISMATCH'); stage = 'finished'
            elif text.startswith('info ') and ' pv ' in text:
                tokens = text.split(); head = tokens[:tokens.index('pv')]; moves = tokens[tokens.index('pv')+1:]
                require(all(head.count(k) == 1 for k in ('depth', 'multipv', 'score')), 'AMBIGUOUS_INFO_FIELDS')
                d = int(head[head.index('depth')+1]); rank = int(head[head.index('multipv')+1]); at = head.index('score')
                kind_score, value = head[at+1:at+3]; value = int(value)
                require(1 <= d <= 16 and 1 <= rank <= 3 and kind_score in ('cp', 'mate') and 1 <= len(moves) <= 64, 'INVALID_INFO_RANGE')
                require(not ('lowerbound' in head and 'upperbound' in head), 'CONFLICTING_BOUNDS')
                if rank == 1:
                    require(d >= depth, 'DEPTH_REGRESSION'); depth = d; iteration += 1; cohort = {}
                require(d == depth and rank not in cohort, 'MIXED_OR_DUPLICATE_ITERATION')
                white = value if context.fen.split()[1] == 'w' else -value
                board = root.copy(stack=True)
                for uci in moves:
                    budget.push()
                    require(not board.outcome(claim_draw=False), 'INTERMEDIATE_PV_CONTINUES_AFTER_TERMINATION')
                    move = board.parse_uci(uci)
                    require(move and move.uci() == uci, 'ILLEGAL_INTERMEDIATE_PV')
                    board.push(move)
                if board.is_checkmate():
                    require(kind_score == 'mate' and (white > 0) == (not board.turn), 'INTERMEDIATE_MATE_SCORE_MISMATCH')
                bound = 'lower' if 'lowerbound' in head else 'upper' if 'upperbound' in head else 'exact'
                if context.fen.split()[1] == 'b' and bound != 'exact':bound = 'upper' if bound == 'lower' else 'lower'
                cohort[rank] = {'rank': rank, 'depth': d, 'iteration': iteration, 'bound': bound,
                    'score': {'kind': kind_score, 'cp': white if kind_score == 'cp' else None, 'mate': white if kind_score == 'mate' else None, 'winner': None, 'moves': moves, 'original_moves': None}}
            else:require(text.startswith('info ') or not text, 'UNEXPECTED_SEARCH_RESPONSE')
        else:
            require(not text, 'RECEIPT_OUTSIDE_EXPECTED_PHASE')
    require(stage == 'closed' and bestmove is not None, 'INCOMPLETE_TRANSCRIPT')
    require(sorted(cohort) == [1, 2, 3], 'INCOMPLETE_FINAL_MULTIPV')
    result = package.get('result') or {}
    require(result.get('status') == 'complete' and result.get('depth') == depth and result.get('workerStarts') == 1, 'SNAPSHOT_RECEIPT_MISMATCH')
    applied['Clear Hash'] = 'triggered_per_context'
    settings = {'engine': {'family': 'stockfish', 'uci_name': name, 'version': 'stockfish.js 19.0.1 lite-single',
        'binary_sha256': WASM_SHA, 'build': 'sha256:'+WASM_SHA, 'build_method': 'binary_sha256',
        'nnue': 'embedded:sha256:'+WASM_SHA, 'identity_method': 'exported_uci_inventory_and_streamed_hashes'},
        'requested_options': dict(applied), 'applied_options': applied, 'option_inventory': list(inventory), 'applied_options_complete': True,
        'application_method': 'exporter_readyok_receipt', 'reset_policy': 'ucinewgame_clear_hash_per_context',
        'limit_kind': 'combined', 'requested_depth': 16, 'time_seconds': 20.0, 'node_limit': None, 'multipv': 3,
        'search_scope': 'unrestricted', 'searched_roots': [], 'perspective': 'white', 'draw_policy': context.draw_policy}
    bundle = Bundle.model_validate({'schema_version': 'reasoning-evidence/1', 'policy_version': 1, 'root': context.model_dump(),
        'producer': {'name': 'PCIS browser receipt exporter', 'exporter_version': '1', 'simulation': False, 'uncertain': False,
                     'method': 'external_export', 'receipt': 'uci_inventory_readyok_exact_final_iteration'},
        'mode': 'ROOT_MULTIPV_COHORT', 'records': [{'id': 'browser-root', 'context': context.model_dump(), 'settings': settings,
        'cohort': session, 'session': session, 'edge_uci': None, 'achieved_depth': depth, 'final_iteration': iteration,
        'score_conversion': 'side_to_move_converted_to_white_by_exporter', 'lines': [cohort[i] for i in (1, 2, 3)]}]})
    validate_bundle(bundle, budget)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipts'); parser.add_argument('--output')
    args = parser.parse_args()
    # Receipt documents may live in ignored reports; existing importer paths stay unchanged.
    fd = os.open(args.receipts, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno()); require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_BYTES, 'UNSAFE_OR_OVERSIZED_RECEIPTS')
        raw = stream.read(MAX_BYTES+1); require(len(raw) <= MAX_BYTES, 'RECEIPT_SIZE_BOUND')
    bundle = verify_package(decode_json(raw))
    if args.output:
        target = safe_artifact_path(args.output)
        # Deliberately cannot overwrite a cache, library, prior export or existing file.
        with target.open('x', encoding='utf-8') as stream:stream.write(bundle.model_dump_json(indent=2))
    print(json.dumps({'status': 'STRUCTURALLY_VALID_NOT_ATTESTED', 'bundle_id': digest(bundle.model_dump()),
        'quality': [quality(r) for r in bundle.records], 'operator_attestation_required': True,
        'receipt_sha256': hashlib.sha256(raw).hexdigest(), 'warning': 'Receipts and hashes are inspectable, not a cryptographic engine/browser signature.'}))


if __name__ == '__main__':
    try:main()
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc)[:160]}));raise SystemExit(2)
