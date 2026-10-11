"use client";

import { useState } from "react";
import type { MoveData, Candidate } from "@/lib/types";
import { classificationColor, classificationLabel } from "@/lib/types";

interface Props {
  move: MoveData | null;
  plyZeroFen?: string;
  variationState: {
    active: boolean;
    candidateLabel: string;
    pvMoves: string[];
    currentStepIndex: number; // 0 = start of variation (fen_before)
  } | null;
  onStartVariation: (pvMoves: string[], label: string, eval_cp?: number) => void;
  onStepVariation: (stepIndex: number) => void;
  onExitVariation: () => void;
}

function formatEval(val: number): string {
  if (Math.abs(val) >= 90000) {
    const moves = Math.max(1, 100000 - Math.abs(val));
    return val > 0 ? `+M${moves}` : `-M${moves}`;
  }
  const pawns = val / 100;
  return pawns >= 0 ? `+${pawns.toFixed(2)}` : pawns.toFixed(2);
}

function getGamePhase(move: MoveData): "Opening" | "Middlegame" | "Endgame" {
  const pf = move.position_features;
  if (pf) {
    const wq = pf.piece_counts?.white?.queen ?? 0;
    const bq = pf.piece_counts?.black?.queen ?? 0;
    const totalMat = (pf.material?.white ?? 0) + (pf.material?.black ?? 0);
    if ((wq === 0 && bq === 0) || totalMat <= 26) return "Endgame";
  }
  if (move.move_number <= 10) return "Opening";
  return "Middlegame";
}

