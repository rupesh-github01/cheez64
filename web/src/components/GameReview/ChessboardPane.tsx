"use client";

import type {RefCallback} from "react";
import styles from "./review.module.css";
import { Chessboard } from "react-chessboard";
import type { MoveData } from "@/lib/types";

interface Props {
  fen: string;
  currentMove: MoveData | null;
  onPrev: () => void;
  onNext: () => void;
  onStart: () => void;
  onEnd: () => void;
  variationBanner?: { label: string; step: number; total: number } | null;
  variationSquares?: { from?: string; to?: string } | null;
  onExitVariation?: () => void;
  evaluationRailRef?: RefCallback<HTMLDivElement>;
  currentPly?: number;
  maxPly?: number;
  lastMove?: string;
}

export default function ChessboardPane({
  fen,
  currentMove,
  onPrev,
  onNext,
  onStart,
  onEnd,
  variationBanner,
  variationSquares,
  onExitVariation,
  evaluationRailRef, currentPly, maxPly, lastMove,
}: Props) {
  // Highlight squares for played move (amber) and best move (green) on critical positions
  const customSquareStyles: Record<string, React.CSSProperties> = {};

  if (variationSquares?.from && variationSquares?.to) {
    customSquareStyles[variationSquares.from] = {
      background: "rgba(59, 130, 246, 0.4)",
      borderRadius: "50%",
    };
    customSquareStyles[variationSquares.to] = {
      background: "rgba(59, 130, 246, 0.5)",
      border: "2px solid rgba(147, 197, 253, 0.8)",
    };
  } else if (currentMove) {
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

  if (!currentMove && lastMove?.length && lastMove.length >= 4) {
    customSquareStyles[lastMove.slice(0,2)] = {background:"rgba(234,179,8,.3)"};
    customSquareStyles[lastMove.slice(2,4)] = {background:"rgba(234,179,8,.4)"};
  }
  return (
    <div className={styles.boardStage}>
      {/* Variation banner if stepping through PV */}
      {variationBanner && (
        <div className="variation-banner">
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: "0.78rem", color: "white", fontWeight: 700 }}>
              ♟ {variationBanner.label}
            </span>
            <span style={{ fontSize: "0.72rem", color: "#93c5fd" }}>
              ({variationBanner.step}/{variationBanner.total})
            </span>
          </div>
          {onExitVariation && (
            <button
              className="copy-btn"
              style={{
                background: "rgba(239, 68, 68, 0.2)",
                color: "white",
                borderColor: "rgba(239, 68, 68, 0.4)",
              }}
              onClick={onExitVariation}
              title="Return to actual game position"
            >
              Exit (Esc)
            </button>
          )}
        </div>
      )}

      <div className={styles.boardRow}>
        {evaluationRailRef && <div className={styles.evaluationRail} ref={evaluationRailRef} aria-label="Selected position evaluation bar"/>}
      <div className={styles.board}>
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

      </div>
      {/* Navigation controls */}
      <nav className={styles.boardNavigation} aria-label="Move navigation">
        <button type="button" disabled={currentPly===0} onClick={onStart} aria-label="Start" title="Start (Home)">⏮</button>
        <button type="button" disabled={currentPly===0} onClick={onPrev} aria-label="Previous" title="Previous (←)">←</button>
        <span aria-live="polite">{currentPly===undefined?'Recorded position':`Ply ${currentPly} / ${maxPly}`}</span>
        <button type="button" disabled={maxPly!==undefined&&currentPly===maxPly} onClick={onNext} aria-label="Next" title="Next (→)">→</button>
        <button type="button" disabled={maxPly!==undefined&&currentPly===maxPly} onClick={onEnd} aria-label="End" title="End (End)">⏭</button>
      </nav>
    </div>
  );
}
