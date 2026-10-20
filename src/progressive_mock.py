"""Synthetic adapter for explicit temporary-database validation, never chess truth."""
import chess
from .evaluation_models import Evaluation,PV


class MockEngineAdapter:
    simulated=True
    def __init__(self,cost=.001):self.virtual_cost=cost;self.calls=0
    def search(self,context,config):
        self.calls+=1;board=chess.Board(context.fen)
        if board.is_game_over(claim_draw=False):raise ValueError('TERMINAL_POSITION_NO_SEARCH')
        return Evaluation(context=context,config=config,achieved_depth=config.depth,nodes=100,
                          pvs=[PV(kind='cp',cp=0,moves=[next(iter(board.legal_moves)).uci()])])
    def close(self):pass
