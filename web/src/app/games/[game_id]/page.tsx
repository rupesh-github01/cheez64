"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState, useCallback, use, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Chess } from "chess.js";
import nextDynamic from "next/dynamic";
import { getGame, getAnalysis, queueAnalysis } from "@/lib/api";
import type { Game, GameAnalysis, MoveData } from "@/lib/types";
import ChessboardPane from "@/components/GameReview/ChessboardPane";
import MoveList from "@/components/GameReview/MoveList";
import ExplanationPanel from "@/components/GameReview/ExplanationPanel";
import PositionExplorer from "@/components/GameReview/PositionExplorer";
import CriticalNav from "@/components/GameReview/CriticalNav";
import EpisodeList from "@/components/GameReview/EpisodeList";
import PositionEvaluation from "@/components/GameReview/PositionEvaluation";
import CoachChatPanel from "@/components/GameReview/CoachChatPanel";
import ReplayWorkspace from "@/components/GameReview/ReplayWorkspace";
import {ReviewHeader} from "@/components/GameReview/ReviewChrome";
import {isReviewTypingTarget} from "@/components/GameReview/review-navigation";
import {Button,Panel,LoadingState,EmptyState,SectionHeading} from "@/components/ui/Primitives";
import styles from "@/components/GameReview/review.module.css";
import "@/components/Understanding/understanding.css";
import PracticeLink from "@/components/Practice/PracticeLink";

const Reasoning = nextDynamic(() => import("@/components/Reasoning/ReasoningPanel"), { ssr: false });
const Understanding = nextDynamic(() => import("@/components/Understanding/UnderstandingPanel"), { ssr: false });

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

const NO_MOVES: MoveData[] = [];
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

  return (
    <Suspense fallback={<div className={styles.root}><LoadingState label="Loading game review"/></div>}>
      <GameReviewContent key={game_id} game_id={game_id} />
    </Suspense>
  );
}

