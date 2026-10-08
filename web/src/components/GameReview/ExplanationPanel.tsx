"use client";

import type { MoveData } from "@/lib/types";
import { classificationColor, classificationLabel } from "@/lib/types";

interface Props {
  move: MoveData;
}

function formatEval(val: number): string {
  if (Math.abs(val) >= 90000) {
    const moves = Math.max(1, 100000 - Math.abs(val));
    return val > 0 ? `+M${moves}` : `-M${moves}`;
  }
  const pawns = val / 100;
  return pawns >= 0 ? `+${pawns.toFixed(2)}` : pawns.toFixed(2);
}

export default function ExplanationPanel({ move }: Props) {
  const expl = move.explanation;
  const tf = move.tactical_finding;
  const cls = move.classification;
  const color = classificationColor(cls);

  return (
    <div>
      {/* Header: move + classification + CPL */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 12,
        }}
      >
        <span
          style={{
            fontWeight: 700,
            fontSize: "1rem",
            fontFamily: "monospace",
          }}
        >
          {move.move_number}
          {move.color === "White" ? "." : "..."} {move.played_move}
        </span>
        <span className={`badge badge-${cls}`}>{classificationLabel(cls)}</span>
        <span
          style={{
            fontSize: "0.78rem",
            color: "var(--text-muted)",
            fontFamily: "monospace",
          }}
        >
          {move.centipawn_loss > 0 ? `-${move.centipawn_loss} cp` : "0 cp"}
        </span>
        {move.best_move && move.best_move !== move.played_move && (
          <span
            style={{
              fontSize: "0.78rem",
              color: "var(--accent-green)",
              fontFamily: "monospace",
            }}
          >
            Best: {move.best_move}
          </span>
        )}
      </div>

      {/* Tactical finding badge */}
      {tf && (
        <div style={{ marginBottom: 10 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 10px",
              borderRadius: "9999px",
              fontSize: "0.72rem",
              fontWeight: 600,
              background: "rgba(239, 68, 68, 0.15)",
              color: "var(--accent-red)",
              border: "1px solid rgba(239, 68, 68, 0.25)",
            }}
          >
            ⚔ {(tf.category || "tactical").replace(/_/g, " ")}
            <span
              style={{
                fontSize: "0.65rem",
                color: "var(--text-muted)",
                marginLeft: 4,
              }}
            >
              ({tf.confidence})
            </span>
          </span>
        </div>
      )}

      {/* Explanation fields */}
      {expl && (
        <div>
          {expl.summary && (
            <div className="explanation-section">
              <div className="explanation-label">Summary</div>
              <div className="explanation-value">{expl.summary}</div>
            </div>
          )}

          {expl.what_happened && (
            <div className="explanation-section">
              <div className="explanation-label">What happened</div>
              <div className="explanation-value">{expl.what_happened}</div>
            </div>
          )}

          {expl.why_it_matters && (
            <div className="explanation-section">
              <div className="explanation-label">Why it matters</div>
              <div className="explanation-value">{expl.why_it_matters}</div>
            </div>
          )}

          {expl.better_move && expl.better_move !== move.played_move && (
            <div className="explanation-section">
              <div className="explanation-label">Better move</div>
              <div className="explanation-value" style={{ fontFamily: "monospace" }}>
                {expl.better_move}
              </div>
            </div>
          )}

          {expl.variation && (
            <div className="explanation-section">
              <div className="explanation-label">Variation</div>
              <div className="variation-line">{expl.variation}</div>
            </div>
          )}

          {expl.motif && expl.motif !== "good_move" && (
            <div className="explanation-section">
              <div className="explanation-label">Motif</div>
              <div className="explanation-value">
                {(expl.motif || "").replace(/_/g, " ")}
              </div>
            </div>
          )}

          {/* Confidence + Limitations footer */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              marginTop: 8,
              paddingTop: 8,
              borderTop: "1px solid var(--border-color)",
            }}
          >
            <span
              style={{
                fontSize: "0.7rem",
                color: "var(--text-muted)",
              }}
            >
              Confidence: {expl.confidence}
            </span>
            {expl.limitations && (
              <span
                style={{
                  fontSize: "0.7rem",
                  color: "var(--accent-yellow)",
                }}
              >
                ⚠ {expl.limitations}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Eval context */}
      <div
        style={{
          display: "flex",
          gap: 16,
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px solid var(--border-color)",
          fontSize: "0.75rem",
          color: "var(--text-muted)",
          fontFamily: "monospace",
        }}
      >
        <span>
          Before:{" "}
          <span style={{ color: move.evaluation_before >= 0 ? "var(--accent-green)" : "var(--accent-red)" }}>
            {formatEval(move.evaluation_before)}
          </span>
        </span>
        <span>
          After:{" "}
          <span style={{ color: move.evaluation_after >= 0 ? "var(--accent-green)" : "var(--accent-red)" }}>
            {formatEval(move.evaluation_after)}
          </span>
        </span>
      </div>
    </div>
  );
}
