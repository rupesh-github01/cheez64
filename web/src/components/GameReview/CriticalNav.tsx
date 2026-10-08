"use client";

import type { MoveData } from "@/lib/types";

interface Props {
  criticalMoves: MoveData[];
  currentPly: number;
  onNavigate: (ply: number) => void;
}

export default function CriticalNav({
  criticalMoves,
  currentPly,
  onNavigate,
}: Props) {
  if (criticalMoves.length === 0) return null;

  // Find current critical index
  const currentIdx = criticalMoves.findIndex((m) => m.ply === currentPly);
  const isOnCritical = currentIdx >= 0;

  // Find prev/next critical relative to currentPly
  const prevCritical = criticalMoves
    .filter((m) => m.ply < currentPly)
    .pop();
  const nextCritical = criticalMoves.find((m) => m.ply > currentPly);

  const criticalPosition = isOnCritical
    ? `${currentIdx + 1} of ${criticalMoves.length}`
    : `${criticalMoves.length} critical`;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <button
        className="crit-nav-btn"
        disabled={!prevCritical}
        onClick={() => prevCritical && onNavigate(prevCritical.ply)}
      >
        ↑ Prev critical
      </button>

      <span
        style={{
          fontSize: "0.78rem",
          color: isOnCritical ? "var(--accent-blue)" : "var(--text-muted)",
          fontWeight: isOnCritical ? 600 : 400,
          minWidth: 80,
          textAlign: "center",
        }}
      >
        {criticalPosition}
      </span>

      <button
        className="crit-nav-btn"
        disabled={!nextCritical}
        onClick={() => nextCritical && onNavigate(nextCritical.ply)}
      >
        Next critical ↓
      </button>
    </div>
  );
}