function GameReviewContent({ game_id }: { game_id: string }) {
  const searchParams = useSearchParams();
  const [game, setGame] = useState<Game | null>(null);
  const [analysis, setAnalysis] = useState<GameAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [barTarget,setBarTarget]=useState<HTMLDivElement|null>(null);
  const [currentPly, setCurrentPly] = useState(0); // 0 = start position
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [activeTab, setActiveTab] = useState<ReviewTab>("coach");

  // Interactive variation stepper state
  const [variationState, setVariationState] = useState<VariationState | null>(null);

  // Analysis Queue state for unanalyzed game
  const [queueing, setQueueing] = useState(false);
  const [queueSuccess, setQueueSuccess] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);

  const handleQueueAnalysis = async () => {
    setQueueing(true);
    setQueueError(null);
    try {
      const res = await queueAnalysis({
        game_ids: [game_id],
        priority: 10,
        force_reanalyze: true,
      });
      setQueueSuccess(res.message || "Game queued for Stockfish analysis!");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to queue game";
      setQueueError(msg);
    } finally {
      setQueueing(false);
    }
  };

  // Load data
  useEffect(() => {
    let current = true;
    getGame(game_id)
      .then((g) => {
        if(!current)return;
        setGame(g);
        return getAnalysis(game_id)
          .then((a) => {
            if(!current)return;
            setAnalysis(a);
            // Check if ply query parameter was passed from dashboard
            const plyParam = searchParams.get("ply");
            if (plyParam) {
              const p = Number(plyParam);
              if (Number.isInteger(p) && p >= 0 && (p === 0 || a.moves.some((m) => m.ply === p))) {
                setCurrentPly(p);
              }
            }
          })
          .catch(() => {
            // Game exists but analysis is missing or not yet analyzed
            if(current)setAnalysis(null);
          });
      })
      .catch((err) => {if(current)setError(err.message);})
      .finally(() => {if(current)setLoading(false);});
    return()=>{current=false;};
  }, [game_id, searchParams]);

  // Derive move arrays
  const allMoves = analysis?.moves ?? NO_MOVES;

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
      if(!analysis||isReviewTypingTarget(e.target)||e.altKey||e.ctrlKey||e.metaKey)return;
      if(["ArrowRight","ArrowLeft","Home","End"].includes(e.key))e.preventDefault();
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
    analysis, variationState,
    goNext,
    goPrev,
    goStart,
    goEnd,
    handleStepVariation,
    handleExitVariation,
  ]);

  if (loading) return <div className={styles.root}><LoadingState label="Loading game review"/></div>;
  if (error) return <div className={styles.root}><EmptyState role="alert"><h1>Game review unavailable</h1><p>{error}</p><Link href="/games" prefetch={false}>Return to Game Library</Link></EmptyState></div>;

  if (!game) return null;

  // PGN replay retains its existing bounded worker and exact-context evaluation.
  if (!analysis) return <div className={styles.root}><ReviewHeader game={game}/><ReplayWorkspace key={game_id} pgn={game.pgn} gameID={game.game_id} initialPly={Number(searchParams.get("ply")||0)}/>
    <details className={styles.savedAnalysis}><summary>Saved game analysis & queue</summary><p>Saved status: {game.analysis_status}. Full-game analysis is not available here. Live browser estimates are separate from saved analysis and coaching.</p><Button onClick={handleQueueAnalysis} disabled={queueing||!!queueSuccess}>{queueing?'Queueing…':queueSuccess?'Queued':'Queue for Analysis'}</Button> <Link href="/library/manage" prefetch={false}>View Analysis Queue →</Link>{queueSuccess&&<p role="status">{queueSuccess} Ensure the worker is running to begin analysis.</p>}{queueError&&<p role="alert">{queueError}</p>}</details>
  </div>;

  return (
    <div className={styles.root}>
      <ReviewHeader game={game}/>
      {/* Main layout */}
      <div className={styles.primary}>
        {/* Left: Board */}
        <div className={styles.boardColumn}>
          <ChessboardPane
            evaluationRailRef={setBarTarget}
            currentPly={variationState?.active?variationState.currentStepIndex:currentPly}
            maxPly={variationState?.active?variationState.steps.length-1:(allMoves.at(-1)?.ply??0)}
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
          <p className={styles.positionLabel}>{variationState?.active?'Variation preview · return to mainline for compatible evidence':currentMove?`After ${currentMove.played_move}`:'Initial position'}</p>
          <details className={styles.technical}><summary>Selected position FEN</summary><code>{displayedFen}</code></details>
        </div>

        {/* Right: Panels */}
        <div className={styles.analysisColumn}>
          {/* Critical navigation */}
          <CriticalNav
            criticalMoves={criticalMoves}
            currentPly={currentPly}
            onNavigate={goToMove}
          />

          {/* Filter tabs */}
          <div className={styles.filterTabs} aria-label="Saved move filters">
            {(["all", "critical", "tactical", "positional", "endgame"] as FilterMode[]).map(
              (mode) => (
                <button
                  key={mode}
                  aria-pressed={filterMode===mode}
                  onClick={() => setFilterMode(mode)}
                >
                  {mode.charAt(0).toUpperCase() + mode.slice(1)}
                  {mode === "critical" && ` (${criticalMoves.length})`}
                </button>
              )
            )}
          </div>

          {/* Move list */}
          <Panel className={styles.movesPanel}><div className={styles.panelHeading}><SectionHeading>Score sheet</SectionHeading><span>{allMoves.length} saved plies</span></div><button className={styles.initialMove} aria-current={currentPly===0?"step":undefined} onClick={goStart}>Initial position</button>
            <MoveList
              moves={filterMode === "all" ? allMoves : filteredMoves}
              currentPly={currentPly}
              onSelectMove={goToMove}
              highlightEpisode={currentMove?.episode_index ?? null}
            />
          </Panel>

          <PositionEvaluation barTarget={barTarget} pgn={game.pgn} ply={currentPly} disabled={!!variationState?.active} legacy={{
            value: currentMove ? currentMove.evaluation_after : allMoves[0]?.evaluation_before ?? null,
            color: currentMove?.color || allMoves[0]?.color || "",
            fen: currentMove?.fen_after || allMoves[0]?.fen_before || "",
            schema: analysis.schema_version,
          }} />
        </div>
      </div>
      <section className={styles.insights} aria-label="Position insights"><div className={styles.insightHeading}><div><p className={styles.kicker}>EXAMINE THE POSITION</p><h2>From moves to ideas.</h2></div>
          <PracticeLink gameID={game.game_id} pgn={game.pgn} ply={currentPly} disabled={!!variationState?.active} />
      </div><Understanding gameID={game.game_id} pgn={game.pgn} ply={currentPly} disabled={!!variationState?.active} />
          <details className={styles.reasoningDisclosure}><summary>Compare continuations · existing reasoning evidence</summary><Reasoning gameID={game.game_id} pgn={game.pgn} ply={currentPly} disabled={!!variationState?.active} /></details>
          <details className={styles.savedAnalysis} open><summary>Saved coaching & move explorer</summary><p className={styles.muted}>Existing legacy analysis. Live browser results do not update these judgments.</p>          {/* Mode Switcher Tabs */}
          <div className={styles.modeTabs} aria-label="Saved analysis sections">
            <button
              aria-pressed={activeTab==="coach"}
              onClick={() => setActiveTab("coach")}
            >
              Coach explanation
            </button>
            <button
              aria-pressed={activeTab==="chat"}
              onClick={() => setActiveTab("chat")}
            >
              Ask coach
            </button>
            <button
              aria-pressed={activeTab==="explorer"}
              onClick={() => setActiveTab("explorer")}
            >
              Position & move explorer
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
          </details>
      </section>
    </div>
  );
}