export default function PositionExplorer({
  move,
  plyZeroFen,
  variationState,
  onStartVariation,
  onStepVariation,
  onExitVariation,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [activePvTab, setActivePvTab] = useState<number>(0);

  // If at start position (ply 0)
  if (!move) {
    const startFen =
      plyZeroFen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="card" style={{ padding: 14 }}>
          <div style={{ fontSize: "0.85rem", fontWeight: 700, marginBottom: 8 }}>
            Initial Position (Start of Game)
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
            <span className="pos-pill">White to move</span>
            <span className="pos-pill">Phase: Opening</span>
            <span className="pos-pill">20 legal moves</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <code
              style={{
                fontSize: "0.72rem",
                color: "var(--text-muted)",
                background: "var(--bg-secondary)",
                padding: "4px 8px",
                borderRadius: 4,
                wordBreak: "break-all",
                flex: 1,
              }}
            >
              {startFen}
            </code>
            <button
              className="copy-btn"
              onClick={() => {
                navigator.clipboard.writeText(startFen);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
            >
              {copied ? "✓ Copied" : "Copy FEN"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const phase = getGamePhase(move);
  const candidates: Candidate[] = move.candidates || [];
  const primaryPv = move.principal_variation || [];
  const pf = move.position_features;

  const copyFen = (fen: string) => {
    navigator.clipboard.writeText(fen);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const playedColor = classificationColor(move.classification);
  const isPlayedBest = move.played_move === move.best_move;

  // Identify top candidate eval for delta comparison
  const topEval = candidates.length > 0 ? candidates[0].evaluation : move.evaluation_before;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* 1. Position Information Card */}
      <div className="card" style={{ padding: 14 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 10,
            flexWrap: "wrap",
            gap: 6,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "0.95rem", fontWeight: 700 }}>
              Move {move.move_number}
              {move.color === "White" ? " (White)" : " (Black)"}
            </span>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              Ply {move.ply}
            </span>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span
              style={{
                fontSize: "0.68rem",
                padding: "2px 8px",
                borderRadius: 4,
                fontWeight: 600,
                background:
                  phase === "Opening"
                    ? "rgba(59, 130, 246, 0.15)"
                    : phase === "Middlegame"
                    ? "rgba(168, 85, 247, 0.15)"
                    : "rgba(234, 179, 8, 0.15)",
                color:
                  phase === "Opening"
                    ? "var(--accent-blue)"
                    : phase === "Middlegame"
                    ? "var(--accent-purple)"
                    : "var(--accent-yellow)",
              }}
            >
              {phase} Phase
            </span>
            {pf?.in_check && (
              <span
                style={{
                  fontSize: "0.68rem",
                  padding: "2px 8px",
                  borderRadius: 4,
                  fontWeight: 700,
                  background: "rgba(239, 68, 68, 0.2)",
                  color: "var(--accent-red)",
                }}
              >
                In Check
              </span>
            )}
          </div>
        </div>

        {/* Position stat pills */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
          <span className="pos-pill">
            Side to move: <strong>{move.color}</strong>
          </span>
          {pf && (
            <>
              <span className="pos-pill">
                Material: W:{pf.material?.white ?? 0} B:{pf.material?.black ?? 0} (
                {(pf.material?.difference ?? 0) === 0
                  ? "Equal"
                  : (pf.material?.difference ?? 0) > 0
                  ? `+${pf.material?.difference} White`
                  : `${pf.material?.difference} Black`}
                )
              </span>
              <span className="pos-pill">
                Legal moves: {pf.legal_moves ?? "—"}
              </span>
              {pf.captures_available && pf.captures_available.length > 0 && (
                <span className="pos-pill">
                  Captures: {pf.captures_available.length}
                </span>
              )}
              {pf.checks_available && pf.checks_available.length > 0 && (
                <span className="pos-pill" style={{ color: "var(--accent-yellow)" }}>
                  Checks: {pf.checks_available.length}
                </span>
              )}
            </>
          )}
        </div>

        {/* FEN display + copy */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "var(--bg-secondary)",
            padding: "6px 10px",
            borderRadius: 6,
            border: "1px solid var(--border-color)",
          }}
        >
          <span
            style={{
              fontSize: "0.68rem",
              fontWeight: 700,
              color: "var(--text-muted)",
              whiteSpace: "nowrap",
            }}
          >
            FEN (before move):
          </span>
          <code
            style={{
              fontSize: "0.7rem",
              color: "var(--text-primary)",
              fontFamily: "var(--font-geist-mono), monospace",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
            }}
            title={move.fen_before}
          >
            {move.fen_before}
          </code>
          <button
            className="copy-btn"
            onClick={() => copyFen(move.fen_before)}
            title="Copy FEN string"
          >
            {copied ? "✓ Copied" : "Copy FEN"}
          </button>
        </div>
      </div>

      {/* 2. Move Comparison Matrix (Played vs Best vs Alternatives) */}
      <div className="card" style={{ padding: 14 }}>
        <div
          style={{
            fontSize: "0.72rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "var(--text-muted)",
            marginBottom: 10,
          }}
        >
          Move Comparison
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: isPlayedBest ? "1fr" : "1fr 1fr",
            gap: 10,
            marginBottom: 10,
          }}
        >
          {/* Played Move */}
          <div
            style={{
              padding: "10px 12px",
              borderRadius: 6,
              background: "var(--bg-secondary)",
              border: `1px solid ${
                isPlayedBest ? "rgba(34, 197, 94, 0.4)" : "var(--border-color)"
              }`,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 6,
              }}
            >
              <span
                style={{
                  fontSize: "0.7rem",
                  fontWeight: 600,
                  color: "var(--text-muted)",
                  textTransform: "uppercase",
                }}
              >
                Played in Game
              </span>
              <span className={`badge badge-${move.classification}`}>
                {classificationLabel(move.classification)}
              </span>
            </div>
            <div
              style={{
                fontSize: "1.15rem",
                fontWeight: 800,
                fontFamily: "var(--font-geist-mono), monospace",
                marginBottom: 4,
                color: isPlayedBest ? "var(--accent-green)" : playedColor,
              }}
            >
              {move.played_move}
              <span
                style={{
                  fontSize: "0.72rem",
                  fontWeight: 400,
                  color: "var(--text-muted)",
                  marginLeft: 6,
                }}
              >
                ({move.played_move_uci})
              </span>
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "0.75rem",
                color: "var(--text-secondary)",
              }}
            >
              <span>
                Eval: <strong>{formatEval(move.evaluation_after)}</strong>
              </span>
              <span>
                Loss:{" "}
                <strong
                  style={{
                    color:
                      move.centipawn_loss > 100
                        ? "var(--accent-red)"
                        : move.centipawn_loss > 30
                        ? "var(--accent-orange)"
                        : "var(--accent-green)",
                  }}
                >
                  {move.centipawn_loss > 0 ? `-${move.centipawn_loss} cp` : "0 cp"}
                </strong>
              </span>
            </div>
          </div>

          {/* Best Engine Move (if different from played) */}
          {!isPlayedBest && (
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 6,
                background: "var(--bg-secondary)",
                border: "1px solid rgba(34, 197, 94, 0.4)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 6,
                }}
              >
                <span
                  style={{
                    fontSize: "0.7rem",
                    fontWeight: 600,
                    color: "var(--accent-green)",
                    textTransform: "uppercase",
                  }}
                >
                  Engine Best Move
                </span>
                <span className="badge badge-best">Best</span>
              </div>
              <div
                style={{
                  fontSize: "1.15rem",
                  fontWeight: 800,
                  fontFamily: "var(--font-geist-mono), monospace",
                  marginBottom: 4,
                  color: "var(--accent-green)",
                }}
              >
                {move.best_move}
                <span
                  style={{
                    fontSize: "0.72rem",
                    fontWeight: 400,
                    color: "var(--text-muted)",
                    marginLeft: 6,
                  }}
                >
                  ({move.best_move_uci})
                </span>
              </div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: "0.75rem",
                  color: "var(--text-secondary)",
                }}
              >
                <span>
                  Eval: <strong>{formatEval(topEval)}</strong>
                </span>
                <span style={{ color: "var(--accent-green)" }}>
                  Optimal (+{move.centipawn_loss} cp vs played)
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Evaluation Before vs After summary row */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "6px 10px",
            background: "var(--bg-primary)",
            borderRadius: 6,
            fontSize: "0.75rem",
            color: "var(--text-muted)",
          }}
        >
          <span>
            Eval before move:{" "}
            <strong style={{ color: "var(--text-primary)" }}>
              {formatEval(move.evaluation_before)}
            </strong>
          </span>
          <span>
            Eval after move:{" "}
            <strong style={{ color: "var(--text-primary)" }}>
              {formatEval(move.evaluation_after)}
            </strong>
          </span>
          <span>
            Drop:{" "}
            <strong
              style={{
                color:
                  move.centipawn_loss > 100
                    ? "var(--accent-red)"
                    : move.centipawn_loss > 30
                    ? "var(--accent-orange)"
                    : "var(--accent-green)",
              }}
            >
              {(move.centipawn_loss / 100).toFixed(2)} pawns
            </strong>
          </span>
        </div>
      </div>

      {/* 3. Candidate Moves List */}
      <div className="card" style={{ padding: 14 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 10,
          }}
        >
          <div
            style={{
              fontSize: "0.72rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-muted)",
            }}
          >
            Candidate Moves ({candidates.length})
          </div>
          <span style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>
            Engine search multi-PV candidates
          </span>
        </div>

        {candidates.length === 0 ? (
          <div
            style={{
              fontSize: "0.8rem",
              color: "var(--text-muted)",
              padding: "10px 0",
              textAlign: "center",
            }}
          >
            No multi-candidate analysis stored for this position.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {candidates.map((cand, idx) => {
              const isPlayed = cand.move === move.played_move;
              const isBest = idx === 0;
              const evalDiff = cand.evaluation - topEval;
              const isPreviewingThis =
                variationState?.active &&
                variationState.candidateLabel === `Candidate #${idx + 1} (${cand.move})`;

              return (
                <div
                  key={idx}
                  className={`candidate-row ${isPlayed ? "is-played" : ""} ${
                    isBest ? "is-best" : ""
                  }`}
                  style={{
                    outline: isPreviewingThis ? "2px solid var(--accent-blue)" : undefined,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      flexWrap: "wrap",
                      gap: 6,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          fontSize: "0.68rem",
                          fontWeight: 700,
                          padding: "1px 6px",
                          borderRadius: 4,
                          background: isBest
                            ? "rgba(34, 197, 94, 0.2)"
                            : "var(--bg-tertiary)",
                          color: isBest ? "var(--accent-green)" : "var(--text-muted)",
                        }}
                      >
                        #{idx + 1}
                      </span>
                      <span
                        style={{
                          fontSize: "0.95rem",
                          fontWeight: 800,
                          fontFamily: "var(--font-geist-mono), monospace",
                          color: isBest
                            ? "var(--accent-green)"
                            : isPlayed
                            ? "var(--accent-blue)"
                            : "var(--text-primary)",
                        }}
                      >
                        {cand.move}
                      </span>
                      {isPlayed && (
                        <span
                          style={{
                            fontSize: "0.65rem",
                            fontWeight: 700,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "rgba(59, 130, 246, 0.2)",
                            color: "#93c5fd",
                            textTransform: "uppercase",
                          }}
                        >
                          Played in game
                        </span>
                      )}
                      {isBest && !isPlayed && (
                        <span
                          style={{
                            fontSize: "0.65rem",
                            fontWeight: 700,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "rgba(34, 197, 94, 0.15)",
                            color: "var(--accent-green)",
                            textTransform: "uppercase",
                          }}
                        >
                          Engine Best
                        </span>
                      )}
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <span
                        style={{
                          fontSize: "0.8rem",
                          fontFamily: "var(--font-geist-mono), monospace",
                          fontWeight: 700,
                          color:
                            cand.evaluation >= 0
                              ? "var(--accent-green)"
                              : "var(--accent-red)",
                        }}
                      >
                        {formatEval(cand.evaluation)}
                      </span>
                      {idx > 0 && (
                        <span
                          style={{
                            fontSize: "0.72rem",
                            color: "var(--text-muted)",
                            fontFamily: "var(--font-geist-mono), monospace",
                          }}
                        >
                          ({evalDiff >= 0 ? "+" : ""}
                          {(evalDiff / 100).toFixed(2)})
                        </span>
                      )}
                      <button
                        className="crit-nav-btn"
                        style={{
                          padding: "3px 8px",
                          fontSize: "0.72rem",
                          background: isPreviewingThis
                            ? "var(--accent-blue-dim)"
                            : undefined,
                          color: isPreviewingThis ? "white" : undefined,
                        }}
                        onClick={() => {
                          if (isPreviewingThis) {
                            onExitVariation();
                          } else {
                            onStartVariation(
                              cand.principal_variation || [cand.move],
                              `Candidate #${idx + 1} (${cand.move})`,
                              cand.evaluation
                            );
                          }
                        }}
                        title="Step through this variation move-by-move on the board"
                      >
                        {isPreviewingThis ? "Exit Preview" : "Step PV ▶"}
                      </button>
                    </div>
                  </div>

                  {/* Variation snippet preview */}
                  {cand.principal_variation && cand.principal_variation.length > 0 && (
                    <div
                      style={{
                        fontSize: "0.72rem",
                        color: "var(--text-muted)",
                        fontFamily: "var(--font-geist-mono), monospace",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                      title={cand.principal_variation.join(" ")}
                    >
                      PV: {cand.principal_variation.slice(0, 8).join(" ")}
                      {cand.principal_variation.length > 8 ? " …" : ""}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 4. Principal Variation Explorer & Interactive Stepper */}
      <div className="card" style={{ padding: 14 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 10,
          }}
        >
          <div
            style={{
              fontSize: "0.72rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-muted)",
            }}
          >
            Principal Variation Explorer
          </div>

          {variationState?.active && (
            <button
              className="copy-btn"
              style={{
                background: "rgba(239, 68, 68, 0.15)",
                color: "var(--accent-red)",
                borderColor: "rgba(239, 68, 68, 0.3)",
              }}
              onClick={onExitVariation}
            >
              ✕ Exit Preview
            </button>
          )}
        </div>

        {/* Primary PV or active variation stepper */}
        {variationState?.active ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div
              style={{
                fontSize: "0.8rem",
                color: "#93c5fd",
                fontWeight: 600,
              }}
            >
              ⚑ Active Variation: {variationState.candidateLabel}
            </div>

            {/* Stepper buttons */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button
                className="crit-nav-btn"
                style={{ padding: "4px 8px" }}
                disabled={variationState.currentStepIndex <= 0}
                onClick={() => onStepVariation(0)}
                title="Start of variation"
              >
                ⏮ Start
              </button>
              <button
                className="crit-nav-btn"
                style={{ padding: "4px 8px" }}
                disabled={variationState.currentStepIndex <= 0}
                onClick={() => onStepVariation(variationState.currentStepIndex - 1)}
                title="Previous move in variation"
              >
                ◀ Prev
              </button>
              <span
                style={{
                  fontSize: "0.75rem",
                  fontFamily: "var(--font-geist-mono), monospace",
                  color: "var(--text-primary)",
                  minWidth: 80,
                  textAlign: "center",
                }}
              >
                Move {variationState.currentStepIndex} of{" "}
                {variationState.pvMoves.length}
              </span>
              <button
                className="crit-nav-btn"
                style={{ padding: "4px 8px" }}
                disabled={
                  variationState.currentStepIndex >= variationState.pvMoves.length
                }
                onClick={() => onStepVariation(variationState.currentStepIndex + 1)}
                title="Next move in variation"
              >
                Next ▶
              </button>
              <button
                className="crit-nav-btn"
                style={{ padding: "4px 8px" }}
                disabled={
                  variationState.currentStepIndex >= variationState.pvMoves.length
                }
                onClick={() => onStepVariation(variationState.pvMoves.length)}
                title="End of variation"
              >
                End ⏭
              </button>
            </div>

            {/* Clickable Move Chips for the active PV */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
              {variationState.pvMoves.map((mSan, idx) => {
                const stepNum = idx + 1;
                const isCurrent = variationState.currentStepIndex === stepNum;
                return (
                  <button
                    key={idx}
                    className={`pv-chip ${isCurrent ? "active" : ""}`}
                    onClick={() => onStepVariation(stepNum)}
                    title={`Jump to step ${stepNum}: ${mSan}`}
                  >
                    <span
                      style={{
                        fontSize: "0.65rem",
                        color: isCurrent ? "rgba(255,255,255,0.7)" : "var(--text-muted)",
                        marginRight: 4,
                      }}
                    >
                      {stepNum}.
                    </span>
                    {mSan}
                  </button>
                );
              })}
            </div>
          </div>
        ) : primaryPv.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                Engine Primary Line ({primaryPv.length} moves)
              </span>
              <button
                className="crit-nav-btn"
                style={{ padding: "3px 8px", fontSize: "0.72rem" }}
                onClick={() =>
                  onStartVariation(primaryPv, `Best Line (${move.best_move})`, topEval)
                }
              >
                Step on Board ▶
              </button>
            </div>

            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {primaryPv.map((mSan, idx) => (
                <button
                  key={idx}
                  className="pv-chip"
                  onClick={() => {
                    onStartVariation(primaryPv, `Best Line (${move.best_move})`, topEval);
                    // Automatically jump to this move
                    setTimeout(() => onStepVariation(idx + 1), 50);
                  }}
                  title={`Step through from move ${idx + 1}`}
                >
                  <span
                    style={{
                      fontSize: "0.65rem",
                      color: "var(--text-muted)",
                      marginRight: 4,
                    }}
                  >
                    {idx + 1}.
                  </span>
                  {mSan}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div
            style={{
              fontSize: "0.8rem",
              color: "var(--text-muted)",
              padding: "10px 0",
              textAlign: "center",
            }}
          >
            No principal variation available for this position.
          </div>
        )}
      </div>
    </div>
  );
}
