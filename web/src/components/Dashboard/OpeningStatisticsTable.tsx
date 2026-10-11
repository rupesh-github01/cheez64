"use client";

import React, { useState } from "react";
import Link from "next/link";
import type { DashboardOpeningEntry } from "@/lib/types";

interface OpeningStatisticsTableProps {
  openings: DashboardOpeningEntry[];
}

export function OpeningStatisticsTable({ openings }: OpeningStatisticsTableProps) {
  const [filterColor, setFilterColor] = useState<"ALL" | "White" | "Black">("ALL");

  if (!openings || openings.length === 0) {
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
        No opening repertoire statistics available yet.
      </div>
    );
  }

  const filtered = openings.filter((o) => {
    if (filterColor === "ALL") return true;
    return o.color.toLowerCase() === filterColor.toLowerCase();
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Header and Filter */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
            Opening Repertoire & Theory Exit
          </h2>
          <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
            Observed opening lines, win rates, and post-theory evaluation transitions
          </span>
        </div>

        <div style={{ display: "flex", gap: "8px" }}>
          {(["ALL", "White", "Black"] as const).map((colorKey) => (
            <button
              key={colorKey}
              onClick={() => setFilterColor(colorKey)}
              style={{
                padding: "6px 14px",
                fontSize: "0.75rem",
                fontWeight: 600,
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border-color)",
                background: filterColor === colorKey ? "var(--bg-active)" : "var(--bg-tertiary)",
                color: filterColor === colorKey ? "var(--accent-blue)" : "var(--text-secondary)",
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
            >
              {colorKey === "ALL" ? "All Openings" : `As ${colorKey}`}
            </button>
          ))}
        </div>
      </div>

      {/* Repertoire Warning / Methodology Note */}
      <div
        style={{
          padding: "10px 14px",
          background: "rgba(234, 179, 8, 0.08)",
          borderLeft: "3px solid var(--accent-yellow)",
          borderRadius: "0 var(--radius-sm) var(--radius-sm) 0",
          fontSize: "0.75rem",
          color: "var(--text-secondary)",
          lineHeight: 1.5,
        }}
      >
        <strong>Evidence Discipline:</strong> Repertoire changes are only recommended when an opening appears repeatedly (≥2 games) with verified post-theory transition drops. Single-game occurrences are labeled as preliminary.
      </div>

      {/* Table / Cards */}
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {filtered.map((op, idx) => {
          const winRatePct = Math.round(op.win_rate * 100);
          const inCpl = op.before_theory.avg_cpl !== null ? `${op.before_theory.avg_cpl.toFixed(1)} cp` : "-";
          const outCpl = op.after_theory.avg_cpl !== null ? `${op.after_theory.avg_cpl.toFixed(1)} cp` : "-";

          return (
            <div
              key={idx}
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--radius-sm)",
                padding: "14px 18px",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
              }}
            >
              {/* Row 1: Name, ECO, Color, Sample Badge */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  <span
                    style={{
                      background: "rgba(59, 130, 246, 0.15)",
                      color: "var(--accent-blue)",
                      fontSize: "0.75rem",
                      fontWeight: 700,
                      padding: "2px 8px",
                      borderRadius: "4px",
                    }}
                  >
                    {op.eco}
                  </span>
                  <span style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--text-primary)" }}>
                    {op.opening_name}
                  </span>
                  {op.variation_name && (
                    <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                      ({op.variation_name})
                    </span>
                  )}
                  <span
                    style={{
                      fontSize: "0.7rem",
                      background: "var(--bg-tertiary)",
                      color: "var(--text-muted)",
                      padding: "2px 6px",
                      borderRadius: "4px",
                    }}
                  >
                    as {op.color}
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  {op.is_preliminary && (
                    <span
                      style={{
                        fontSize: "0.7rem",
                        background: "rgba(234, 179, 8, 0.12)",
                        color: "var(--accent-yellow)",
                        padding: "2px 8px",
                        borderRadius: "12px",
                        fontWeight: 600,
                      }}
                    >
                      {op.sample_label}
                    </span>
                  )}
                  <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--text-primary)" }}>
                    {op.wins}W – {op.draws}D – {op.losses}L ({winRatePct}%)
                  </span>
                </div>
              </div>

              {/* Row 2: In-Theory vs Out-of-Theory Performance */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                  gap: "10px",
                  fontSize: "0.8rem",
                  background: "var(--bg-secondary)",
                  padding: "10px 14px",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                <div>
                  <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>In Theory (Moves 1–{op.before_theory.moves_count}):</span>
                  <div style={{ fontWeight: 600, color: "var(--text-primary)", marginTop: "2px" }}>
                    Avg CPL: <strong style={{ color: "var(--accent-green)" }}>{inCpl}</strong>
                    {" • "}
                    <span>Errors: {op.before_theory.critical_mistakes}</span>
                  </div>
                </div>

                <div>
                  <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>Post-Theory (Moves {op.before_theory.moves_count + 1}+):</span>
                  <div style={{ fontWeight: 600, color: "var(--text-primary)", marginTop: "2px" }}>
                    Avg CPL: <strong style={{ color: op.after_theory.avg_cpl && op.after_theory.avg_cpl > 100 ? "var(--accent-red)" : "var(--accent-yellow)" }}>{outCpl}</strong>
                    {" • "}
                    <span>Errors: {op.after_theory.critical_mistakes} ({op.after_theory.blunders} blunders)</span>
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
                  {op.game_ids && op.game_ids.length > 0 && (
                    <Link
                      href={`/games/${op.game_ids[0]}`}
                      style={{
                        fontSize: "0.75rem",
                        color: "var(--accent-blue)",
                        textDecoration: "none",
                        fontWeight: 600,
                        background: "rgba(59, 130, 246, 0.1)",
                        padding: "4px 10px",
                        borderRadius: "4px",
                      }}
                    >
                      View Game Review →
                    </Link>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
