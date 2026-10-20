"""Benign fake UCI process; never Stockfish. Used only in safety tests."""
import json
import os
from pathlib import Path
import sys
import time
import chess
board=chess.Board()

mode=sys.argv[1] if len(sys.argv)>1 else 'normal'
if len(sys.argv)>2: Path(sys.argv[2]).write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp()}))
for line in sys.stdin:
    line=line.strip()
    if line=='uci': print('id name ProgressiveFixture-v1\noption name Threads type spin default 1 min 1 max 1\noption name Hash type spin default 16 min 1 max 64\noption name MultiPV type spin default 1 min 1 max 5\noption name UCI_Chess960 type check default true\noption name Ponder type check default true\nuciok',flush=True)
    elif line=='isready': print('readyok',flush=True)
    elif line.startswith('setoption '):
        if len(sys.argv)>2:
            with Path(sys.argv[2]+'.options').open('a') as out:out.write(line+'\n')
    elif line.startswith('position fen '):
        parts=line.split();board=chess.Board(' '.join(parts[2:8]))
        if len(parts)>8:
            for move in parts[9:]:board.push_uci(move)
        if len(sys.argv)>2:Path(sys.argv[2]+'.trace').write_text(line)
    elif line.startswith('go '):
        if mode=='hang':
            print('info string HANGING',flush=True); time.sleep(60)
        elif mode=='crash': raise SystemExit(2)
        else:
            move=next(iter(board.legal_moves)).uci()
            score='mate 3' if mode=='mate' else 'cp 23'
            print(f'info depth 18 nodes 100 score {score} pv {move}\nbestmove {move}',flush=True)
    elif line=='quit':
        if mode=='stubborn': time.sleep(60)
        break
