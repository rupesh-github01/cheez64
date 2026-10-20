"""Bounded legal replay and transparent structural ranking; no engine imports."""
import io
import chess
import chess.pgn
from .positions import canonical_position
from .evaluation_models import canonical_json, digest, PositionContext


def replay(pgn):
    if len(pgn.encode())>2*1024**2: raise ValueError('PGN exceeds 2 MiB')
    stream=io.StringIO(pgn); game=chess.pgn.read_game(stream)
    if game is None or game.errors or stream.read().strip(): raise ValueError('Expected one legal PGN')
    board=game.board(); canonical_position(board)
    root=board.fen(en_passant='legal'); complete=root==chess.STARTING_FEN
    moves=[]; frames=[]; decisions=[]
    def frame():
        # All moves/root/boards have already been validated by this bounded replay.
        # This canonical representation is exactly Segment 16 context v1, not a new identity.
        fen=board.fen(en_passant='legal'); position=canonical_position(board)
        raw={'fen':fen,'root_fen':root,'moves':list(moves),'root_history_complete':complete}
        identity={'version':1,'position_version':1,'variant':'standard','position':position,'halfmove':board.halfmove_clock,
                  'kind':'history','root':root,'moves':list(moves),'history_complete':complete,'draw_policy':'fide_claims_and_automatic_v1'}
        provider={**identity,'kind':'provider_unknown','root':None,'moves':[],'history_complete':False}
        outcome=board.outcome(claim_draw=False)
        terminal=None if outcome is None else {'termination':outcome.termination.name,'winner':outcome.winner}
        frames.append({'terminal':terminal,'request':raw,'canonical':canonical_json(identity),'key':digest(identity),'provider_key':digest(provider)})
    frame()
    values={chess.PAWN:1,chess.KNIGHT:3,chess.BISHOP:3,chess.ROOK:5,chess.QUEEN:9,chess.KING:0}
    for ply,move in enumerate(game.mainline_moves(),1):
        if ply>1000: raise ValueError('UNSUPPORTED_HISTORY: game exceeds 1000 plies')
        if not move or not board.is_legal(move): raise ValueError('Illegal/null move')
        phase='opening' if ply<=20 else ('endgame' if len(board.piece_map())<=12 else 'middlegame')
        capture=board.is_capture(move); material=values.get(board.piece_type_at(move.to_square),0)
        if board.is_en_passant(move): material=1
        color='white' if board.turn else 'black'
        check=board.gives_check(move)
        board.push(move); moves.append(move.uci())
        piece=board.piece_at(move.to_square)
        hanging=bool(piece and piece.piece_type!=chess.PAWN and board.is_attacked_by(not piece.color,move.to_square) and not board.is_attacked_by(piece.color,move.to_square))
        anchor=(ply%8==4)
        score=3*check+2*capture+min(4,material)+3*hanging+3*anchor
        decisions.append({'ply':ply,'color':color,'phase':phase,'move':move.uci(),'check':check,'capture':capture,
                          'material_change':material,'hanging_heuristic':hanging,'quiet':not check and not capture,'anchor':anchor,
                          'score':score,'evidence':'OBSERVED_AND_HEURISTIC','engine_verified':False})
        frame()
    terminal=board.outcome(claim_draw=False)
    return frames,decisions,{'root_history_complete':complete,'terminal':None if terminal is None else {'termination':terminal.termination.name,'winner':terminal.winner},
                            'claimable_draw_not_automatically_claimed':True}


def shortlist(decisions, limit=8):
    selected=[]; windows=set()
    def choose(items):
        for d in items:
            if len(selected)>=limit: return
            if d['ply']//8 in windows: continue
            if d['phase']=='opening' and any(x['phase']=='opening' for x in selected): continue
            selected.append(d); windows.add(d['ply']//8)
            return
    ranked=sorted(decisions,key=lambda x:(-x['score'],x['ply']))
    # Reserve quiet coverage first, then colors and phases, then strongest remaining signals.
    quiet=[d for d in decisions if d['quiet']]
    choose([d for d in quiet if d['anchor']]+quiet)
    choose([d for d in quiet if d['anchor']]+quiet)
    for color in ('white','black'):
        if not any(x['color']==color for x in selected): choose([d for d in ranked if d['color']==color])
    for phase in ('opening','middlegame','endgame'):
        if not any(x['phase']==phase for x in selected): choose([d for d in ranked if d['phase']==phase])
    for d in ranked: choose([d])
    return sorted(selected,key=lambda x:(-x['score'],x['ply']))
