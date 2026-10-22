"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useDashboardRead } from "@/lib/player-dashboard";
import { lookupContext, selectedPly } from "./pgn-context";
import type { Replay } from "./pgn-context";
import { Panel, State, Evidence } from "./Primitives";
import "./dashboard.css";
import PracticeLink from "@/components/Practice/PracticeLink";
const Reasoning = dynamic(() => import("@/components/Reasoning/ReasoningPanel"), { ssr: false });
const Understanding = dynamic(() => import("@/components/Understanding/UnderstandingPanel"), { ssr: false });
const Board = dynamic(() => import("./BoardPreview"), { ssr: false });
type Source = { kind: string; engine_name: string | null; engine_version: string | null; engine_build: string | null; nnue: string | null; provenance: string };
type CacheItem = { status: string; reason?: string; context_compatibility: string; partial: boolean; available_truncated: boolean; available: { config_hash: string; source: Source; available_depths: number[]; context_compatibility: string }[]; evaluation: null | { achieved_depth: number; observed_at: number; returned_multipv: number; config_hash: string; source: Source; config: { depth: number | null; time_seconds: number | null; multipv: number; applied_options: Record<string, unknown>; limit_kind: string }; pvs: { kind: string; cp: number | null; mate: number | null; winner: string | null; moves: string[] }[]; limitations: string[] } };
export default function GamePositionBrowser({ pgn, initialPly = 0, understandingGameID }: { pgn: string; initialPly?: number; understandingGameID?: string }) {
  const [loaded, setLoaded] = useState<{ pgn: string; replay?: Replay; error?: string }>();
  const [choice, setChoice] = useState<{ pgn: string; ply: number; initial: number }>();
  const [advisory, setAdvisory] = useState(false); const [config, setConfig] = useState("");
  const [request, setRequest] = useState<{ key: string; body: unknown }>();
  useEffect(() => {
    const worker = new Worker(new URL("./pgn.worker.ts", import.meta.url));
    const timeout = setTimeout(() => { worker.terminate(); setLoaded({ pgn, error: "PGN replay exceeded the five-second browser limit" }); }, 5000);
    worker.onmessage = event => { clearTimeout(timeout); setLoaded({ pgn, ...event.data }); worker.terminate(); };
    worker.onerror = () => { clearTimeout(timeout); setLoaded({ pgn, error: "PGN worker unavailable" }); worker.terminate(); };
    worker.postMessage(pgn);
    return () => { clearTimeout(timeout); worker.terminate(); };
  }, [pgn]);
  const replay = loaded?.pgn === pgn ? loaded.replay : undefined;
  const ply = replay ? selectedPly(choice?.pgn === pgn && choice.initial === initialPly ? choice.ply : initialPly, replay.moves.length) : 0;
  const contextKey = JSON.stringify([pgn, ply, advisory, config]);
  const active = request?.key === contextKey;
  const result = useDashboardRead<{ items: CacheItem[] }>(active ? "/api/evaluations/lookup-batch" : null, false, 0, active ? request.body : undefined);
  const item = result.data?.items[0]; const evaluation = item?.evaluation;
  function go(value: number) { setChoice({ pgn, ply: value, initial: initialPly }); }
  return <><Panel title="Game positions" kicker="Legal PGN replay · no engine execution">
    {!replay ? <State loading={loaded?.pgn !== pgn} error={loaded?.pgn === pgn ? loaded.error : undefined} /> : <div className="pd-columns"><div><Board fen={replay.fens[ply]} /><div className="pd-actions"><button disabled={!ply} onClick={() => go(0)}>Start</button><button disabled={!ply} onClick={() => go(ply - 1)}>Previous</button><span aria-live="polite">Ply {ply} / {replay.moves.length}</span><button disabled={ply === replay.moves.length} onClick={() => go(ply + 1)}>Next</button><button disabled={ply === replay.moves.length} onClick={() => go(replay.moves.length)}>End</button></div><p className="pd-muted">{replay.root_history_complete ? "Complete history from the standard initial position." : "Setup root: preceding history is unverified; trusted lookup is unavailable."}</p><details><summary>Selected six-field FEN</summary><code className="pd-json">{replay.fens[ply]}</code></details></div><div><div className="pd-pgn-moves"><button aria-current={ply === 0 ? "step" : undefined} onClick={() => go(0)}>0 · Initial position</button>{replay.sans.map((san, i) => <button key={i} aria-current={ply === i + 1 ? "step" : undefined} onClick={() => go(i + 1)}>{i + 1} · {san}</button>)}</div><h3>Local evaluation evidence</h3><p className="pd-muted">Only an explicit lookup reads the existing cache. Absence is not a chess judgment. No cloud request, job or search is started.</p><label><input type="checkbox" checked={advisory} onChange={e => setAdvisory(e.target.checked)} /> Include advisory evidence with unverified draw/history context</label>
    <button disabled={ply > 1000 || (!replay.root_history_complete && !advisory)} onClick={() => setRequest({ key: contextKey, body: { items: [{ context: lookupContext(replay, ply), require_trusted: !advisory, allow_advisory: advisory, allow_cloud: false, ...(config ? { config_hash: config } : {}) }] } })}>Look up selected context locally</button>{ply > 1000 && <p>Lookup unavailable: full history exceeds the 1,000-ply context cap.</p>}
    {active && <><State loading={result.loading} error={result.error} />{item && <><p><Evidence value={item.context_compatibility} /> {item.status} {item.reason}</p>{item.status === "CONFIG_REQUIRED" && <p>Choose a compatible engine configuration, then look up again. Depths across configurations are not ranked together.</p>}{!!item.available.length && <label>Exact configuration<select value={config} onChange={e => setConfig(e.target.value)}><option value="">Server compatibility selection</option>{item.available.map(c => <option key={c.config_hash} value={c.config_hash}>{c.source.engine_name || c.source.kind} · depths {c.available_depths.join(", ")} · {c.config_hash.slice(0, 12)} · {c.context_compatibility}</option>)}</select></label>}{item.available_truncated && <p>Configuration list is bounded.</p>}{evaluation && <><p>White-relative scores · achieved depth {evaluation.achieved_depth} · requested depth {evaluation.config.depth ?? "not depth-limited"}, time {evaluation.config.time_seconds ?? "not time-limited"} s · {evaluation.returned_multipv}/{evaluation.config.multipv} PVs{item.partial ? " · partial" : ""}</p>{evaluation.pvs.map((pv, i) => <p key={i}><strong>{pv.kind === "cp" ? `${((pv.cp ?? 0) / 100).toFixed(2)} pawns` : pv.kind === "mate" ? `Mate ${pv.mate}` : `Checkmate · ${pv.winner} wins`}</strong> · {pv.moves.join(" ") || "terminal position"}</p>)}<p>{evaluation.source.kind} · {evaluation.source.engine_name || "Identity unavailable"} {evaluation.source.engine_version} · recorded {new Date(evaluation.observed_at * 1000).toLocaleString()}</p><details><summary>Build, NNUE, options & provenance</summary><pre className="pd-json">{JSON.stringify({ source: evaluation.source, config: evaluation.config, config_hash: evaluation.config_hash }, null, 2)}</pre></details><p>{evaluation.limitations.join(" ")}</p></>}{!evaluation && <p>No evaluation satisfies this request. Coverage applies only to this selected context.</p>}</>}</>}
    </div></div>}
  </Panel>{replay && understandingGameID && <><PracticeLink gameID={understandingGameID} pgn={pgn} ply={ply} /><Understanding gameID={understandingGameID} pgn={pgn} ply={ply} /><Reasoning gameID={understandingGameID} pgn={pgn} ply={ply} /></>}</>;
}
