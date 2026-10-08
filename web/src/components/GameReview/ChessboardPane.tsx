"use client";

import { Chessboard } from "react-chessboard";
import type { MoveData } from "@/lib/types";

interface Props {
  fen: string;
  currentMove: MoveData | null;
  onPrev: () => void;
  onNext: () => void;
  onStart: () => void;
  onEnd: () => void;
}

export default function ChessboardPane({
  fen,
  currentMove,
  onPrev,
  onNext,
  onStart,
  onEnd,
}: Props) {
  // Highlight squares for played move (amber) and best move (green) on critical positions
  const customSquareStyles: Record<string, React.CSSProperties> = {};

  if (currentMove) {
    const playedUci = currentMove.played_move_uci;
    const bestUci = currentMove.best_move_uci;

    if (playedUci && playedUci.length >= 4) {
      const from = playedUci.slice(0, 2);
      const to = playedUci.slice(2, 4);
      customSquareStyles[from] = {
        background: "rgba(234, 179, 8, 0.3)",
        borderRadius: "50%",
      };
      customSquareStyles[to] = {
        background: "rgba(234, 179, 8, 0.4)",
      };
    }

    if (currentMove.is_critical && bestUci && bestUci.length >= 4) {
      const bestTo = bestUci.slice(2, 4);
      if (!customSquareStyles[bestTo]) {
        customSquareStyles[bestTo] = {
          background: "rgba(34, 197, 94, 0.35)",
          border: "2px solid rgba(34, 197, 94, 0.6)",
        };
      }
    }
  }

  return (
    <div>
      <div className="board-container">
        <Chessboard
          options={{
            position: fen,
            allowDragging: false,
            boardStyle: {
              borderRadius: "8px",
              boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
            },
            darkSquareStyle: { backgroundColor: "#779952" },
            lightSquareStyle: { backgroundColor: "#edeed1" },
            squareStyles: customSquareStyles,
          }}
        />
      </div>

      {/* Navigation controls */}
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          gap: 4,
          marginTop: 10,
        }}
      >
        <button className="crit-nav-btn" onClick={onStart} title="Start">
          ⏮
        </button>
        <button className="crit-nav-btn" onClick={onPrev} title="Previous (←)">
          ◀
        </button>
        <button className="crit-nav-btn" onClick={onNext} title="Next (→)">
          ▶
        </button>
        <button className="crit-nav-btn" onClick={onEnd} title="End">
          ⏭
        </button>
      </div>
    </div>
  );
}
