"use client";

import React from "react";
import type { DashboardConfidence } from "@/lib/types";

interface MethodologicalLimitationsProps {
  confidence: DashboardConfidence;
  limitations: string[];
}

export function MethodologicalLimitations({ confidence, limitations }: MethodologicalLimitationsProps) {
  const confBadgeBg =
    confidence.level === "high"
      ? "rgba(34, 197, 94, 0.15)"
      : confidence.level === "moderate"
      ? "rgba(59, 130, 246, 0.15)"
      : "rgba(234, 179, 8, 0.15)";
  const confBadgeColor =
    confidence.level === "high"
      ? "var(--accent-green)"
      : confidence.level === "moderate"
      ? "var(--accent-blue)"
      : "var(--accent-yellow)";

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius-md)",
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
        <h3 style={{ fontSize: "0.95rem", fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
          Methodological Constraints & Evidence Discipline
        </h3>
        <span
          style={{
            fontSize: "0.75rem",
            background: confBadgeBg,
            color: confBadgeColor,
            padding: "2px 10px",
            borderRadius: "12px",
            fontWeight: 700,
          }}
        >
          {confidence.label}
        </span>
      </div>

      <p style={{ margin: 0, fontSize: "0.8rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>
        {confidence.warning}
      </p>

      {limitations && limitations.length > 0 && (
        <ul style={{ margin: "4px 0 0", paddingLeft: "18px", display: "flex", flexDirection: "column", gap: "6px" }}>
          {limitations.map((lim, idx) => (
            <li key={idx} style={{ fontSize: "0.8rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
              {lim}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
