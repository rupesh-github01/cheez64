"use client";
import { useEffect, useRef, useState } from "react";
import { loadUnderstanding, understandingKey, typedScore } from "@/lib/position-understanding";
import type { Understanding } from "@/lib/position-understanding";
import { Evidence, Panel, State } from "@/components/PlayerDashboard/Primitives";
import "@/components/PlayerDashboard/dashboard.css";
import "./understanding.css";

export default function UnderstandingPanel({ gameID, pgn, ply, disabled = false }: { gameID: string; pgn: string; ply: number; disabled?: boolean }) {
  const key = understandingKey(gameID, pgn, ply, disabled);
  const [view, setView] = useState<{ key: string; loading?: boolean; data?: Understanding; error?: string }>();
  const [config, setConfig] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); }, [key]);
  const active = view?.key === key ? view : undefined;
  const data = active?.data;
  async function load(evidence = false) {
    controller.current?.abort(); const owned = new AbortController(); controller.current = owned;
    setView({ key, loading: true });
    try {
      const result = await loadUnderstanding(gameID, pgn, ply, owned.signal, evidence, evidence ? config : "");
      if (!owned.signal.aborted) setView({ key, data: result });
    } catch (error) {
      if (!owned.signal.aborted) setView({ key, error: error instanceof Error ? error.message : "Understanding unavailable" });
    }
  }
  const configs = [...new Set(data?.engine_evidence.contexts?.flatMap(x => x.available_configs || []) || [])];
  return <div className="pd-root pd-understanding" style={{ padding: 0 }}><Panel title="Understand this position" kicker="On demand · separate from legacy scoring and coaching">
    <p className="pd-muted">Recorded game ply {ply}. Board facts and heuristic examination prompts work without engine analysis.</p>
    {disabled ? <State empty="Return to the recorded game position to load understanding. Variation preview is a different history." /> : <>
      <div className="pd-actions"><button disabled={active?.loading} onClick={() => load()}>Load position understanding</button><button disabled={active?.loading} onClick={() => load(true)}>Read existing accepted evidence</button></div>
      <p className="pd-muted">These actions only read. No search, cloud request, queue or job is started. Engine evidence must have an accepted non-simulated producer.</p>
      {!active && <State empty={view ? "Selection changed. Load this position to replace the previous insight." : "Choose Load to inspect this position. No understanding request has been made."} />}
      {(active?.loading || active?.error) && <State loading={active?.loading} error={active?.error} />}
      {data && <>
        <p className="pd-muted">{data.source.source_freshness} · {new Date(data.generated_at).toLocaleTimeString()} · {data.schema_version}</p>
        {data.coverage.partial && <p className="pd-notice">Partial assessment{data.coverage.budget_exhausted ? " · computation budget reached" : " · bounded candidate or output coverage"}. {data.coverage.omitted_findings || 0} findings omitted. Absence of a finding does not establish absence of a tactic.</p>}
        {!data.positions.after && <State empty="The PGN could not be completely validated within this request's budget. No valid-prefix position is presented as complete." />}
        <h3>What changed?</h3>
        {ply === 0 ? <p className="pd-muted">Initial position: no preceding move or invented change.</p> : <>{data.changes.events.map((event, i) => <p className="pd-muted" key={i}>{event.kind === "played_move" ? event.san : "Castling rook"}: {event.from} → {event.to}{event.capture ? ` · captured ${event.capture.piece} on ${event.capture.square}` : ""}{event.promotion ? ` · promoted to ${event.promotion}` : ""}</p>)}<p className="pd-muted">{data.changes.created.length} returned relationships created · {data.changes.removed.length} removed · {data.changes.persisted.length} persisted.</p><p className="pd-muted">Changed descriptors: {data.changes.changed.map(x => `${x.color || "both"} ${x.feature.replaceAll("_", " ")}`).join(" · ") || "None established in the completed comparison"}. Strategic impact is not inferred.</p></>}
        <h3>Tactical relationships</h3>
        {!data.findings.length && <p className="pd-muted">No motif returned within the assessed scope. Positional descriptors remain useful; no tactical-safety claim follows.</p>}
        <div className="pd-facts">{data.findings.map(f => <details className="pd-fact" key={f.id}><summary>{f.motif.replaceAll("_", " ")} · {f.stage}</summary><Evidence value={f.provenance} /><p>{f.squares.join(", ")}</p><pre className="pd-json">{JSON.stringify(f.facts, null, 2)}</pre><p>{f.limitations.join(" ")}</p></details>)}</div>
        <h3 className="pd-subheading">Why examine this position?</h3>
        {data.explanations.map((e, i) => <article className="pd-fact" key={i}><Evidence value={e.provenance} /><p>{e.what_changed}</p><p>{e.why_examine}</p><h3>Examine next</h3><p>{e.examine_next}</p><small>Evidence: {e.supports.join(", ")}</small></article>)}
        {data.features.after && <><h3 className="pd-subheading">Positional descriptors</h3><div className="pd-columns">{Object.entries(data.features.after.colors).map(([color, f]) => <div key={color}><h3>{color}</h3><p className="pd-muted">King {f.king_safety.king_square} · {f.king_safety.geometrically_attacked_ring.length} geometrically attacked ring squares. This is not a safety score.</p><p className="pd-muted">Isolated pawns: {f.pawn_structure.isolated.join(", ") || "none by this rule"}. Passed by pawn geometry: {f.pawn_structure.passed_by_pawn_geometry.join(", ") || "none"}.</p><p className="pd-muted">Open files: {f.files.open.join(", ") || "none"} · semi-open: {f.files.semi_open.join(", ") || "none"}.</p><p className="pd-muted">Development: {f.development.status} · heuristic enemy-half control {f.space.count}/{f.space.denominator} squares.</p>{Object.entries(f).map(([name, detail]) => <details key={name}><summary>{name.replaceAll("_", " ")}</summary><pre className="pd-json">{JSON.stringify(detail, null, 2)}</pre></details>)}</div>)}</div><details><summary>Material counts & conventional weights</summary><pre className="pd-json">{JSON.stringify(data.features.after.material, null, 2)}</pre></details></>}
        <h3 className="pd-subheading">Existing engine evidence</h3><p className="pd-muted">{data.engine_evidence.status}. Context compatibility and producer admission are separate guarantees.</p>
        {data.engine_evidence.contexts?.map((c, i) => <p className="pd-muted" key={i}>{c.stage} · {c.context_compatibility} · {c.understanding_admission}{c.rejected_reasons?.length ? ` · ${c.rejected_reasons.join(", ")}` : ""}</p>)}
        {!!configs.length && <label>Exact configuration<select value={config} onChange={e => setConfig(e.target.value)}><option value="">Compatible server selection</option>{configs.map(c => <option key={c}>{c}</option>)}</select></label>}
        {config && <button onClick={() => setConfig("")}>Clear chosen configuration</button>}
        {data.engine_evidence.accepted.map((record, i) => <article className="pd-fact" key={i}><Evidence value="ENGINE_VERIFIED" /><p>{record.stage}: accepted non-simulated local search · achieved depth {record.evaluation.achieved_depth}, requested {record.evaluation.config.depth ?? "unknown"}, requested time {record.evaluation.config.time_seconds ?? "unknown"} s.</p><p>Engine-supported search evidence; no motif causation or forced gain is inferred.</p>{record.evaluation.pvs.map((pv, j) => <p key={j}>{typedScore(pv)} · {pv.moves.join(" ")} · {pv.moves.length}/{pv.available_plies} PV plies shown</p>)}<details><summary>Accepted work, build, NNUE, reset policy & options</summary><pre className="pd-json">{JSON.stringify(record, null, 2)}</pre></details></article>)}
        {data.engine_evidence.score_change_cp_white !== undefined && <p className="pd-muted">White-relative searched score change: {data.engine_evidence.score_change_cp_white} cp. {data.engine_evidence.score_change_limitations}</p>}
        <details className="pd-methods"><summary>Detector scope, provenance & limitations</summary><pre className="pd-json">{JSON.stringify(data.coverage, null, 2)}</pre>{data.limitations.map((text, i) => <p key={i}>{text}</p>)}</details>
      </>}
    </>}
  </Panel></div>;
}
