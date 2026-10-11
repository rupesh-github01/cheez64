"use client";

import React from "react";
import type { DashboardOverview } from "@/lib/types";

interface PerformanceOverviewCardsProps {
  overview: DashboardOverview;
}

export function PerformanceOverviewCards({ overview }: PerformanceOverviewCardsProps) {
  const {
    total_library_games,
    library_analyzed_games,
    library_unanalyzed_games,
    results,
    white_stats,
    black_stats,
    analyzed_metrics,
  } = overview;

  const analyzedPercent =
    total_library_games > 0
      ? Math.round((library_analyzed_games / total_library_games) * 100)
      : 0;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px" }}>
      {/* Card 1: Library Coverage */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius-md)",
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: "0.8rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)", fontWeight: 600 }}>
            Library Coverage
          </span>
          <span
            style={{
              fontSize: "0.7rem",
              background: "rgba(59, 130, 246, 0.15)",
              color: "var(--accent-blue)",
              padding: "2px 8px",
              borderRadius: "12px",
              fontWeight: 600,
            }}
          >
            Library-Wide
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
          <span style={{ fontSize: "2rem", fontWeight: 700, color: "var(--text-primary)" }}>
            {total_library_games}
          </span>
          <span style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>total games</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
            <span>Analyzed: <strong style={{ color: "var(--accent-green)" }}>{library_analyzed_games}</strong></span>
            <span>Unanalyzed: <strong style={{ color: "var(--text-muted)" }}>{library_unanalyzed_games}</strong></span>
          </div>
          <div style={{ height: "6px", background: "var(--bg-tertiary)", borderRadius: "3px", overflow: "hidden" }}>
            <div
              style={{
                width: `${analyzedPercent}%`,
                height: "100%",
                background: "var(--accent-green)",
                borderRadius: "3px",
              }}
            />
          </div>
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            {analyzedPercent}% of games fully analyzed by engine
          </span>
        </div>
      </div>

      {/* Card 2: Match Results & Win Rate */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius-md)",
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: "0.8rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)", fontWeight: 600 }}>
            Game Results & Win Rate
          </span>
          <span
            style={{
              fontSize: "0.7rem",
              background: "rgba(34, 197, 94, 0.15)",
              color: "var(--accent-green)",
              padding: "2px 8px",
              borderRadius: "12px",
              fontWeight: 600,
            }}
          >
            All Games
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
          <span style={{ fontSize: "2rem", fontWeight: 700, color: "var(--text-primary)" }}>
            {Math.round(results.win_rate * 100)}%
          </span>
          <span style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>
            ({results.wins}W – {results.draws}D – {results.losses}L)
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-color)", paddingBottom: "4px" }}>
            <span>As White:</span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {white_stats.wins}W – {white_stats.draws}D – {white_stats.losses}L ({Math.round(white_stats.win_rate * 100)}%)
            </span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", paddingTop: "2px" }}>
            <span>As Black:</span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {black_stats.wins}W – {black_stats.draws}D – {black_stats.losses}L ({Math.round(black_stats.win_rate * 100)}%)
            </span>
          </div>
        </div>
      </div>

      {/* Card 3: Engine Accuracy (CPL) */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius-md)",
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: "0.8rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)", fontWeight: 600 }}>
            Move Accuracy (Avg CPL)
          </span>
          <span
            style={{
              fontSize: "0.7rem",
              background: "rgba(234, 179, 8, 0.15)",
              color: "var(--accent-yellow)",
              padding: "2px 8px",
              borderRadius: "12px",
              fontWeight: 600,
            }}
          >
            Analyzed Only
          </span>
        </div>
        {analyzed_metrics ? (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
              <span style={{ fontSize: "2rem", fontWeight: 700, color: "var(--accent-yellow)" }}>
                {analyzed_metrics.average_cpl}
              </span>
              <span style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>cp loss / move</span>
            </div>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
              Computed across <strong>{analyzed_metrics.moves_analyzed}</strong> moves in <strong>{analyzed_metrics.games_analyzed}</strong> analyzed games.
              <br />
              <span style={{ color: "var(--text-secondary)" }}>Lower centipawn loss indicates higher engine alignment.</span>
            </div>
          </>
        ) : (
          <div style={{ padding: "12px 0", color: "var(--text-muted)", fontSize: "0.85rem" }}>
            No analyzed games available to compute centipawn loss.
          </div>
        )}
      </div>

      {/* Card 4: Move Quality Breakdown */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius-md)",
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: "0.8rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)", fontWeight: 600 }}>
            Move Classifications
          </span>
          <span
            style={{
              fontSize: "0.7rem",
              background: "rgba(239, 68, 68, 0.15)",
              color: "var(--accent-red)",
              padding: "2px 8px",
              borderRadius: "12px",
              fontWeight: 600,
            }}
          >
            Analyzed Only
          </span>
        </div>
        {analyzed_metrics ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
            <div
              style={{
                background: "rgba(34, 197, 94, 0.1)",
                border: "1px solid rgba(34, 197, 94, 0.2)",
                borderRadius: "var(--radius-sm)",
                padding: "8px 10px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <span style={{ fontSize: "0.7rem", color: "var(--accent-green)", fontWeight: 600 }}>Good & Best</span>
              <span style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--accent-green)" }}>
                {analyzed_metrics.classifications.good}
              </span>
            </div>
            <div
              style={{
                background: "rgba(234, 179, 8, 0.1)",
                border: "1px solid rgba(234, 179, 8, 0.2)",
                borderRadius: "var(--radius-sm)",
                padding: "8px 10px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <span style={{ fontSize: "0.7rem", color: "var(--accent-yellow)", fontWeight: 600 }}>Inaccuracies</span>
              <span style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--accent-yellow)" }}>
                {analyzed_metrics.classifications.inaccuracy}
              </span>
            </div>
            <div
              style={{
                background: "rgba(249, 115, 22, 0.1)",
                border: "1px solid rgba(249, 115, 22, 0.2)",
                borderRadius: "var(--radius-sm)",
                padding: "8px 10px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <span style={{ fontSize: "0.7rem", color: "var(--accent-orange)", fontWeight: 600 }}>Mistakes</span>
              <span style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--accent-orange)" }}>
                {analyzed_metrics.classifications.mistake}
              </span>
            </div>
            <div
              style={{
                background: "rgba(239, 68, 68, 0.1)",
                border: "1px solid rgba(239, 68, 68, 0.2)",
                borderRadius: "var(--radius-sm)",
                padding: "8px 10px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <span style={{ fontSize: "0.7rem", color: "var(--accent-red)", fontWeight: 600 }}>Blunders</span>
              <span style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--accent-red)" }}>
                {analyzed_metrics.classifications.blunder}
              </span>
            </div>
          </div>
        ) : (
          <div style={{ padding: "12px 0", color: "var(--text-muted)", fontSize: "0.85rem" }}>
            No analyzed moves available.
          </div>
        )}
      </div>
    </div>
  );
}
