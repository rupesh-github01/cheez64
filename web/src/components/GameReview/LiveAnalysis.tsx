"use client";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { Replay } from "@/components/PlayerDashboard/pgn-context";
import { BrowserStockfishController } from "@/lib/browser-stockfish-controller";
import type { LiveSnapshot } from "@/lib/browser-stockfish-controller";
import { ENGINE_IDENTITY, livePosition } from "@/lib/browser-stockfish";
import { browserLocksSupported } from "@/lib/browser-engine-lease";
import { scoreText } from "@/lib/position-evaluation";
import EvaluationBar from "./EvaluationBar";
const subscribeCapabilities = () => () => {};
export default function LiveAnalysis({ replay, ply, pgn, barTarget }: { replay: Replay; ply: number; pgn: string; barTarget?: HTMLElement | null }) {
  const controller = useRef<BrowserStockfishController | null>(null);
  const [view, setView] = useState<LiveSnapshot>();
  const [fallback, setFallback] = useState(false);
  const locksSupported = useSyncExternalStore(subscribeCapabilities, browserLocksSupported, () => false);
  const selected = useMemo(() => {
    try { return { position: livePosition(replay, ply, JSON.stringify([pgn, ply])) }; }
    catch (error) { return { error: error instanceof Error ? error.message : "Invalid live position" }; }
  }, [replay, ply, pgn]);
  useEffect(() => {
    const engine = new BrowserStockfishController(setView); controller.current = engine;
    return () => { engine.dispose(); controller.current = null; };
  }, []);
  useEffect(() => {
    if (selected.position) controller.current?.select(selected.position);
    else controller.current?.stop();
  }, [selected]);
  const current = view?.key === selected.position?.key ? view : undefined;
  const terminal = selected.position?.terminal;
  const enabled = !!view?.enabled;
  const override = enabled || !!current?.lines.length || !!terminal;
  const score = terminal ? selected.position?.terminalScore : current?.lines[0]?.score;
  const loading = enabled && !score && !terminal && current?.status !== "error";
  return <div className="live-evaluation" data-override={override} data-worker-alive={!!view?.workerAlive} data-worker-starts={view?.workerStarts || 0} data-live-depth={current?.depth || 0} data-live-pvs={current?.lines.length || 0} data-live-complete={!!current?.complete}>
    <div className="live-evaluation-controls"><button disabled={!selected.position || !!terminal || enabled || (!locksSupported && !fallback)} onClick={() => selected.position && controller.current?.start(selected.position, fallback)}>Start Live Analysis</button><button disabled={!enabled && !view?.workerAlive} onClick={() => controller.current?.stop()}>Stop Live Analysis</button>{!enabled && !!current?.lines.length && <button onClick={() => controller.current?.dismiss()}>Close live result · show cache</button>}</div>
    {!locksSupported && <label className="live-fallback"><input type="checkbox" checked={fallback} disabled={enabled} onChange={e => setFallback(e.target.checked)} /> Web Locks unavailable: I have closed live analysis in other tabs. Only this tab can be protected; cross-tab exclusion is not guaranteed.</label>}
    <p role="status">{selected.error || (terminal ? `BOARD_FACT · ${terminal}. No engine PV is needed.` : `Browser engine: ${current?.status || (enabled ? "loading" : "idle")}`)}{current?.error ? ` · ${current.error}` : ""}</p>
    {override && <div className="position-eval-layout live-evaluation-result"><EvaluationBar target={barTarget} channel="live" score={score} loading={loading} tier={terminal ? "BOARD_FACT" : "LIVE_ENGINE_UNVERIFIED"} bound={current?.lines[0]?.bound} /><div className="position-eval-details">
      {!terminal && <>{current?.capability && <p>{current.capability}</p>}<strong>LIVE_ENGINE_UNVERIFIED</strong><p>{current?.engine || "Loading verified Stockfish browser build…"}</p><p>{current?.status === "stopped" ? "Stopped-session estimate · worker terminated" : current?.status === "complete" ? "Bounded search complete · worker idle" : current?.status === "error" ? "Last valid result before engine failure" : "Live engine estimate"}</p><p>Depth {current?.depth || "pending"} · {current?.lines.length || 0}/{current?.expected || 3} lines · {current?.complete ? "complete exact-score depth cohort" : "partial iteration or bounded score"} · {Math.max(0, (current?.elapsed || 0) / 1000).toFixed(2)} s wall</p><ol className="position-eval-lines">{current?.lines.map(line => <li key={line.rank}><strong>{line.bound === "lower" ? "≥ " : line.bound === "upper" ? "≤ " : ""}{scoreText(line.score)}</strong> · {line.sans.join(" ")}<small> · depth {line.depth} · {line.nodes === undefined ? "nodes unreported" : `${line.nodes} nodes`}{line.time === undefined ? "" : ` · engine ${(line.time / 1000).toFixed(2)} s`}</small></li>)}</ol><p>{current?.message}</p></>}
      {terminal && <p>{terminal} · deterministic board status. No engine search or fabricated continuation.</p>}
    </div></div>}
    {!replay.root_history_complete && <p>Setup root: preceding history is unknown. The engine receives the supplied root and complete recorded prefix only.</p>}
    <p className="position-eval-note">Browser estimates only · not accepted reasoning evidence or move-quality judgments.</p>
    <details><summary>Browser engine provenance, limits & license</summary><p>On-demand, single-thread browser engine · Hash ≤16 MiB · MultiPV up to 3 · depth 14 or 10 seconds. No cloud/native execution or trusted-cache writes. Scores are estimates, not proofs.</p><p>Web Locks coordinate participating same-origin tabs where supported. Native dispatch remains disabled by default; this is not the native host lock. Browser RSS: NOT_MEASURABLE by this controller; Hash and WASM controls do not enforce an exact process-memory ceiling.</p><p><a href="/engines/stockfish-19.0.1/SOURCE.md" target="_blank" rel="noreferrer">GPL-3.0 license, corresponding source and build inputs</a></p><pre>{JSON.stringify({ ...ENGINE_IDENTITY, uci_name: view?.engine || "not observed yet", applied_commands: view?.options || {}, worker_starts: view?.workerStarts || 0, worker_alive: !!view?.workerAlive, bestmove: current?.bestmove || "not observed" }, null, 2)}</pre></details>
  </div>;
}
