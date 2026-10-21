"""Constructed chess fixtures, no engine process or personal database access."""
import hashlib
import json
import chess
import pytest
from src.understanding_models import UnderstandingBudget, BudgetExceeded
from src.positions import reconstruct_understanding_position, UnsupportedVariant
from src.tactical_analysis import understanding_motifs, understanding_transition
from src.position_features import extract_understanding_features
from src.understanding import understand_game, pgn_digest, SourceChanged, SourceMissing
from src.library import GameLibrary


def board(fen):
    b = chess.Board(fen)
    assert b.is_valid(), b.status()
    return b


def motifs(b):
    return understanding_motifs(b, UnderstandingBudget())[0]


@pytest.mark.parametrize('fen,motif,squares', [
    ('6k1/3r1q2/8/4N3/8/8/8/6K1 b - - 0 1', 'fork', ['e5','d7','f7']),
    ('4r1k1/8/8/8/8/8/4N3/4K3 w - - 0 1', 'absolute_pin', ['e8','e2','e1']),
    ('4r1k1/8/8/8/8/8/4B3/4Q1K1 w - - 0 1', 'relative_pin', ['e8','e2','e1']),
    ('4r1k1/8/8/8/4K3/8/4R3/8 w - - 0 1', 'skewer_alignment', ['e8','e4','e2']),
    ('6k1/8/8/8/8/5n2/8/4Q1K1 w - - 0 1', 'loose_piece', ['e1','f3']),
    ('7k/5K2/6Q1/8/8/8/8/8 w - - 0 1', 'mate_in_one', ['g6','g7']),
])
def test_board_motif_families(fen, motif, squares):
    findings = motifs(board(fen))
    assert any(f['motif'] == motif and f['squares'] == squares for f in findings)
    assert all(f['provenance'] != 'ENGINE_VERIFIED' for f in findings)


def test_pin_legal_witness_vs_geometry_and_along_ray_move():
    b = board('4r1k1/8/8/8/8/8/4R3/4K3 w - - 0 1')
    assert b.is_legal(chess.Move.from_uci('e2e8'))
    assert any(f['motif'] == 'absolute_pin' for f in motifs(b))
    # A pinned knight geometrically attacks d4, but cannot legally take its rook.
    b = board('4r1k1/8/8/8/3r4/8/4N3/4K3 w - - 0 1')
    assert chess.E2 in b.attackers(chess.WHITE, chess.D4)
    assert not b.is_legal(chess.Move.from_uci('e2d4'))
    loose = [f for f in motifs(b) if f['motif'] == 'loose_piece' and f['squares'][0] == 'd4']
    assert loose and loose[0]['facts']['legal_capture_now'] == []


def test_overloaded_defender_is_only_candidate():
    # Rook d1 supports d4 and a1; black rooks attack those defended targets.
    b = board('r5k1/8/8/8/3Q3r/8/8/R2R2K1 w - - 0 1')
    findings = [f for f in motifs(b) if f['motif'] == 'overloaded_defender_candidate']
    assert any(f['squares'][0] == 'd1' for f in findings)
    assert all(f['provenance'] == 'HEURISTIC' and f['predicate_status'] == 'CANDIDATE' for f in findings)


@pytest.mark.parametrize('fen,uci,motif', [
    ('4k3/8/8/8/8/8/4B3/K3R3 w - - 0 1','e2f3','discovered_check'),
    ('8/8/8/k2pP2R/8/8/8/7K w - d6 0 2','e5d6','discovered_check'),
])
def test_discovery_and_en_passant(fen, uci, motif):
    b = board(fen); m = b.parse_uci(uci); a = b.copy(); a.push(m)
    events, findings = understanding_transition(b, a, m, UnderstandingBudget())
    assert any(f['motif'] == motif for f in findings)
    if b.is_en_passant(m):
        assert events[0]['capture']['square'] == 'd5'
        assert findings[0]['facts']['blockers_removed'] == ['d5','e5']


def test_moved_slider_not_discovery_and_blocked_skewer_negative():
    b = board('4k3/8/8/8/8/8/R7/7K w - - 0 1');m=b.parse_uci('a2e2');a=b.copy();a.push(m)
    assert not understanding_transition(b,a,m,UnderstandingBudget())[1]
    b = board('4r1k1/8/4p3/8/4K3/8/4R3/8 w - - 0 1')
    assert not any(f['motif']=='skewer_alignment' for f in motifs(b))


def test_terminal_and_stalemate_are_not_hanging_or_mate_opportunities():
    b=board('7k/5K2/6Q1/8/8/8/8/8 b - - 0 1')
    assert b.is_stalemate()
    assert [f['motif'] for f in motifs(b)] == ['terminal_position']
    b.turn=chess.WHITE;b.push_uci('g6g7')
    assert [f['motif'] for f in motifs(b)] == ['checkmate']


