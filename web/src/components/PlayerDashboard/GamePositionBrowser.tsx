"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { selectedPly } from "./pgn-context";
import { usePGNReplay } from "./usePGNReplay";
import PositionEvaluation from "@/components/GameReview/PositionEvaluation";
import EvaluationBar from "@/components/GameReview/EvaluationBar";
import { Panel, State } from "./Primitives";
import "./dashboard.css";
import PracticeLink from "@/components/Practice/PracticeLink";
const Reasoning = dynamic(() => import("@/components/Reasoning/ReasoningPanel"), { ssr: false });
const Understanding = dynamic(() => import("@/components/Understanding/UnderstandingPanel"), { ssr: false });
const Board = dynamic(() => import("./BoardPreview"), { ssr: false });
export default function GamePositionBrowser({ pgn, initialPly = 0, understandingGameID }: { pgn: string; initialPly?: number; understandingGameID?: string }) {
  const loaded = usePGNReplay(pgn);
  const [choice, setChoice] = useState<{ pgn: string; ply: number; initial: number }>();
  const replay = loaded.replay;
  const ply = replay ? selectedPly(choice?.pgn === pgn && choice.initial === initialPly ? choice.ply : initialPly, replay.moves.length) : 0;
  function go(value: number) { setChoice({ pgn, ply: value, initial: initialPly }); }
  return <><Panel title="Game positions" kicker="Legal PGN replay · no engine execution">
    {!replay ? <section className="position-evaluation" aria-label="Position evaluation"><h3>Position evaluation</h3><div className="position-eval-layout"><EvaluationBar loading={!loaded.error} /><State loading={!loaded.error} error={loaded.error} /></div></section> : <div className="pd-columns"><div><Board fen={replay.fens[ply]} /><div className="pd-actions"><button disabled={!ply} onClick={() => go(0)}>Start</button><button disabled={!ply} onClick={() => go(ply - 1)}>Previous</button><span aria-live="polite">Ply {ply} / {replay.moves.length}</span><button disabled={ply === replay.moves.length} onClick={() => go(ply + 1)}>Next</button><button disabled={ply === replay.moves.length} onClick={() => go(replay.moves.length)}>End</button></div><p className="pd-muted">{replay.root_history_complete ? "Complete history from the standard initial position." : "Setup root: preceding history is unverified; trusted lookup is unavailable."}</p><details><summary>Selected six-field FEN</summary><code className="pd-json">{replay.fens[ply]}</code></details></div><div><div className="pd-pgn-moves"><button aria-current={ply === 0 ? "step" : undefined} onClick={() => go(0)}>0 · Initial position</button>{replay.sans.map((san, i) => <button key={i} aria-current={ply === i + 1 ? "step" : undefined} onClick={() => go(i + 1)}>{i + 1} · {san}</button>)}</div><PositionEvaluation pgn={pgn} ply={ply} replay={replay} />
    </div></div>}
  </Panel>{replay && understandingGameID && <><PracticeLink gameID={understandingGameID} pgn={pgn} ply={ply} /><Understanding gameID={understandingGameID} pgn={pgn} ply={ply} /><Reasoning gameID={understandingGameID} pgn={pgn} ply={ply} /></>}</>;
}
