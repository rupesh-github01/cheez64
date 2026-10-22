"use client";
import { useEffect, useMemo, useState } from "react";
import { lookupContext } from "@/components/PlayerDashboard/pgn-context";
import type { Replay } from "@/components/PlayerDashboard/pgn-context";
import { usePGNReplay } from "@/components/PlayerDashboard/usePGNReplay";
import { evaluationStatus, fetchEvaluation, legalLines, scoreText, legacyEstimate, unavailableReason, cloudStatusExplanation } from "@/lib/position-evaluation";
import type { CacheResult, LegacySample } from "@/lib/position-evaluation";
import EvaluationBar from "./EvaluationBar";
import EvaluationCoverage from "./EvaluationCoverage";
import "./position-evaluation.css";
export default function PositionEvaluation({ pgn, ply, replay: supplied, disabled = false, legacy }: { pgn: string; ply: number; replay?: Replay; disabled?: boolean; legacy?: LegacySample }) {
  const loaded = usePGNReplay(pgn, supplied);
  const replay = loaded.replay;
  const reason = disabled ? "Return to the recorded mainline for context-compatible evaluation." : loaded.error || (ply > 1000 ? "Evaluation unavailable: complete history exceeds 1,000 plies." : undefined);
  return <section className="position-evaluation" aria-label="Position evaluation"><h3>Position evaluation</h3>{reason || !replay ? <div className="position-eval-layout"><EvaluationBar loading={!reason} /><p role="status">{reason || "Loading legal game history…"}</p></div> : <EvaluationSession key={JSON.stringify([pgn, ply])} replay={replay} ply={ply} legacy={legacy} />}{replay && !disabled && <EvaluationCoverage key={pgn} replay={replay} />}</section>;
}
function EvaluationSession({ replay, ply, legacy }: { replay: Replay; ply: number; legacy?: LegacySample }) {
  const context = useMemo(() => lookupContext(replay, ply), [replay, ply]);
  const [config, setConfig] = useState("");
  const [action, setAction] = useState<{ id: number; key?: string }>({ id: 0 });
  const [operatorKey, setOperatorKey] = useState("");
  const [cloud, setCloud] = useState<Awaited<ReturnType<typeof evaluationStatus>>>();
  const [view, setView] = useState<{ request: string; item?: CacheResult; error?: string }>();
  const request = JSON.stringify([config, action.id]);
  useEffect(() => {
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 5000);
    evaluationStatus(controller.signal).then(setCloud).catch(() => {}).finally(() => clearTimeout(timeout));
    return () => { controller.abort(); clearTimeout(timeout); };
  }, []);
  useEffect(() => {
    const controller = new AbortController(); const timeout = setTimeout(() => { controller.abort(); setView({ request, error: "Evaluation request timed out; no score available." }); }, action.key === undefined ? 12000 : 18000);
    fetchEvaluation(context, controller.signal, config, action.key).then(item => {
      if (!controller.signal.aborted) setView({ request, item });
    }).catch(error => { if (!controller.signal.aborted) setView({ request, error: error.message }); }).finally(() => clearTimeout(timeout));
    return () => { controller.abort(); clearTimeout(timeout); };
  }, [context, config, action, request]);
  const current = view?.request === request ? view : undefined;
  const item = current?.item; const evaluation = item?.evaluation;
  let invalid = ""; let lines: ReturnType<typeof legalLines> = [];
  try { if (item) lines = legalLines(replay, ply, item); } catch { invalid = "Evaluation refused: invalid score, history or principal variation."; }
  const loading = !current;
  const estimate = legacyEstimate(legacy, replay.fens[ply]);
  const cachedScore = !invalid ? lines[0]?.score : undefined;
  const legacyFallback = !loading && !cachedScore ? estimate : undefined;
  function chooseConfig(value: string) { setConfig(value); setAction(a => ({ id: a.id + 1 })); }
  return <div className="position-eval-layout"><EvaluationBar score={cachedScore || legacyFallback} loading={loading} tier={legacyFallback ? "LEGACY_ESTIMATE" : undefined} /><div className="position-eval-details" aria-live="polite">
    {loading ? <p role="status">{action.key === undefined ? "Looking up this position in the existing cache…" : "Fetching explicitly requested advisory cloud evidence…"}</p> : <>
      {(current.error || invalid) && <p role="alert">{current.error || invalid}</p>}
      {item && <><p>{evaluation ? evaluation.source.kind === "lichess_cloud" ? "ADVISORY_CLOUD · " : item.context_compatibility === "TRUSTED_CONTEXT_MATCH" ? "CONTEXT_COMPATIBLE_CACHE · " : "ADVISORY_CACHE · " : "NO_COMPATIBLE_EVIDENCE · "}{item.status} · {item.context_compatibility}{item.reason ? ` · ${item.reason}` : ""}</p>
        {!evaluation && <p>{unavailableReason(item)} Absence is not an equal position or evidence that no analysis exists.</p>}
        {item.requested_pvs && <p>Requested {item.requested_pvs} lines · goal {item.pv_goal_met && !invalid ? "met" : "not met"} · cloud attempt: {item.cloud_resolution_status} {item.cloud_resolution_reason}</p>}
        {item.status === "CONFIG_REQUIRED" && <p>Select an engine configuration; incompatible searches are not compared.</p>}
        {!!item.available?.length && <label>Engine configuration<select value={config} onChange={e => chooseConfig(e.target.value)}><option value="">Server compatibility selection</option>{item.available.map(c => <option key={c.config_hash} value={c.config_hash}>{c.source.engine_name || c.source.kind} · depth {c.available_depths.join(", ")} · {c.config_hash.slice(0, 12)}</option>)}</select></label>}
        {item.available_truncated && <p>Configuration list is bounded.</p>}
        {evaluation && !invalid && <><p><strong>{scoreText(lines[0]?.score)}</strong> · White-relative · achieved depth {evaluation.achieved_depth}</p><p>{evaluation.source.kind === "lichess_cloud" ? "Lichess cloud · advisory only; draw/repetition history unverified" : `${evaluation.source.engine_name || "Local engine"} ${evaluation.source.engine_version || ""} · local_stockfish · producer execution is not independently attested`}</p><p>Requested depth {evaluation.config.depth ?? "unspecified"} · time {evaluation.config.time_seconds ?? "unspecified"} s · {evaluation.returned_multipv}/{evaluation.config.multipv} PVs{item.partial ? " · partial search result" : ""}</p><ol className="position-eval-lines">{lines.map((line, i) => <li key={i}><strong>{scoreText(line.score)}</strong> · {line.sans.join(" ") || "Terminal checkmate"}</li>)}</ol><p>{evaluation.limitations?.join(" ")}</p><details><summary>Recorded source and search provenance</summary><pre>{JSON.stringify(evaluation, null, 2)}</pre></details></>}
      </>}
    </>}
    {estimate && <p><strong>LEGACY_ESTIMATE · {scoreText(estimate)} (White)</strong> · historical mover-relative cp converted for display only. Engine identity, depth, search settings and draw/history verification are unknown. No cache insertion or new reasoning/coaching claim.</p>}
    {legacy && !estimate && <p>Legacy value unavailable: absent/non-cp or mate sentinel, unknown schema/perspective, or position mismatch. No verified mate distance is reconstructed.</p>}
    <button disabled={loading} onClick={() => setAction(a => ({ id: a.id + 1 }))}>Refresh cached evaluation</button>
    <p className="position-eval-note">Selected position only · cache reads never launch an engine or contact a provider. Fewer than three lines means fewer recorded variations, not invented alternatives.</p>
    <details><summary>Explicit Lichess cloud lookup</summary><p>Cloud is advisory. A request sends this position to Lichess and may write only the separate evaluation cache. Server enablement and an operator key are required; read-only deployments refuse resolution.</p>{cloud?.cloud_enabled && cloud.cloud_resolution_allowed !== false ? <><label>Operator key<input type="password" autoComplete="off" value={operatorKey} onChange={e => setOperatorKey(e.target.value)} /></label><button disabled={loading || !operatorKey} onClick={() => { setAction(a => ({ id: a.id + 1, key: operatorKey })); setOperatorKey(""); }}>Fetch available cloud evaluation</button></> : <p>{cloudStatusExplanation(cloud)}</p>}</details>
  </div></div>;
}