def test_positional_families_and_no_history_development():
    b=board('6k1/p7/8/3N4/2P1P3/2P5/8/R3K2R w KQ - 0 1')
    f=extract_understanding_features(b,UnderstandingBudget())
    w=f['colors']['white']
    assert w['pawn_structure']['doubled_files']==['c']
    assert w['pawn_structure']['isolated']==['c3','c4','e4']
    assert w['files']['open']==['b','d','f','g','h']
    assert w['files']['semi_open']==['a']
    assert w['weak_squares']['outpost_candidates']==['d5']
    assert w['space']['denominator']==32
    assert w['development']['status']=='UNKNOWN_HISTORY'
    assert w['king_safety']['castling_rights']
    assert w['piece_activity']['items'] and f['material']['conventional_points']['provenance']=='HEURISTIC'


@pytest.mark.parametrize('text,ply', [('1. e4 e5 *',0), ('1. Nf3 Nf6 2. Ng1 *',3)])
def test_replay_ply_zero_and_original_piece_returns(text,ply):
    r=reconstruct_understanding_position(text,ply,UnderstandingBudget())
    assert (r['before'] is None)==(ply==0)
    assert len(r['moves'])==ply
    if ply:
        assert r['after_development']['g1']=={'square':chess.G1,'ever_moved':True}


@pytest.mark.parametrize('fen,uci,kind', [
    ('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1','e1g1','castling'),
    ('1r5k/P7/8/8/8/8/8/7K w - - 0 1','a7b8n','promotion'),
])
def test_special_move_transition(fen,uci,kind):
    b=board(fen);m=b.parse_uci(uci);a=b.copy();a.push(m)
    events,_=understanding_transition(b,a,m,UnderstandingBudget())
    if kind=='castling':assert events[1]=={'kind':'castling_rook','from':'h1','to':'f1'}
    else:assert events[0]['promotion']=='knight' and events[0]['capture']['piece']=='r'


@pytest.mark.parametrize('text', ['1. e4 e5 2. Illegal *','1. -- *','1. e4 *\n\n1. d4 *', '[Variant "Antichess"]\n\n1. e4 *'])
def test_invalid_mainline_or_variant_rejected(text):
    with pytest.raises(ValueError):reconstruct_understanding_position(text,0,UnderstandingBudget())


def test_bounded_parse_variations_and_ply_ranges():
    r=reconstruct_understanding_position('1. e4 (1. d4 d5) e5 *',2,UnderstandingBudget())
    assert r['moves']==['e2e4','e7e5']
    with pytest.raises(ValueError,match='PLY_NOT_IN_GAME'):reconstruct_understanding_position('1. e4 *',2,UnderstandingBudget())
    with pytest.raises(ValueError,match='PGN_TOO_LARGE'):reconstruct_understanding_position(' '*2097153,0,UnderstandingBudget())
    with pytest.raises(ValueError,match='PGN_PLY_LIMIT'):
        reconstruct_understanding_position(' '.join(['Nf3 Nf6 Ng1 Ng8']*2501)+' *',0,UnderstandingBudget(board_seconds=10))


def test_budget_and_quiet_probe_limits():
    b=chess.Board();budget=UnderstandingBudget(max_pushes=1)
    f,c,_,_=understanding_motifs(b,budget)
    assert budget.pushes<=1 and budget.probes<=16
    assert any(v['status']=='PARTIAL_BUDGET' for v in c.values())
    assert not any(x['motif']=='mate_in_one' for x in f)
    with pytest.raises(BudgetExceeded):reconstruct_understanding_position('1. e4 *',0,UnderstandingBudget(board_seconds=0))


@pytest.fixture
def source(tmp_path):
    path=tmp_path/'library.db';pgn='[White "A"]\n[Black "B"]\n\n1. e4 e5 2. Nf3 *'
    with GameLibrary(str(path)) as lib:g,_=lib.add_game(pgn)
    return path,g.game_id,pgn


def test_composition_explanations_readonly_and_freshness(source,monkeypatch):
    path,gid,pgn=source;before=path.read_bytes()
    monkeypatch.setattr(GameLibrary,'_init_db',lambda *_:pytest.fail('writer'))
    r=understand_game(path,gid,3,pgn_digest(pgn))
    assert r.positions['before'] and r.positions['after']
    assert r.source['source_freshness']=='CURRENT_MATCH_AT_FINAL_CHECK'
    assert r.engine_evidence['status']=='NOT_REQUESTED'
    assert len(r.findings)<=32 and len(r.explanations)<=8
    assert all(e['supports'] and 'HEURISTIC' in e['why_examine'] for e in r.explanations)
    assert path.read_bytes()==before
    with pytest.raises(SourceChanged):understand_game(path,gid,0,'0'*64)
    import src.understanding as module
    real=module.read_source;calls=[]
    def changed(*args):
        calls.append(1);return real(*args) if len(calls)==1 else pgn+'\n'
    monkeypatch.setattr(module,'read_source',changed)
    with pytest.raises(SourceChanged):understand_game(path,gid,0,pgn_digest(pgn))


