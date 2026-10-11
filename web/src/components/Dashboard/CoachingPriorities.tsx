"use client";

import React, { useState } from "react";
import Link from "next/link";
import type { CoachingRecommendationDetail } from "@/lib/types";

interface CoachingPrioritiesProps {
  recommendations: CoachingRecommendationDetail[];
}

export function CoachingPriorities({ recommendations }: CoachingPrioritiesProps) {
  const [expandedRecId, setExpandedRecId] = useState<string | null>(
    recommendations.length > 0 ? recommendations[0].recommendation_id : null
  );

  if (!recommendations || recommendations.length === 0) {
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
        No critical recurring weaknesses identified in the analyzed games sample.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
            Priority Coaching Recommendations
          </h2>
          <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
            Ranked by recurrence across games, mistake severity, and tactical confidence
          </span>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        {recommendations.map((rec) => {
          const isExpanded = expandedRecId === rec.recommendation_id;
          const priorityColor =
            rec.priority === 1
              ? "var(--accent-red)"
              : rec.priority === 2
              ? "var(--accent-orange)"
              : rec.priority === 3
              ? "var(--accent-yellow)"
              : "var(--accent-blue)";

          return (
            <div
              key={rec.recommendation_id}
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border-color)",
                borderRadius: "var(--radius-md)",
                overflow: "hidden",
                transition: "border-color 0.15s ease",
              }}
            >
              {/* Header card / summary */}
              <div
                onClick={() => setExpandedRecId(isExpanded ? null : rec.recommendation_id)}
                style={{
                  padding: "18px 20px",
                  cursor: "pointer",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: "16px",
                  background: isExpanded ? "var(--bg-secondary)" : "var(--bg-card)",
                }}
              >
                <div style={{ display: "flex", gap: "14px", alignItems: "flex-start" }}>
                  {/* Priority Number Badge */}
                  <div
                    style={{
                      width: "36px",
                      height: "36px",
                      borderRadius: "var(--radius-sm)",
                      background: "var(--bg-tertiary)",
                      border: `1.5px solid ${priorityColor}`,
                      color: priorityColor,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "1rem",
                      fontWeight: 700,
                      flexShrink: 0,
                    }}
                  >
                    #{rec.priority}
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                        {rec.title}
                      </span>
                      <span
                        style={{
                          fontSize: "0.7rem",
                          background: "var(--bg-tertiary)",
                          color: "var(--text-secondary)",
                          padding: "2px 8px",
                          borderRadius: "4px",
                          textTransform: "uppercase",
                          fontWeight: 600,
                        }}
                      >
                        {rec.category.replace(/_/g, " ")}
                      </span>
                      <span
                        style={{
                          fontSize: "0.7rem",
                          background: "rgba(59, 130, 246, 0.1)",
                          color: "var(--accent-blue)",
                          padding: "2px 8px",
                          borderRadius: "4px",
                          fontWeight: 600,
                        }}
                      >
                        Score: {rec.score.toFixed(1)}
                      </span>
                    </div>
                    <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.4 }}>
                      {rec.summary}
                    </span>
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--accent-blue)",
                      background: "rgba(59, 130, 246, 0.1)",
                      padding: "4px 10px",
                      borderRadius: "var(--radius-sm)",
                      fontWeight: 600,
                    }}
                  >
                    {rec.suggested_training_type}
                  </span>
                  <span style={{ fontSize: "0.9rem", color: "var(--text-muted)" }}>
                    {isExpanded ? "▲" : "▼"}
                  </span>
                </div>
              </div>

              {/* Expanded details */}
              {isExpanded && (
                <div
                  style={{
                    padding: "20px",
                    borderTop: "1px solid var(--border-color)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "18px",
                    background: "var(--bg-secondary)",
                  }}
                >
                  {/* Rationale */}
                  <div>
                    <span style={{ fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 700, color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                      Coaching Rationale
                    </span>
                    <p style={{ margin: "6px 0 0", fontSize: "0.85rem", color: "var(--text-primary)", lineHeight: 1.5 }}>
                      {rec.rationale}
                    </p>
                  </div>

                  {/* Evidence Points */}
                  {rec.evidence && rec.evidence.length > 0 && (
                    <div>
                      <span style={{ fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 700, color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                        Key Evidence from Analyzed Games
                      </span>
                      <ul style={{ margin: "6px 0 0", paddingLeft: "18px", display: "flex", flexDirection: "column", gap: "4px" }}>
                        {rec.evidence.map((ev, i) => (
                          <li key={i} style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                            {ev}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Clickable Example Positions */}
                  {rec.affected_moves && rec.affected_moves.length > 0 && (
                    <div>
                      <span style={{ fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 700, color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                        Example Positions to Inspect
                      </span>
                      <div
                        style={{
                          marginTop: "8px",
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
                          gap: "10px",
                        }}
                      >
                        {rec.affected_moves.map((m, idx) => (
                          <div
                            key={idx}
                            style={{
                              background: "var(--bg-card)",
                              border: "1px solid var(--border-color)",
                              borderRadius: "var(--radius-sm)",
                              padding: "12px 14px",
                              display: "flex",
                              flexDirection: "column",
                              gap: "8px",
                            }}
                          >
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                              <span style={{ fontWeight: 700, fontSize: "0.9rem", color: "var(--text-primary)" }}>
                                {m.move_text || `Move ${m.move_number}`}
                              </span>
                              <span
                                style={{
                                  fontSize: "0.75rem",
                                  fontWeight: 700,
                                  color: "var(--accent-red)",
                                  background: "rgba(239, 68, 68, 0.15)",
                                  padding: "2px 6px",
                                  borderRadius: "4px",
                                }}
                              >
                                -{m.cpl} cp
                              </span>
                            </div>

                            <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                              <span>Played: <strong style={{ color: "var(--accent-red)" }}>{m.played_move}</strong></span>
                              {" • "}
                              <span>Best: <strong style={{ color: "var(--accent-green)" }}>{m.best_move}</strong></span>
                            </div>

                            {m.summary && (
                              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontStyle: "italic", lineHeight: 1.4 }}>
                                &ldquo;{m.summary}&rdquo;
                              </div>
                            )}

                            {m.opponent && (
                              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                                vs. {m.opponent} {m.game_date ? `(${m.game_date})` : ""}
                              </div>
                            )}

                            {/* Direct link into Game Review at exact ply */}
                            <Link
                              href={`/games/${m.game_id}?ply=${m.ply}`}
                              style={{
                                marginTop: "4px",
                                fontSize: "0.75rem",
                                color: "var(--accent-blue)",
                                textDecoration: "none",
                                fontWeight: 600,
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "4px",
                              }}
                            >
                              Inspect Move in Game Review →
                            </Link>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Affected Games list */}
                  {rec.affected_games_detail && rec.affected_games_detail.length > 0 && (
                    <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                      <span>Affected Games:</span>
                      {rec.affected_games_detail.map((g, idx) => (
                        <Link
                          key={idx}
                          href={`/games/${g.game_id}`}
                          style={{
                            color: "var(--accent-blue)",
                            textDecoration: "none",
                            background: "var(--bg-tertiary)",
                            padding: "2px 8px",
                            borderRadius: "4px",
                          }}
                        >
                          {g.date ? `${g.date}` : `Game ${idx + 1}`} ({g.result || "*"})
                        </Link>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
