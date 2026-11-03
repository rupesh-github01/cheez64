"""Small board-fact regressions; no engine score or provenance is fabricated."""
import hashlib
import pytest
from src.library import GameLibrary
from src.reasoning import reason_game


@pytest.mark.parametrize('moves,ply,alternative,phrase,winner', [
    ('1. e4 d5 *', 2, 'e4d5', 'captures a black pawn on d5', None),
    ('1. e4 d5 2. exd5 *', 3, 'd8d5', 'captures a white pawn on d5', None),
    ('1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 *', 6, 'h5f7', 'captures a black pawn on f7 gives actual checkmate', 'white'),
    ('1. f3 e5 2. g4 *', 3, 'd8h4', 'gives actual checkmate', 'black'),
])
def test_readable_capture_and_actual_mate_without_engine_evidence(tmp_path, monkeypatch, moves, ply, alternative, phrase, winner):
    path = (tmp_path/'library.db').resolve()
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_DB', str(tmp_path/'absent-progressive.db'))
    monkeypatch.setenv('CHESS_COACH_REASONING_DIR', str(tmp_path/'absent-artifacts'))
    text = '[White "Fixture"]\n[Black "Fixture"]\n\n'+moves
    with GameLibrary(str(path)) as library:
        game, _ = library.add_game(text)
    response = reason_game(path, game.game_id, ply, hashlib.sha256(text.encode()).hexdigest(), alternative=alternative).model_dump()
    assert response['status'] == 'INSUFFICIENT_EVIDENCE'
    assert phrase in response['explanations'][0]['text']
    assert all(c['class'] == 'BOARD_FACT' for c in response['claims'])
    assert response['candidates'][0]['score'] is None
    assert response['comparison']['preferred_recorded_uci'] is None
    assert response['coverage']['engine_launches'] == response['coverage']['cloud_calls'] == 0
    if winner:
        assert response['candidates'][0]['terminal_outcome']['winner'] == winner
    assert not (tmp_path/'absent-progressive.db').exists()
