"use client";
import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { loadReasoning, reasoningKey, reasoningScore, currentReasoningView, type Reasoning, type Availability, type ReasoningLine } from "@/lib/position-reasoning";
import "./reasoning.css";
const Board = dynamic(() => import("@/components/PlayerDashboard/BoardPreview"), { ssr: false, loading: () => <p>Loading legal variation board…</p> });
function LineExplorer({ line }: { line: ReasoningLine }) {
  const [step, setStep] = useState(0);
  return <article className="reasoning-line"><h4>{line.sans[0] || line.candidate_uci} · {line.origin.replaceAll("_", " ")}</h4>
    <Board fen={line.fens[step]} /><div className="reasoning-actions"><button disabled={!step} onClick={() => setStep(n => n - 1)}>Previous variation move</button><span>Ply {step} / {line.moves.length}</span><button disabled={step === line.moves.length} onClick={() => setStep(n => n + 1)}>Next variation move</button></div>
    {step > 0 && line.steps[step - 1] && <div className="reasoning-claim"><strong>Board fact · {line.steps[step - 1].mover}</strong><p>{line.steps[step - 1].san}: {line.steps[step - 1].events.map(e => `${e.kind === "castling_rook" ? "rook " : ""}${e.from} → ${e.to}${e.capture ? `, captures ${e.capture.piece} on ${e.capture.square}` : ""}${e.promotion ? `, promotes to ${e.promotion}` : ""}`).join("; ")}{line.steps[step - 1].check ? " · check" : ""}{line.steps[step - 1].terminal ? ` · ${line.steps[step - 1].terminal}` : ""}</p></div>}
    <ol>{line.sans.map((san, i) => <li key={i}><button aria-current={step === i + 1 ? "step" : undefined} onClick={() => setStep(i + 1)}>{i + 1}. {san} · {line.moves[i]}</button></li>)}</ol>
    <p>{line.opponent_response ? `Recorded principal reply: ${line.opponent_response.san}. Other defenses are not exhaustively established.` : "No qualified recorded opponent reply is available."}</p>
    {line.display_truncated && <p>Showing {line.moves.length} of {line.full_validated_plies} fully validated plies. This prefix is not a complete proof.</p>}
  </article>;
}
export default function ReasoningPanel({ gameID, pgn, ply, disabled = false }: { gameID: string; pgn: string; ply: number; disabled?: boolean }) {
  const [alternative, setAlternative] = useState("");
  const [choice, setChoice] = useState<{ source: string; bundle: string; profile: string }>();
  const sourceKey = JSON.stringify([gameID, pgn, ply, disabled]);
  const bundle = choice?.source === sourceKey ? choice.bundle : "";
  const profile = choice?.source === sourceKey ? choice.profile : "";
  function setBundle(value: string) { setChoice({ source: sourceKey, bundle: value, profile: "" }); }
  function setProfile(value: string) { setChoice({ source: sourceKey, bundle, profile: value }); }
  const key = reasoningKey(gameID, pgn, ply, alternative, bundle, profile, disabled);
  const [availability, setAvailability] = useState<{ key: string; value: Availability }>();
  const [view, setView] = useState<{ key: string; controller: AbortController; loading?: boolean; error?: string; data?: Reasoning }>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), [key]);
  const active = view && currentReasoningView(view.key, key, view.controller.signal.aborted) ? view : undefined;
  const catalog = availability?.key === sourceKey ? availability.value : undefined;
  const data = active?.data;
  async function load(inspect = false) {
    if (disabled) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setView({ key, controller, loading: true });
    try {
      if (inspect) {
        const choices = await loadReasoning(gameID, pgn, ply, controller.signal, { availability: true }) as Availability;
        if (!controller.signal.aborted) setAvailability({ key: sourceKey, value: choices });
        // Explicit inspection also provides structural fallback; selecting a bundle is a separate action.
      }
      const value = await loadReasoning(gameID, pgn, ply, controller.signal, { alternative: alternative.trim().toLowerCase(), bundle, profile }) as Reasoning;
      if (!controller.signal.aborted) setView({ key, controller, data: value });
    } catch (error) { if (!controller.signal.aborted) setView({ key, controller, error: (error as Error).message }); }
  }
  const profiles = [...new Set([...(catalog?.bundles.find(b => b.id === bundle)?.profiles || []), ...(data?.comparison.available_profiles || [])])];
  return <section className="reasoning-panel" aria-label="Position reasoning"><p className="reasoning-kicker">SELECTED-POSITION REASONING · EXISTING EVIDENCE ONLY</p><h2>Compare concrete continuations</h2>
    <p>Compare recorded candidates and a legal alternative. No search, cloud call or background job is started.</p>
    {disabled ? <p>Return to the recorded mainline to inspect reasoning.</p> : <><button onClick={() => void load(true)} disabled={active?.loading}>Inspect existing reasoning evidence</button>
      <label>Alternative move (coordinate notation)<input value={alternative} onChange={e => setAlternative(e.target.value)} placeholder="g1f3 or a7a8n" autoComplete="off" /></label>
      {catalog && <><p>Catalog: {catalog.status}. Entries describe availability, not proof of engine execution.</p><label>Evidence bundle<select value={bundle} onChange={e => setBundle(e.target.value)}><option value="">Retained local evidence / structural fallback</option>{catalog.bundles.map(b => <option key={b.id} value={b.id}>{b.origin} · {b.id.slice(0, 16)}</option>)}</select></label></>}
      {!!profiles.length && <label>Exact comparison profile<select value={profile} onChange={e => setProfile(e.target.value)}><option value="">Compatible cohort selection</option>{profiles.map(p => <option key={p} value={p}>{p.slice(0, 20)}</option>)}</select></label>}
      <button onClick={() => void load()} disabled={active?.loading}>Compare selected evidence</button>
      {!active && <p className="reasoning-muted">{view ? "Selection changed. Previous results are hidden; request this position explicitly." : "No reasoning request has been made. Inspect or compare when ready."}</p>}
    </>}
    {active?.loading && <p role="status">Validating history, complete variations and evidence…</p>}{active?.error && <p role="alert">{active.error}</p>}
    {data && <><p className="reasoning-status">{data.status} · {data.evidence.status} · {data.evidence.context_compatibility}</p>
      {data.evidence.interpretation && <strong>{data.evidence.interpretation}</strong>}
      {data.evidence.reason && <p>{data.evidence.reason}</p>}
      <p>{data.source.source_freshness} · root ply {data.source.root_ply} · {data.root.side_to_move} to move.</p>
      {data.status === "CONFIG_REQUIRED" && <p>Select one exact profile. Different settings are not combined.</p>}
      {!data.comparison.compared_moves?.length && <p>Insufficient compatible evidence to rank moves. A missing evaluation is unavailable, not zero.</p>}
      {data.comparison.reason && <p>Comparison limitation: {data.comparison.reason}</p>}
      <div className="reasoning-table"><table><caption>Recorded candidates · subset only</caption><thead><tr><th>Move</th><th>Recorded score</th><th>Search support</th></tr></thead><tbody>{data.candidates.map(c => <tr key={c.uci}><td><button onClick={() => setAlternative(c.uci)}>{c.san} ({c.uci})</button><small>{c.origin.replaceAll("_", " ")}</small></td><td>{reasoningScore(c.score)}{c.terminal_outcome && <p>Board fact: {c.terminal_outcome.termination} · {c.terminal_outcome.winner ? `${c.terminal_outcome.winner} wins` : "draw"}</p>}</td><td>{c.quality.replaceAll("_", " ")}{c.achieved_depth != null && <><br />Depth {c.achieved_depth} / requested {c.requested_depth ?? "not depth-limited"}{c.requested_depth_met === false && " · requested depth unmet"}</>}</td></tr>)}</tbody></table></div>
      {data.comparison.cp_differences?.map(d => <p key={`${d.a}:${d.b}`}>{d.a} versus {d.b}: recorded difference {d.for_root_mover} cp for the root mover{d.recorded_scores_close ? " · close recorded scores under the 30 cp policy" : ""}. This is not CPL or causal attribution.</p>)}
      {data.comparison.score_relation && <p>{data.comparison.score_relation.replaceAll("_", " ")}</p>}
      <div className="reasoning-lines">{data.lines.map(line => <LineExplorer key={`${key}:${line.id}`} line={line} />)}</div>
      <h3>What these variations show</h3>{data.explanations.map(e => <article className="reasoning-claim" key={e.claim_id}><strong>{e.class.replaceAll("_", " ")}</strong><p>{e.text}</p><small>Evidence: {data.claims.find(c => c.id === e.claim_id)?.references.join(", ")}</small></article>)}
      {!data.explanations.length && <p>No supported claim was generated within this evidence scope.</p>}
      <details><summary>Checkpoint facts and positional descriptors</summary>{data.checkpoints.map(point => <div key={point.id}><h4>{point.id}</h4>{point.changed_features?.map((f, i) => <p key={i}>{f.color || "both"} · {f.feature.replaceAll("_", " ")} · {f.attribution}</p>)}{point.findings.map((f, i) => <p key={i}>{f.provenance}: {f.motif.replaceAll("_", " ")} on {f.squares.join(", ")}</p>)}<pre>{JSON.stringify({ features: point.features, detector_coverage: point.coverage }, null, 2)}</pre></div>)}</details>
      <details><summary>Provenance, configuration and limitations</summary><pre>{JSON.stringify(data.evidence, null, 2)}</pre><p>{data.limitations.join(" ")}</p><p>{data.coverage.partial ? "Partial computation." : "Bounded computation."} {data.coverage.child_pushes} child/PV pushes.</p></details>
    </>}
  </section>;
}
