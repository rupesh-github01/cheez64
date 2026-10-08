"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { listGames, type ListGamesParams } from "@/lib/api";
import type { Game } from "@/lib/types";

type StatusFilter = "" | "ANALYZED" | "NOT_ANALYZED";
type ResultFilter = "" | "1-0" | "0-1" | "1/2-1/2";

export default function GamesPage() {
  const router = useRouter();
  const [games, setGames] = useState<Game[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("");
  const [resultFilter, setResultFilter] = useState<ResultFilter>("");

  useEffect(() => {
    setLoading(true);
    setError(null);

    const params: ListGamesParams = { limit: 100 };
    if (statusFilter) params.analysis_status = statusFilter;
    if (resultFilter) params.result = resultFilter;

    listGames(params)
      .then((res) => {
        setGames(res.games);
        setTotal(res.total);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [statusFilter, resultFilter]);

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px" }}>
      <div style={{ marginBottom: 24 }}>
        <h1
          style={{
            fontSize: "1.5rem",
            fontWeight: 700,
            marginBottom: 4,
            letterSpacing: "-0.02em",
          }}
        >
          Game Library
        </h1>
        <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
          {total} game{total !== 1 ? "s" : ""} in library
        </p>
      </div>

      {/* Filters */}
      <div
        style={{
          display: "flex",
          gap: 12,
          marginBottom: 20,
          flexWrap: "wrap",
        }}
      >
        <div className="filter-tabs">
          <button
            className={`filter-tab ${statusFilter === "" ? "active" : ""}`}
            onClick={() => setStatusFilter("")}
          >
            All
          </button>
          <button
            className={`filter-tab ${statusFilter === "ANALYZED" ? "active" : ""}`}
            onClick={() => setStatusFilter("ANALYZED")}
          >
            Analyzed
          </button>
          <button
            className={`filter-tab ${statusFilter === "NOT_ANALYZED" ? "active" : ""}`}
            onClick={() => setStatusFilter("NOT_ANALYZED")}
          >
            Not Analyzed
          </button>
        </div>

        <div className="filter-tabs">
          <button
            className={`filter-tab ${resultFilter === "" ? "active" : ""}`}
            onClick={() => setResultFilter("")}
          >
            Any result
          </button>
          <button
            className={`filter-tab ${resultFilter === "1-0" ? "active" : ""}`}
            onClick={() => setResultFilter("1-0")}
          >
            1-0
          </button>
          <button
            className={`filter-tab ${resultFilter === "0-1" ? "active" : ""}`}
            onClick={() => setResultFilter("0-1")}
          >
            0-1
          </button>
          <button
            className={`filter-tab ${resultFilter === "1/2-1/2" ? "active" : ""}`}
            onClick={() => setResultFilter("1/2-1/2")}
          >
            ½-½
          </button>
        </div>
      </div>

      {/* Content */}
      {loading && (
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            padding: 48,
            color: "var(--text-muted)",
          }}
        >
          Loading games...
        </div>
      )}

      {error && (
        <div
          className="card"
          style={{
            padding: 20,
            textAlign: "center",
            color: "var(--accent-red)",
          }}
        >
          <p style={{ fontWeight: 600, marginBottom: 8 }}>
            Failed to load games
          </p>
          <p style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
            {error}
          </p>
          <p
            style={{
              fontSize: "0.8rem",
              color: "var(--text-muted)",
              marginTop: 12,
            }}
          >
            Make sure the API server is running:{" "}
            <code
              style={{
                background: "var(--bg-secondary)",
                padding: "2px 6px",
                borderRadius: 4,
              }}
            >
              python3 api/main.py
            </code>
          </p>
        </div>
      )}

      {!loading && !error && games.length === 0 && (
        <div
          className="card"
          style={{
            padding: 40,
            textAlign: "center",
            color: "var(--text-muted)",
          }}
        >
          No games found.
        </div>
      )}

      {!loading && !error && games.length > 0 && (
        <div className="card" style={{ overflow: "hidden" }}>
          {/* Header */}
          <div
            className="game-row"
            style={{
              background: "var(--bg-secondary)",
              cursor: "default",
              fontWeight: 600,
              fontSize: "0.72rem",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-muted)",
            }}
          >
            <span>White</span>
            <span>Black</span>
            <span>Result</span>
            <span>Date</span>
            <span>Opening</span>
            <span>Status</span>
          </div>

          {/* Rows */}
          {games.map((g, i) => (
            <div
              key={g.game_id}
              className="game-row animate-fade-in"
              style={{ animationDelay: `${i * 20}ms` }}
              onClick={() => router.push(`/games/${g.game_id}`)}
            >
              <span style={{ fontSize: "0.85rem", fontWeight: 500 }}>
                {g.white}
                {g.white_rating && (
                  <span
                    style={{
                      color: "var(--text-muted)",
                      fontSize: "0.75rem",
                      marginLeft: 4,
                    }}
                  >
                    ({g.white_rating})
                  </span>
                )}
              </span>
              <span style={{ fontSize: "0.85rem", fontWeight: 500 }}>
                {g.black}
                {g.black_rating && (
                  <span
                    style={{
                      color: "var(--text-muted)",
                      fontSize: "0.75rem",
                      marginLeft: 4,
                    }}
                  >
                    ({g.black_rating})
                  </span>
                )}
              </span>
              <span
                style={{
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  color:
                    g.result === "1-0"
                      ? "var(--text-primary)"
                      : g.result === "0-1"
                        ? "var(--text-secondary)"
                        : "var(--text-muted)",
                }}
              >
                {g.result}
              </span>
              <span
                style={{
                  fontSize: "0.8rem",
                  color: "var(--text-secondary)",
                }}
              >
                {g.date
                  ? g.date.replace(/\./g, "-").slice(0, 10)
                  : "—"}
              </span>
              <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                {g.opening ? (
                  <>
                    <span
                      style={{
                        color: "var(--accent-cyan)",
                        fontWeight: 600,
                        marginRight: 4,
                      }}
                    >
                      {g.opening.eco}
                    </span>
                    {g.opening.opening_name}
                  </>
                ) : (
                  <span style={{ color: "var(--text-muted)" }}>—</span>
                )}
              </span>
              <span>
                <span
                  className={`badge badge-${g.analysis_status === "ANALYZED" ? "analyzed" : g.analysis_status === "NOT_ANALYZED" ? "not-analyzed" : g.analysis_status === "ANALYZING" ? "analyzing" : "failed"}`}
                >
                  {g.analysis_status === "ANALYZED"
                    ? "Analyzed"
                    : g.analysis_status === "NOT_ANALYZED"
                      ? "Pending"
                      : g.analysis_status}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
