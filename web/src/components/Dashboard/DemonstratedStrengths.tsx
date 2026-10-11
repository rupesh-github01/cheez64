"use client";

import React from "react";
import Link from "next/link";
import type { DashboardStrength } from "@/lib/types";

interface DemonstratedStrengthsProps {
  strengths: DashboardStrength[];
}

export function DemonstratedStrengths({ strengths }: DemonstratedStrengthsProps) {
  if (!strengths || strengths.length === 0) {
    return null;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
          Demonstrated Strengths
        </h2>
        <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
          Verified positive habits grounded in consistent multi-game evidence
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "12px" }}>
        {strengths.map((str, idx) => (
          <div
            key={idx}
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border-color)",
              borderLeft: "3px solid var(--accent-green)",
              borderRadius: "var(--radius-md)",
              padding: "18px 20px",
              display: "flex",
              flexDirection: "column",
              gap: "10px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "8px" }}>
              <span style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--text-primary)" }}>
                {str.area}
              </span>
              <span
                style={{
                  fontSize: "0.7rem",
                  background: "rgba(34, 197, 94, 0.15)",
                  color: "var(--accent-green)",
                  padding: "2px 8px",
                  borderRadius: "12px",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                across {str.games_ratio} games
              </span>
            </div>

            <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {str.description}
            </p>

            {str.sample_evidence && str.sample_evidence.length > 0 && (
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "4px" }}>
                {str.sample_evidence.map((ev, evIdx) => (
                  <div key={evIdx} style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    {ev.real_game_id ? (
                      <Link
                        href={`/games/${ev.real_game_id}`}
                        style={{
                          color: "var(--accent-blue)",
                          textDecoration: "none",
                          background: "var(--bg-secondary)",
                          padding: "2px 8px",
                          borderRadius: "4px",
                        }}
                      >
                        Example Game ({ev.color || "Review"}) →
                      </Link>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
