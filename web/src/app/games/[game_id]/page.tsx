"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState, useCallback, use } from "react";
import { getGame, getAnalysis } from "@/lib/api";
import type { Game, GameAnalysis, MoveData } from "@/lib/types";
import ChessboardPane from "@/components/GameReview/ChessboardPane";
import MoveList from "@/components/GameReview/MoveList";
import ExplanationPanel from "@/components/GameReview/ExplanationPanel";
import CriticalNav from "@/components/GameReview/CriticalNav";
import EpisodeList from "@/components/GameReview/EpisodeList";
import EvaluationBar from "@/components/GameReview/EvaluationBar";

type FilterMode = "all" | "critical" | "tactical" | "positional" | "endgame";

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

  // Board FEN
  const currentFen = (() => {
    if (currentPly === 0) {
      return "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    }
    const move = allMoves.find((m) => m.ply === currentPly);
    return move?.fen_after ?? "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  })();

  // Navigation
  const goToMove = useCallback((ply: number) => setCurrentPly(ply), []);

  const goNext = useCallback(() => {
    const maxPly = allMoves.length > 0 ? allMoves[allMoves.length - 1].ply : 0;
    if (currentPly < maxPly) {
      const nextMove = allMoves.find((m) => m.ply > currentPly);
      if (nextMove) setCurrentPly(nextMove.ply);
    }
  }, [currentPly, allMoves]);

  const goPrev = useCallback(() => {
    if (currentPly <= 0) return;
    const prevMoves = allMoves.filter((m) => m.ply < currentPly);
    if (prevMoves.length > 0) {
      setCurrentPly(prevMoves[prevMoves.length - 1].ply);
    } else {
      setCurrentPly(0);
    }
  }, [currentPly, allMoves]);

  const goStart = useCallback(() => setCurrentPly(0), []);
  const goEnd = useCallback(() => {
    if (allMoves.length > 0) setCurrentPly(allMoves[allMoves.length - 1].ply);
  }, [allMoves]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") goNext();
      else if (e.key === "ArrowLeft") goPrev();
      else if (e.key === "Home") goStart();
      else if (e.key === "End") goEnd();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goNext, goPrev, goStart, goEnd]);

  // Current eval normalized to White's perspective (positive = White ahead, negative = Black ahead)
  const currentEval = (() => {
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
            fen={currentFen}
            currentMove={currentMove}
            onPrev={goPrev}
            onNext={goNext}
            onStart={goStart}
            onEnd={goEnd}
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
          <div className="card" style={{ padding: 12, maxHeight: 260, overflowY: "auto" }}>
            <MoveList
              moves={filterMode === "all" ? allMoves : filteredMoves}
              currentPly={currentPly}
              onSelectMove={goToMove}
              highlightEpisode={currentMove?.episode_index ?? null}
            />
          </div>

          {/* Explanation panel */}
          {currentMove && (
            <div className="card animate-fade-in" style={{ padding: 16 }}>
              <ExplanationPanel move={currentMove} />
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
