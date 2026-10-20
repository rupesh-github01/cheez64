"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { getDashboard } from "@/lib/api";
import type { DashboardData } from "@/lib/types";
import { PerformanceOverviewCards } from "@/components/Dashboard/PerformanceOverviewCards";
import { PerformanceTrendsChart } from "@/components/Dashboard/PerformanceTrendsChart";
import { CoachingPriorities } from "@/components/Dashboard/CoachingPriorities";
import { DemonstratedStrengths } from "@/components/Dashboard/DemonstratedStrengths";
import { OpeningStatisticsTable } from "@/components/Dashboard/OpeningStatisticsTable";
import { MethodologicalLimitations } from "@/components/Dashboard/MethodologicalLimitations";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadDashboard() {
      setLoading(true);
      setError(null);
      try {
        const res = await getDashboard();
        setData(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load dashboard data");
      } finally {
        setLoading(false);
      }
    }

    loadDashboard();
  }, []);

  if (loading) {
    return (
      <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "40px 24px" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
          <div style={{ height: "40px", width: "300px", background: "var(--bg-secondary)", borderRadius: "var(--radius-sm)" }} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
            {[1, 2, 3, 4].map((i) => (
              <div key={i} style={{ height: "140px", background: "var(--bg-secondary)", borderRadius: "var(--radius-md)" }} />
            ))}
          </div>
          <div style={{ height: "280px", background: "var(--bg-secondary)", borderRadius: "var(--radius-md)" }} />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ maxWidth: "800px", margin: "60px auto", padding: "24px", textAlign: "center" }}>
        <div
          style={{
            background: "rgba(239, 68, 68, 0.1)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            borderRadius: "var(--radius-md)",
            padding: "32px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: "2rem" }}>⚠️</span>
          <h2 style={{ margin: 0, color: "var(--accent-red)", fontSize: "1.2rem" }}>
            Unable to Load Dashboard
          </h2>
          <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: "0.9rem" }}>{error}</p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: "8px 18px",
              background: "var(--accent-blue)",
              color: "#fff",
              border: "none",
              borderRadius: "var(--radius-sm)",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data || data.overview.total_library_games === 0) {
    return (
      <div style={{ maxWidth: "800px", margin: "60px auto", padding: "24px", textAlign: "center" }}>
        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--radius-md)",
            padding: "48px 32px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: "2.5rem" }}>♟</span>
          <h2 style={{ margin: 0, color: "var(--text-primary)", fontSize: "1.4rem" }}>
            Your Game Library is Empty
          </h2>
          <p style={{ margin: 0, color: "var(--text-secondary)", maxWidth: "460px", lineHeight: 1.5 }}>
            To view aggregated performance statistics, coaching priorities, and opening repertoire metrics, import and analyze games in your library.
          </p>
          <Link
            href="/games"
            style={{
              marginTop: "8px",
              padding: "10px 20px",
              background: "var(--accent-blue)",
              color: "#fff",
              textDecoration: "none",
              borderRadius: "var(--radius-sm)",
              fontWeight: 600,
            }}
          >
            Go to Game Library →
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "32px 24px 64px", display: "flex", flexDirection: "column", gap: "32px" }}>
      {/* Page Title & Target Player Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <h1 style={{ fontSize: "1.6rem", fontWeight: 800, margin: 0, letterSpacing: "-0.02em", color: "var(--text-primary)" }}>
              Player Dashboard
            </h1>
            <span
              style={{
                fontSize: "0.85rem",
                fontWeight: 700,
                color: "var(--accent-blue)",
                background: "rgba(59, 130, 246, 0.15)",
                padding: "2px 10px",
                borderRadius: "12px",
              }}
            >
              {data.player_name}
            </span>
          </div>
          <p style={{ margin: "6px 0 0", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
            Evidence-grounded chess analytics across analyzed games from your library.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Link
            href="/games"
            style={{
              padding: "8px 16px",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-secondary)",
              color: "var(--text-primary)",
              textDecoration: "none",
              fontSize: "0.85rem",
              fontWeight: 600,
            }}
          >
            Browse Game Library
          </Link>
        </div>
      </div>

      {/* 1. Overall Performance Cards */}
      <section>
        <PerformanceOverviewCards overview={data.overview} />
      </section>

      {/* 2. Chronological Performance Trends */}
      <section>
        <PerformanceTrendsChart trends={data.trends} />
      </section>

      {/* 3. Coaching Recommendations & Weaknesses */}
      <section>
        <CoachingPriorities recommendations={data.recommendations} />
      </section>

      {/* 4. Demonstrated Strengths */}
      {data.strengths && data.strengths.length > 0 && (
        <section>
          <DemonstratedStrengths strengths={data.strengths} />
        </section>
      )}

      {/* 5. Opening Statistics & Repertoire */}
      <section>
        <OpeningStatisticsTable openings={data.openings} />
      </section>

      {/* 6. Methodological Limitations & Evidence Discipline */}
      <section>
        <MethodologicalLimitations confidence={data.confidence} limitations={data.limitations} />
      </section>
    </div>
  );
}