def test_composition_parse_budget_never_publishes_prefix(source):
    path,gid,pgn=source
    r=understand_game(path,gid,0,pgn_digest(pgn),budget=UnderstandingBudget(board_seconds=0))
    assert r.coverage['partial'] and r.positions['after'] is None and not r.findings


def test_automatic_vs_claimable_draw_history():
    r=reconstruct_understanding_position(' '.join(['Nf3 Nf6 Ng1 Ng8']*4)+' *',16,UnderstandingBudget())
    assert r['after'].is_fivefold_repetition()
    assert motifs(r['after'])[0]['motif']=='terminal_position'
    r=reconstruct_understanding_position(' '.join(['Nf3 Nf6 Ng1 Ng8']*2)+' *',8,UnderstandingBudget())
    assert r['after'].can_claim_threefold_repetition()
    assert not any(f['motif']=='terminal_position' for f in motifs(r['after']))


def test_double_attack_and_pinned_fork_do_not_imply_wins():
    b=board('6k1/3p1p2/8/4N3/8/8/8/6K1 b - - 0 1')
    assert any(f['motif']=='double_attack' for f in motifs(b))
    b=board('4r1k1/3r1q2/8/4N3/8/8/8/4K3 w - - 0 1')
    f=next(f for f in motifs(b) if f['motif']=='fork' and f['squares'][0]=='e5')
    assert f['facts']['attacker_pinned'] and 'forced gain' in f['limitations'][0]
    b=board('6k1/3r4/8/4N3/8/8/8/6K1 b - - 0 1')
    assert not any(f['motif']=='fork' and f['squares'][0]=='e5' for f in motifs(b))


def test_positional_counterexamples_and_color_mirroring():
    b=board('6k1/2p5/8/3N4/2P1P3/8/8/6K1 w - - 0 1')
    f=extract_understanding_features(b,UnderstandingBudget())
    assert f['colors']['white']['weak_squares']['outpost_candidates']==['d5']
    mirrored=extract_understanding_features(b.mirror(),UnderstandingBudget())
    assert mirrored['colors']['black']['weak_squares']['outpost_candidates']==['d4']
    # c6 attacks d5 now, removing this candidate, unlike the future-challenge c7 pawn.
    b.remove_piece_at(chess.C7);b.set_piece_at(chess.C6,chess.Piece(chess.PAWN,chess.BLACK))
    assert extract_understanding_features(b,UnderstandingBudget())['colors']['white']['weak_squares']['outpost_candidates']==[]
    b=board('6k1/8/3r4/3P4/8/8/8/6K1 w - - 0 1')
    f=extract_understanding_features(b,UnderstandingBudget())
    assert f['colors']['white']['pawn_structure']['passed_by_pawn_geometry']==['d5']
    assert f['colors']['white']['pawn_structure']['blocked_passers']==['d5']
    assert f['material']['piece_counts']['black']['rook']==1
    assert f['colors']['white']['development']['status']=='UNKNOWN_HISTORY'


def test_result_tail_and_unclosed_annotations_rejected():
    for text in ('1. e4 * e5','1. e4 {unclosed','1. e4 (1. d4 *','1. e4 % e5 *'):
        with pytest.raises(ValueError):reconstruct_understanding_position(text,0,UnderstandingBudget())


def test_actual_source_deletion_and_comment_edit_during_composition(source,monkeypatch):
    import src.understanding as module
    path,gid,text=source;real=module.extract_understanding_features
    def delete(*args):
        with GameLibrary(str(path)) as lib:lib.delete_game(gid)
        return real(*args)
    monkeypatch.setattr(module,'extract_understanding_features',delete)
    with pytest.raises(SourceMissing):understand_game(path,gid,0,pgn_digest(text))
    with GameLibrary(str(path)) as lib:lib.add_game(text)
    def edit(*args):
        import sqlite3
        with sqlite3.connect(path) as c:c.execute('UPDATE games SET pgn=pgn||?',('\n{changed comment}',))
        return real(*args)
    monkeypatch.setattr(module,'extract_understanding_features',edit)
    with pytest.raises(SourceChanged):understand_game(path,gid,0,pgn_digest(text))


@pytest.mark.parametrize('fen,pair', [('6k1/8/8/8/8/8/6B1/4KB2 w - - 0 1',False),('6k1/8/8/8/8/8/8/2B1KB2 w - - 0 1',True)])
def test_bishop_pair_requires_both_color_complexes(fen,pair):
    features=extract_understanding_features(board(fen),UnderstandingBudget())
    assert features['material']['piece_counts']['white']['bishop']==2
    assert features['colors']['white']['bishop_pair'] is pair
