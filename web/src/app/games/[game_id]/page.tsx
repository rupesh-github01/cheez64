"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState, useCallback, use } from "react";
import { Chess } from "chess.js";
import { getGame, getAnalysis } from "@/lib/api";
import type { Game, GameAnalysis, MoveData } from "@/lib/types";
import ChessboardPane from "@/components/GameReview/ChessboardPane";
import MoveList from "@/components/GameReview/MoveList";
import ExplanationPanel from "@/components/GameReview/ExplanationPanel";
import PositionExplorer from "@/components/GameReview/PositionExplorer";
import CriticalNav from "@/components/GameReview/CriticalNav";
import EpisodeList from "@/components/GameReview/EpisodeList";
import EvaluationBar from "@/components/GameReview/EvaluationBar";
import CoachChatPanel from "@/components/GameReview/CoachChatPanel";

type FilterMode = "all" | "critical" | "tactical" | "positional" | "endgame";
type ReviewTab = "coach" | "chat" | "explorer";

interface VariationStep {
  stepIndex: number;
  fen: string;
  san: string;
  from?: string;
  to?: string;
}

interface VariationState {
  active: boolean;
  candidateLabel: string;
  pvMoves: string[];
  eval_cp?: number;
  steps: VariationStep[];
  currentStepIndex: number;
}

const TACTICAL_CATEGORIES = new Set([
  "material_loss",
  "hanging_piece",
  "missed_capture",
  "missed_check_or_forcing_move",
]);

