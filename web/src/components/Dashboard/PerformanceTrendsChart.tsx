"use client";

import React, { useState } from "react";
import Link from "next/link";
import type { DashboardTrendItem } from "@/lib/types";

interface PerformanceTrendsChartProps {
  trends: DashboardTrendItem[];
}

export function PerformanceTrendsChart({ trends }: PerformanceTrendsChartProps) {
  const [activeMetric, setActiveMetric] = useState<"cpl" | "blunders">("cpl");

  if (!trends || trends.length === 0) {
    return (
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border-color)",
          borderRadius: "var(--radius-md)",
          padding: "32px",
          textAlign: "center",
          color: "var(--text-muted)",
        }}
      >
        No chronological performance data available yet.
      </div>
    );
  }

  // Filter analyzed games for CPL calculations
  const analyzedGames = trends.filter((t) => t.is_analyzed && t.avg_cpl !== null);
  const maxCpl = Math.max(...analyzedGames.map((t) => t.avg_cpl || 0), 100);
  const maxBlunders = Math.max(...analyzedGames.map((t) => t.blunders || 0), 4);

  // SVG dimensions
  const svgWidth = 800;
  const svgHeight = 200;
  const paddingX = 40;
  const paddingY = 30;
  const chartWidth = svgWidth - paddingX * 2;
  const chartHeight = svgHeight - paddingY * 2;

  // Compute points
  const points = trends.map((t, index) => {
    const x =
      trends.length > 1
        ? paddingX + (index / (trends.length - 1)) * chartWidth
        : paddingX + chartWidth / 2;

    let y = paddingY + chartHeight / 2;
    if (activeMetric === "cpl" && t.avg_cpl !== null) {
      y = paddingY + chartHeight - (t.avg_cpl / maxCpl) * chartHeight;
    } else if (activeMetric === "blunders" && t.blunders !== null) {
      y = paddingY + chartHeight - (t.blunders / maxBlunders) * chartHeight;
    }

    return { ...t, x, y };
  });

  const pathD = points
    .filter((p) => p.is_analyzed)
    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius-md)",
        padding: "24px",
        display: "flex",
        flexDirection: "column",
        gap: "20px",
      }}
    >
      {/* Header and Toggle */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
            Performance Trends Over Time
          </h2>
          <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
            Chronological progression across {trends.length} recorded games
          </span>
        </div>

        <div style={{ display: "flex", gap: "8px" }}>
          <button
            onClick={() => setActiveMetric("cpl")}
            style={{
              padding: "6px 14px",
              fontSize: "0.75rem",
              fontWeight: 600,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-color)",
              background: activeMetric === "cpl" ? "var(--bg-active)" : "var(--bg-tertiary)",
              color: activeMetric === "cpl" ? "var(--accent-yellow)" : "var(--text-secondary)",
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            Avg Centipawn Loss
          </button>
          <button
            onClick={() => setActiveMetric("blunders")}
            style={{
              padding: "6px 14px",
              fontSize: "0.75rem",
              fontWeight: 600,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-color)",
              background: activeMetric === "blunders" ? "var(--bg-active)" : "var(--bg-tertiary)",
              color: activeMetric === "blunders" ? "var(--accent-red)" : "var(--text-secondary)",
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            Blunders per Game
          </button>
        </div>
      </div>

      {/* Lightweight SVG Visualizer */}
      <div style={{ width: "100%", overflowX: "auto" }}>
        <svg
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          style={{ width: "100%", minWidth: "560px", height: "auto", overflow: "visible" }}
        >
          {/* Background grid lines */}
          <line
            x1={paddingX}
            y1={paddingY}
            x2={paddingX + chartWidth}
            y2={paddingY}
            stroke="var(--border-color)"
            strokeDasharray="4 4"
          />
          <line
            x1={paddingX}
            y1={paddingY + chartHeight / 2}
            x2={paddingX + chartWidth}
            y2={paddingY + chartHeight / 2}
            stroke="var(--border-color)"
            strokeDasharray="4 4"
          />
          <line
            x1={paddingX}
            y1={paddingY + chartHeight}
            x2={paddingX + chartWidth}
            y2={paddingY + chartHeight}
            stroke="var(--border-color)"
          />

          {/* Value guidelines */}
          <text x={paddingX - 10} y={paddingY + 4} fill="var(--text-muted)" fontSize="10" textAnchor="end">
            {activeMetric === "cpl" ? `${Math.round(maxCpl)} cp` : `${maxBlunders}`}
          </text>
          <text x={paddingX - 10} y={paddingY + chartHeight / 2 + 4} fill="var(--text-muted)" fontSize="10" textAnchor="end">
            {activeMetric === "cpl" ? `${Math.round(maxCpl / 2)} cp` : `${Math.round(maxBlunders / 2)}`}
          </text>
          <text x={paddingX - 10} y={paddingY + chartHeight + 4} fill="var(--text-muted)" fontSize="10" textAnchor="end">
            0
          </text>

          {/* Trend line */}
          {pathD && (
            <path
              d={pathD}
              fill="none"
              stroke={activeMetric === "cpl" ? "var(--accent-yellow)" : "var(--accent-red)"}
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* Data Points */}
          {points.map((p, idx) => {
            const isWin = p.result === "win";
            const isLoss = p.result === "loss";
            const outcomeColor = isWin ? "var(--accent-green)" : isLoss ? "var(--accent-red)" : "var(--accent-yellow)";

            return (
              <g key={p.game_id}>
                {/* Result Indicator marker at bottom */}
                <circle cx={p.x} cy={paddingY + chartHeight + 14} r="4" fill={outcomeColor} />

                {/* Metric Node */}
                {p.is_analyzed && (
                  <>
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r="6"
                      fill="var(--bg-card)"
                      stroke={activeMetric === "cpl" ? "var(--accent-yellow)" : "var(--accent-red)"}
                      strokeWidth="2.5"
                    />
                    <text
                      x={p.x}
                      y={p.y - 10}
                      fill="var(--text-primary)"
                      fontSize="10"
                      fontWeight="600"
                      textAnchor="middle"
                    >
                      {activeMetric === "cpl" ? `${Math.round(p.avg_cpl || 0)}` : `${p.blunders || 0}`}
                    </text>
                  </>
                )}

                {/* X axis game number & date */}
                <text
                  x={p.x}
                  y={svgHeight - 4}
                  fill="var(--text-muted)"
                  fontSize="9"
                  textAnchor="middle"
                >
                  {p.date ? p.date.slice(5) : `G${idx + 1}`}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Legend & Rating Notes */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
          padding: "10px 14px",
          background: "var(--bg-secondary)",
          borderRadius: "var(--radius-sm)",
          fontSize: "0.75rem",
          color: "var(--text-secondary)",
        }}
      >
        <div style={{ display: "flex", gap: "16px", alignItems: "center" }}>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-green)" }} /> Win
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-red)" }} /> Loss
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-yellow)" }} /> Draw
          </span>
        </div>

        <div>
          <span>Platform Ratings recorded: </span>
          <strong style={{ color: "var(--text-primary)" }}>
            {trends.filter((t) => t.player_rating).map((t) => t.player_rating).join(" → ")}
          </strong>
        </div>
      </div>

      {/* Chronological Games List */}
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-secondary)" }}>
          Game Timeline
        </span>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {trends.map((t, idx) => {
            const isWin = t.result === "win";
            const isLoss = t.result === "loss";
            const outcomeBadge = isWin ? "WIN" : isLoss ? "LOSS" : "DRAW";
            const outcomeBg = isWin
              ? "rgba(34, 197, 94, 0.15)"
              : isLoss
              ? "rgba(239, 68, 68, 0.15)"
              : "rgba(234, 179, 8, 0.15)";
            const outcomeColor = isWin
              ? "var(--accent-green)"
              : isLoss
              ? "var(--accent-red)"
              : "var(--accent-yellow)";

            return (
              <div
                key={t.game_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "10px 14px",
                  background: "var(--bg-secondary)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-color)",
                  gap: "12px",
                  flexWrap: "wrap",
                }}
              >
                {/* Left: Index, Outcome, Opponent */}
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span style={{ color: "var(--text-muted)", fontSize: "0.8rem", width: "24px" }}>
                    #{idx + 1}
                  </span>
                  <span
                    style={{
                      background: outcomeBg,
                      color: outcomeColor,
                      fontSize: "0.7rem",
                      fontWeight: 700,
                      padding: "2px 8px",
                      borderRadius: "4px",
                    }}
                  >
                    {outcomeBadge}
                  </span>
                  <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-primary)" }}>
                    vs. {t.opponent}
                  </span>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--text-muted)",
                      background: "var(--bg-tertiary)",
                      padding: "2px 6px",
                      borderRadius: "4px",
                    }}
                  >
                    as {t.color}
                  </span>
                  {t.player_rating && (
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                      ({t.player_rating} vs {t.opponent_rating || "?"})
                    </span>
                  )}
                </div>

                {/* Middle: Opening */}
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--accent-blue)",
                      fontWeight: 600,
                      background: "rgba(59, 130, 246, 0.1)",
                      padding: "2px 6px",
                      borderRadius: "4px",
                    }}
                  >
                    {t.eco}
                  </span>
                  <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    {t.opening_name}
                  </span>
                </div>

                {/* Right: Accuracy & Game Link */}
                <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
                  {t.is_analyzed ? (
                    <div style={{ display: "flex", gap: "10px", fontSize: "0.75rem" }}>
                      <span style={{ color: "var(--accent-yellow)" }}>
                        <strong>{t.avg_cpl}</strong> CPL
                      </span>
                      <span style={{ color: "var(--accent-red)" }}>
                        <strong>{t.blunders}</strong> blunders
                      </span>
                      <span style={{ color: "var(--accent-orange)" }}>
                        <strong>{t.mistakes}</strong> mistakes
                      </span>
                    </div>
                  ) : (
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Unanalyzed</span>
                  )}

                  <Link
                    href={`/games/${t.game_id}`}
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--accent-blue)",
                      textDecoration: "none",
                      fontWeight: 600,
                      padding: "4px 10px",
                      borderRadius: "4px",
                      background: "rgba(59, 130, 246, 0.1)",
                      transition: "background 0.15s ease",
                    }}
                  >
                    Review Game →
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Discipline Disclaimer */}
      <div
        style={{
          borderLeft: "3px solid var(--accent-blue)",
          background: "rgba(59, 130, 246, 0.08)",
          padding: "10px 14px",
          borderRadius: "0 var(--radius-sm) var(--radius-sm) 0",
          fontSize: "0.75rem",
          color: "var(--text-secondary)",
          lineHeight: 1.5,
        }}
      >
        <strong>Methodology Discipline:</strong> Centipawn loss (CPL) reflects engine evaluation drop on each turn and is not a direct measure of human playing strength or rating progression. Ratings shown reflect metadata recorded at game time.
      </div>
    </div>
  );
}
