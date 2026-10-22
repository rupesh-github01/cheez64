"use client";
import { useEffect, useRef, useState } from "react";
import type { Replay } from "@/components/PlayerDashboard/pgn-context";
import { coverageCounts, evaluationStatus, inspectCoverage, unavailableReason, cloudStatusExplanation } from "@/lib/position-evaluation";
import type { CoverageRow } from "@/lib/position-evaluation";
export default function EvaluationCoverage({ replay }: { replay: Replay }) {
  const [start, setStart] = useState(0);
  const [rows, setRows] = useState<CoverageRow[]>();
  const [cloudRows, setCloudRows] = useState<CoverageRow[]>([]);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number[]>([]); const [key, setKey] = useState("");
  const [cloud, setCloud] = useState<Awaited<ReturnType<typeof evaluationStatus>>>();
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const end = Math.min(start + 19, replay.fens.length - 1);
  const valid = Number.isInteger(start) && start >= 0 && start < replay.fens.length;
  const counts = rows ? coverageCounts(rows) : undefined;
  async function inspect(cloudRequest = false) {
    if ((!cloudRequest && !valid) || (cloudRequest && (!selected.length || selected.length > 3 || !key))) return;
    controller.current?.abort(); const c = new AbortController(); controller.current = c;
    setBusy(true); setError("");
    const timeout = setTimeout(() => { c.abort(); setError("Coverage request timed out. Retry explicitly; nothing is scheduled."); setBusy(false); }, cloudRequest ? 20000 : 15000);
    c.signal.addEventListener("abort", () => clearTimeout(timeout), { once: true });
    const secret = cloudRequest ? key : undefined; if (cloudRequest) setKey("");
    try {
      const values = await inspectCoverage(replay, cloudRequest ? selected : Array.from({ length: end - start + 1 }, (_, i) => start + i), c.signal, secret);
      if (!c.signal.aborted) {
        if (cloudRequest) setCloudRows(values);
        else { setRows(values); setCloudRows([]); setSelected([]); }
        const status = await evaluationStatus(c.signal); if (!c.signal.aborted) setCloud(status);
      }
    } catch (e) { if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Coverage unavailable"); }
    finally { clearTimeout(timeout); if (controller.current === c && !c.signal.aborted) setBusy(false); }
  }
  return <details className="position-coverage"><summary>Selected-game evaluation coverage</summary>
    <p>Explicit read-only inspection of up to 20 positions per window. Counts describe this inspected selection, not the whole library. Legacy estimates are excluded.</p>
    <label>Window starts at ply<input type="number" min={0} max={replay.fens.length - 1} value={start} disabled={busy} onChange={e => setStart(Number(e.target.value))} /></label>
    <p>{replay.fens.length} recorded game positions · next window: {valid ? `${start}–${end}` : "invalid range"}</p>
    <button disabled={busy || !valid} onClick={() => inspect()}>Inspect cached coverage</button>
    <button disabled={busy || !valid || end === replay.fens.length - 1} onClick={() => setStart(end + 1)}>Next window</button>
    {busy && <p role="status">Reading selected contexts…</p>}{error && <p role="alert">{error}</p>}
    {counts && rows && <><p><strong>{counts.compatible}/{counts.inspected} compatible cached positions</strong> · {counts.trusted_context} exact trusted-context matches (producer unattested) · {counts.advisory} advisory · one PV: {counts.one} · two PVs: {counts.two} · three or more PVs: {counts.three_plus} · missing: {counts.missing} · incompatible/configuration required: {counts.incompatible} · unavailable: {counts.unavailable}</p>
      <p>Inspected plies {rows[0]?.ply}–{rows.at(-1)?.ply}; {rows.length === replay.fens.length ? "complete recorded-game selection" : `${replay.fens.length - rows.length} positions uninspected`}. Results are snapshots; refresh after external cache updates.</p>
      <ul className="position-coverage-rows">{rows.map(row => <li key={row.ply}><label><input type="checkbox" checked={selected.includes(row.ply)} disabled={busy || row.ply > 1000 || (!selected.includes(row.ply) && selected.length >= 3)} onChange={e => setSelected(s => e.target.checked ? [...s, row.ply] : s.filter(p => p !== row.ply))} />Ply {row.ply} · {row.item.status} · {row.lines >= 3 ? "3+" : row.lines} PVs</label><p>{unavailableReason(row.item)}</p>{row.item.requested_pvs && <p>Requested {row.item.requested_pvs} lines · goal {row.item.pv_goal_met ? "met" : "not met"} · {row.item.cloud_resolution_status} {row.item.cloud_resolution_reason}</p>}</li>)}</ul>
      {!!cloudRows.length && <><h4>Last explicit cloud results</h4><p>Cache coverage above remains the last local inspection snapshot. Inspect again to refresh counts; use Refresh cached evaluation to update the selected-position bar.</p>{cloudRows.map(r => <p key={r.ply}>Ply {r.ply} · {r.item.status} · {r.lines} displayed PVs · {unavailableReason(r.item)} · three-line goal {r.item.pv_goal_met ? "met" : "not met"} · {r.item.cloud_resolution_status} {r.item.cloud_resolution_reason}</p>)}</>}
      <h4>Explicit cloud selection · up to three positions</h4><p>Only checked positions are sent. Request three lines where available. Existing rate limits may defer later items; no automatic retries or delayed work. A smaller cached result remains useful without meeting the three-line goal.</p>
      {cloud?.cloud_enabled && cloud.cloud_resolution_allowed !== false ? <><label>Coverage operator key<input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} /></label><button disabled={busy || !key || !selected.length} onClick={() => inspect(true)}>Fetch cloud for {selected.length} selected positions</button></> : <p>{cloudStatusExplanation(cloud)}</p>}
    </>}
  </details>;
}
