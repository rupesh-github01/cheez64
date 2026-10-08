"use client";

import type { MoveData } from "@/lib/types";
import { classificationColor } from "@/lib/types";

interface Props {
  moves: MoveData[];
  currentPly: number;
  onSelectMove: (ply: number) => void;
  highlightEpisode: number | null;
}

export default function MoveList({
  moves,
  currentPly,
  onSelectMove,
  highlightEpisode,
}: Props) {
  if (moves.length === 0) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: "0.85rem", padding: 8 }}>
        No moves match the current filter.
      </div>
    );
  }

  // Group moves into pairs (white, black) by move_number
  const rows: Array<{ moveNum: number; white?: MoveData; black?: MoveData }> = [];
  const moveMap = new Map<number, { white?: MoveData; black?: MoveData }>();

  for (const m of moves) {
    if (!moveMap.has(m.move_number)) {
      moveMap.set(m.move_number, {});
    }
    const entry = moveMap.get(m.move_number)!;
    if (m.color === "White") entry.white = m;
    else entry.black = m;
  }

  for (const [moveNum, pair] of Array.from(moveMap.entries()).sort(
    (a, b) => a[0] - b[0]
  )) {
    rows.push({ moveNum, ...pair });
  }

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "1px 2px", alignItems: "center" }}>
      {rows.map(({ moveNum, white, black }) => (
        <span key={moveNum} style={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
          <span
            style={{
              fontSize: "0.75rem",
              color: "var(--text-muted)",
              minWidth: 24,
              textAlign: "right",
              marginRight: 2,
              fontFamily: "monospace",
            }}
          >
            {moveNum}.
          </span>
          {white && (
            <MoveCell
              move={white}
              isActive={white.ply === currentPly}
              inEpisode={highlightEpisode !== null && white.episode_index === highlightEpisode}
              onClick={() => onSelectMove(white.ply)}
            />
          )}
          {black && (
            <MoveCell
              move={black}
              isActive={black.ply === currentPly}
              inEpisode={highlightEpisode !== null && black.episode_index === highlightEpisode}
              onClick={() => onSelectMove(black.ply)}
            />
          )}
        </span>
      ))}
    </div>
  );
}

function MoveCell({
  move,
  isActive,
  inEpisode,
  onClick,
}: {
  move: MoveData;
  isActive: boolean;
  inEpisode: boolean;
  onClick: () => void;
}) {
  const color = classificationColor(move.classification);
  const isCritical = move.is_critical;

  return (
    <span
      className={`move-item ${isActive ? "active" : ""} ${isCritical ? "critical" : ""}`}
      onClick={onClick}
      style={{
        borderLeft: isCritical ? `2px solid ${color}` : undefined,
        background: inEpisode && !isActive ? "rgba(59, 130, 246, 0.1)" : undefined,
      }}
      title={`${move.played_move} — ${move.classification} (${move.centipawn_loss} cp)`}
    >
      {move.played_move}
      {isCritical && (
        <span
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: color,
            marginLeft: 3,
            flexShrink: 0,
          }}
        />
      )}
    </span>
  );
}
