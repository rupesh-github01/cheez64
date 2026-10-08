"use client";

import type { Episode } from "@/lib/types";

interface Props {
  episodes: Episode[];
  currentPly: number;
  onSelectEpisode: (startPly: number) => void;
}

export default function EpisodeList({
  episodes,
  currentPly,
  onSelectEpisode,
}: Props) {
  if (!episodes || episodes.length === 0) return null;

  return (
    <div>
      <div
        style={{
          fontSize: "0.72rem",
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-muted)",
          marginBottom: 8,
        }}
      >
        Episodes ({episodes.length})
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {episodes.map((ep, idx) => {
          const moves = ep.moves || [];
          const firstMove = moves[0];
          const lastMove = moves[moves.length - 1];

          const calcPly = (m?: { move_number: number; color: string; ply?: number }) => {
            if (!m) return 0;
            if (typeof m.ply === "number") return m.ply;
            return (m.move_number - 1) * 2 + (m.color === "White" ? 1 : 2);
          };

          const startPly = ep.start_ply ?? calcPly(firstMove);
          const endPly = ep.end_ply ?? calcPly(lastMove);
          const isActive = currentPly >= startPly && currentPly <= endPly;

          const rawCategory =
            typeof ep.category === "string"
              ? ep.category
              : typeof firstMove?.tactical_finding?.category === "string"
              ? firstMove.tactical_finding.category
              : typeof firstMove?.classification === "string"
              ? firstMove.classification
              : "sequence";
          const categoryName = (rawCategory || "sequence").replace(/_/g, " ");
          const epIndex = ep.episode_index ?? idx + 1;
          const count = moves.length || ep.move_count || 1;

          return (
            <button
              key={epIndex}
              className={`episode-badge ${isActive ? "active" : ""}`}
              onClick={() => onSelectEpisode(startPly)}
              title={`Episode ${epIndex}: ${categoryName} (ply ${startPly}–${endPly})`}
            >
              <span style={{ fontWeight: 700 }}>E{epIndex}</span>
              <span>{categoryName}</span>
              <span style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>
                {count} move{count !== 1 ? "s" : ""}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