export default function GameReviewPage({
  params,
}: {
  params: Promise<{ game_id: string }>;
}) {
  const { game_id } = use(params);
  const [game, setGame] = useState<Game | null>(null);
  const [analysis, setAnalysis] = useState<GameAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [currentPly, setCurrentPly] = useState(0); // 0 = start position
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [activeTab, setActiveTab] = useState<ReviewTab>("coach");

  // Interactive variation stepper state
  const [variationState, setVariationState] = useState<VariationState | null>(null);

  // Load data
  useEffect(() => {
    setLoading(true);
    Promise.all([getGame(game_id), getAnalysis(game_id)])
      .then(([g, a]) => {
        setGame(g);
        setAnalysis(a);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [game_id]);

  // Derive move arrays
  const allMoves = analysis?.moves ?? [];

  const filteredMoves = allMoves.filter((m) => {
    if (filterMode === "all") return true;
    if (filterMode === "critical") return m.is_critical;
    if (filterMode === "tactical") {
      const cat = m.tactical_finding?.category ?? "";
      return TACTICAL_CATEGORIES.has(cat);
    }
    if (filterMode === "endgame") {
      const pf = m.position_features;
      if (!pf) return false;
      const wq = pf.piece_counts?.white?.queen ?? 0;
      const bq = pf.piece_counts?.black?.queen ?? 0;
      const total =
        (pf.material?.white ?? 0) + (pf.material?.black ?? 0);
      return (wq === 0 && bq === 0) || total <= 26;
    }
    // positional: not endgame, not tactical
    if (filterMode === "positional") {
      const cat = m.tactical_finding?.category ?? "";
      if (TACTICAL_CATEGORIES.has(cat)) return false;
      const pf = m.position_features;
      if (!pf) return true;
      const wq = pf.piece_counts?.white?.queen ?? 0;
      const bq = pf.piece_counts?.black?.queen ?? 0;
      const total =
        (pf.material?.white ?? 0) + (pf.material?.black ?? 0);
      const isEndgame = (wq === 0 && bq === 0) || total <= 26;
      return !isEndgame;
    }
    return true;
  });

  const criticalMoves = allMoves.filter((m) => m.is_critical);
  const currentMove: MoveData | null =
    currentPly > 0 ? allMoves.find((m) => m.ply === currentPly) ?? null : null;

  // Board FEN (normal game position)
  const gameFen = (() => {
    if (currentPly === 0) {
      return (
        allMoves[0]?.fen_before ??
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
      );
    }
    const move = allMoves.find((m) => m.ply === currentPly);
    return move?.fen_after ?? "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  })();

  // Displayed FEN (reflects variation step if previewing)
  const displayedFen =
    variationState?.active && variationState.steps[variationState.currentStepIndex]
      ? variationState.steps[variationState.currentStepIndex].fen
      : gameFen;

  const variationSquares =
    variationState?.active && variationState.steps[variationState.currentStepIndex]
      ? {
          from: variationState.steps[variationState.currentStepIndex].from,
          to: variationState.steps[variationState.currentStepIndex].to,
        }
      : null;

  const variationBanner = variationState?.active
    ? {
        label: variationState.candidateLabel,
        step: variationState.currentStepIndex,
        total: variationState.pvMoves.length,
      }
    : null;

  // Variation Stepper handlers
  const handleStartVariation = useCallback(
    (pvMoves: string[], label: string, eval_cp?: number) => {
      const rootFen =
        currentMove?.fen_before ??
        allMoves[0]?.fen_before ??
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

      try {
        const chess = new Chess(rootFen);
        const steps: VariationStep[] = [
          { stepIndex: 0, fen: rootFen, san: "" },
        ];
        for (let i = 0; i < pvMoves.length; i++) {
          const m = pvMoves[i];
          let res = null;
          try {
            res = chess.move(m);
          } catch {
            res = null;
          }
          if (!res) break;
          steps.push({
            stepIndex: i + 1,
            fen: chess.fen(),
            san: res.san,
            from: res.from,
            to: res.to,
          });
        }
        setVariationState({
          active: true,
          candidateLabel: label,
          pvMoves,
          eval_cp,
          steps,
          currentStepIndex: Math.min(1, steps.length - 1),
        });
        setActiveTab("explorer");
      } catch (err) {
        console.error("Failed to parse variation", err);
      }
    },
    [currentMove, allMoves]
  );

  const handleStepVariation = useCallback((stepIndex: number) => {
    setVariationState((prev) => {
      if (!prev) return null;
      const clamped = Math.max(0, Math.min(stepIndex, prev.steps.length - 1));
      return { ...prev, currentStepIndex: clamped };
    });
  }, []);

  const handleExitVariation = useCallback(() => {
    setVariationState(null);
  }, []);

  // Navigation
  const goToMove = useCallback((ply: number) => {
    setVariationState(null);
    setCurrentPly(ply);
  }, []);

  const goNext = useCallback(() => {
    setVariationState(null);
    const maxPly = allMoves.length > 0 ? allMoves[allMoves.length - 1].ply : 0;
    if (currentPly < maxPly) {
      const nextMove = allMoves.find((m) => m.ply > currentPly);
      if (nextMove) setCurrentPly(nextMove.ply);
    }
  }, [currentPly, allMoves]);

  const goPrev = useCallback(() => {
    setVariationState(null);
    if (currentPly <= 0) return;
    const prevMoves = allMoves.filter((m) => m.ply < currentPly);
    if (prevMoves.length > 0) {
      setCurrentPly(prevMoves[prevMoves.length - 1].ply);
    } else {
      setCurrentPly(0);
    }
  }, [currentPly, allMoves]);

  const goStart = useCallback(() => {
    setVariationState(null);
    setCurrentPly(0);
  }, []);

  const goEnd = useCallback(() => {
    setVariationState(null);
    if (allMoves.length > 0) setCurrentPly(allMoves[allMoves.length - 1].ply);
  }, [allMoves]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (variationState?.active) {
        if (e.key === "ArrowRight") {
          e.preventDefault();
          handleStepVariation(variationState.currentStepIndex + 1);
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          handleStepVariation(variationState.currentStepIndex - 1);
        } else if (e.key === "Escape") {
          e.preventDefault();
          handleExitVariation();
        }
        return;
      }
      if (e.key === "ArrowRight") goNext();
      else if (e.key === "ArrowLeft") goPrev();
      else if (e.key === "Home") goStart();
      else if (e.key === "End") goEnd();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [
    variationState,
    goNext,
    goPrev,
    goStart,
    goEnd,
    handleStepVariation,
    handleExitVariation,
  ]);

  // Current eval normalized to White's perspective (positive = White ahead, negative = Black ahead)
  const currentEval = (() => {
    if (variationState?.active && variationState.eval_cp !== undefined) {
      // Evaluation from perspective of side to move at fen_before
      const side = currentMove?.color ?? "White";
      return side === "White" ? variationState.eval_cp : -variationState.eval_cp;
    }
    if (!currentMove) {
      return allMoves[0]?.evaluation_before ?? 0;
    }
    const raw = currentMove.evaluation_after ?? 0;
    return currentMove.color === "White" ? raw : -raw;
  })();

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          minHeight: "60vh",
          color: "var(--text-muted)",
        }}
      >
        Loading game review...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ maxWidth: 600, margin: "40px auto", padding: 20 }}>
        <div className="card" style={{ padding: 24, textAlign: "center" }}>
          <p style={{ color: "var(--accent-red)", fontWeight: 600 }}>
            Failed to load game
          </p>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 8 }}>
            {error}
          </p>
        </div>
      </div>
    );
  }

  if (!game || !analysis) return null;

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: "16px 20px" }}>
      {/* Game header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          marginBottom: 16,
          flexWrap: "wrap",
        }}
      >
        <a
          href="/games"
          style={{
            color: "var(--text-muted)",
            fontSize: "0.8rem",
            textDecoration: "none",
          }}
        >
          ← Library
        </a>
        <h1 style={{ fontSize: "1.15rem", fontWeight: 700, margin: 0 }}>
          {game.white} vs {game.black}
        </h1>
        <span
          style={{
            fontSize: "0.85rem",
            fontWeight: 600,
            color: "var(--text-secondary)",
          }}
        >
          {game.result}
        </span>
        {game.opening && (
          <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
            <span style={{ color: "var(--accent-cyan)", fontWeight: 600 }}>
              {game.opening.eco}
            </span>{" "}
            {game.opening.opening_name}
          </span>
        )}
        {game.date && (
          <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
            {game.date.replace(/\./g, "-").slice(0, 10)}
          </span>
        )}
      </div>

      {/* Main layout */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 24px 1fr",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* Left: Board */}
        <div>
          <ChessboardPane
            fen={displayedFen}
            currentMove={currentMove}
            variationBanner={variationBanner}
            variationSquares={variationSquares}
            onExitVariation={handleExitVariation}
            onPrev={
              variationState?.active
                ? () => handleStepVariation(variationState.currentStepIndex - 1)
                : goPrev
            }
            onNext={
              variationState?.active
                ? () => handleStepVariation(variationState.currentStepIndex + 1)
                : goNext
            }
            onStart={
              variationState?.active
                ? () => handleStepVariation(0)
                : goStart
            }
            onEnd={
              variationState?.active
                ? () => handleStepVariation(variationState.pvMoves.length)
                : goEnd
            }
          />
        </div>

        {/* Eval bar */}
        <EvaluationBar eval_cp={currentEval} />

        {/* Right: Panels */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          {/* Critical navigation */}
          <CriticalNav
            criticalMoves={criticalMoves}
            currentPly={currentPly}
            onNavigate={goToMove}
          />

          {/* Mode Switcher Tabs */}
          <div className="mode-toggle-group">
            <button
              className={`mode-toggle-btn ${activeTab === "coach" ? "active" : ""}`}
              onClick={() => setActiveTab("coach")}
            >
              🎓 Coach Explanation
            </button>
            <button
              className={`mode-toggle-btn ${activeTab === "chat" ? "active" : ""}`}
              onClick={() => setActiveTab("chat")}
            >
              💬 Ask Coach
            </button>
            <button
              className={`mode-toggle-btn ${activeTab === "explorer" ? "active" : ""}`}
              onClick={() => setActiveTab("explorer")}
            >
              🔍 Position & Move Explorer
              {currentMove?.candidates && currentMove.candidates.length > 0 && (
                <span
                  style={{
                    fontSize: "0.65rem",
                    padding: "1px 6px",
                    borderRadius: 9999,
                    background:
                      activeTab === "explorer"
                        ? "rgba(59, 130, 246, 0.3)"
                        : "var(--bg-tertiary)",
                  }}
                >
                  {currentMove.candidates.length} candidates
                </span>
              )}
            </button>
          </div>

          {/* Filter tabs */}
          <div className="filter-tabs">
            {(["all", "critical", "tactical", "positional", "endgame"] as FilterMode[]).map(
              (mode) => (
                <button
                  key={mode}
                  className={`filter-tab ${filterMode === mode ? "active" : ""}`}
                  onClick={() => setFilterMode(mode)}
                >
                  {mode.charAt(0).toUpperCase() + mode.slice(1)}
                  {mode === "critical" && ` (${criticalMoves.length})`}
                </button>
              )
            )}
          </div>

          {/* Move list */}
          <div className="card" style={{ padding: 12, maxHeight: 240, overflowY: "auto" }}>
            <MoveList
              moves={filterMode === "all" ? allMoves : filteredMoves}
              currentPly={currentPly}
              onSelectMove={goToMove}
              highlightEpisode={currentMove?.episode_index ?? null}
            />
          </div>

          {/* Main content pane: Coach vs Chat vs Explorer */}
          {activeTab === "coach" ? (
            currentMove && (
              <div className="card animate-fade-in" style={{ padding: 16 }}>
                <ExplanationPanel
                  move={currentMove}
                  onExploreMove={() => setActiveTab("explorer")}
                  onExplorePv={(pv) => {
                    const pvArr = pv.split(/\s+/).filter(Boolean);
                    handleStartVariation(pvArr, `Variation: ${currentMove.best_move}`);
                  }}
                />
              </div>
            )
          ) : activeTab === "chat" ? (
            <div className="card animate-fade-in" style={{ padding: 16 }}>
              <CoachChatPanel
                gameId={game_id}
                currentPly={currentPly}
                currentMove={currentMove}
                playerName={game.white}
              />
            </div>
          ) : (
            <div className="animate-fade-in">
              <PositionExplorer
                move={currentMove}
                plyZeroFen={allMoves[0]?.fen_before}
                variationState={
                  variationState
                    ? {
                        active: variationState.active,
                        candidateLabel: variationState.candidateLabel,
                        pvMoves: variationState.pvMoves,
                        currentStepIndex: variationState.currentStepIndex,
                      }
                    : null
                }
                onStartVariation={handleStartVariation}
                onStepVariation={handleStepVariation}
                onExitVariation={handleExitVariation}
              />
            </div>
          )}

          {/* Episodes */}
          {analysis.episodes && analysis.episodes.length > 0 && (
            <div className="card" style={{ padding: 12 }}>
              <EpisodeList
                episodes={analysis.episodes}
                currentPly={currentPly}
                onSelectEpisode={goToMove}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

