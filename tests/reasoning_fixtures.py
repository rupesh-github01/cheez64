"""Explicitly invented scores/identity for contract validation, never engine results."""
import chess
from src.reasoning_models import Bundle
from src.evaluation_models import PositionContext

PGN = '[White "A"]\n[Black "B"]\n\n1. e4 e5 *'


def context(moves=()):
    b = chess.Board()
    for move in moves: b.push_uci(move)
    return PositionContext(fen=b.fen(en_passant='legal'), root_fen=chess.STARTING_FEN, moves=list(moves), root_history_complete=True).model_dump()


def settings(multipv=2):
    return {'engine': {'family': 'stockfish', 'uci_name': 'Stockfish SYNTHETIC FIXTURE', 'version': 'not a real run',
        'binary_sha256': 'a'*64, 'build': 'sha256:'+'a'*64, 'build_method': 'binary_sha256',
        'nnue': 'embedded:sha256:'+'a'*64, 'identity_method': 'exported_uci_inventory_and_streamed_hashes'},
        'requested_options': {'Threads': 1, 'Hash': 64}, 'applied_options': {'Threads': 1, 'Hash': 64, 'MultiPV': multipv, 'UCI_Chess960': False, 'Ponder': False},
        'option_inventory': ['Threads', 'Hash', 'MultiPV', 'UCI_Chess960', 'Ponder'], 'applied_options_complete': True,
        'application_method': 'exporter_readyok_receipt', 'reset_policy': 'ucinewgame_clear_hash_per_context',
        'limit_kind': 'depth', 'requested_depth': 16, 'time_seconds': None, 'node_limit': None, 'multipv': multipv,
        'search_scope': 'unrestricted', 'searched_roots': [], 'perspective': 'white', 'draw_policy': 'fide_claims_and_automatic_v1'}


def line(moves, cp=35, rank=1, kind='cp', mate=None):
    return {'rank': rank, 'depth': 16, 'iteration': 16, 'bound': 'exact',
            'score': {'kind': kind, 'cp': cp if kind == 'cp' else None, 'mate': mate, 'winner': None, 'moves': moves, 'original_moves': None}}


def bundle_data(mode='ROOT_MULTIPV_COHORT'):
    root = context(['e2e4', 'e7e5'])
    base = {'id': 'root-search', 'context': root, 'settings': settings(), 'cohort': 'fixture-cohort', 'session': 'fixture-session',
        'edge_uci': None, 'achieved_depth': 16, 'final_iteration': 16, 'nodes': 120, 'elapsed_seconds': .01,
        'score_conversion': 'white_relative_export', 'lines': [line(['g1f3', 'b8c6', 'f1c4']), line(['b1c3', 'g8f6', 'f1c4'], 10, 2)]}
    records = [base]
    if mode == 'SIBLING_CHILD_COHORT':
        records = []
        for i, (edge, pv, cp) in enumerate([('g1f3', ['b8c6', 'f1c4'], 35), ('b1c3', ['g8f6', 'f1c4'], 10)]):
            records.append({**base, 'id': 'child-'+str(i), 'edge_uci': edge, 'context': context(root['moves']+[edge]),
                'settings': settings(1), 'lines': [line(pv, cp)]})
    return {'schema_version': 'reasoning-evidence/1', 'policy_version': 1, 'root': root,
        'producer': {'name': 'Synthetic contract fixture', 'exporter_version': '1', 'simulation': True, 'uncertain': False,
                     'method': 'synthetic_fixture', 'receipt': 'uci_inventory_readyok_exact_final_iteration'},
        'mode': mode, 'records': records}


def bundle(mode='ROOT_MULTIPV_COHORT'):
    return Bundle.model_validate(bundle_data(mode))
