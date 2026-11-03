"use client";
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { usePGNReplay } from '@/components/PlayerDashboard/usePGNReplay';
import { collectBrowserEvidence, type BrowserEvidencePackage } from '@/lib/browser-reasoning-export';
import { browserLocksSupported } from '@/lib/browser-engine-lease';
import type { LiveSnapshot } from '@/lib/browser-stockfish-controller';
const subscribe = () => () => {};
export default function ReasoningEvidenceSearch({ pgn, ply }: { pgn: string; ply: number }) {
  const loaded = usePGNReplay(pgn); const request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false), [fallback, setFallback] = useState(false), [error, setError] = useState('');
  const [result, setResult] = useState<BrowserEvidencePackage>(), [state, setState] = useState<LiveSnapshot>();
  const locks = useSyncExternalStore(subscribe, browserLocksSupported, () => false);
  useEffect(() => () => request.current?.abort(), []);
  async function start() {
    if (!loaded.replay || request.current) return;
    const abort = new AbortController(); request.current = abort; setBusy(true); setResult(undefined); setError(''); setState(undefined);
    try { const value = await collectBrowserEvidence(loaded.replay, ply, s => { if (!abort.signal.aborted) setState(s); }, abort.signal, fallback); if (!abort.signal.aborted) setResult(value); }
    catch (e) { if (!abort.signal.aborted) setError((e as Error).message); }
    finally { if (request.current === abort) { setBusy(false); request.current = null; } }
  }
  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = `reasoning-receipts-${result.session}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <details className="reasoning-evidence-search"><summary>Collect browser reasoning evidence</summary>
    <p>Separate explicit search: depth 16 or 20 seconds, Hash 16 MiB, one browser worker. Stop Live Analysis first. No cloud/native execution or automatic acceptance. Browser RSS is not measurable.</p>
    {!locks && <label><input type="checkbox" checked={fallback} disabled={busy} onChange={e => setFallback(e.target.checked)} /> I closed other analysis tabs; only this tab is protected without Web Locks.</label>}
    <button disabled={busy || !loaded.replay || (!locks && !fallback)} onClick={() => void start()}>Start Reasoning Evidence Search</button>
    <button disabled={!busy} onClick={() => request.current?.abort()}>Cancel evidence search</button>
    <p role="status">{error || loaded.error || (busy ? `${state?.status || 'Verifying bundled assets'} · depth ${state?.depth || 0} · ${state?.lines.length || 0} PVs` : result ? result.error || `Collected ${result.result?.depth || 0} depth, ${result.result?.lines.length || 0} PVs. Not admitted.` : 'No evidence search requested.')}</p>
    {result && <><button onClick={download}>Download unattested receipts</button><p>{result.truncated ? 'Transcript truncated: export cannot qualify.' : result.result?.complete && (result.result?.depth || 0) >= 16 ? 'Collection meets preliminary depth/cohort checks. Offline receipt and legal-PV verification is still required.' : 'Incomplete or shallow result: insufficient evidence; actual output is retained.'}</p>
      <pre>{JSON.stringify({ engine: result.result?.engine, options_sent: result.result?.options, depth: result.result?.depth, lines: result.result?.lines, artifacts: result.artifacts, trust: result.trust }, null, 2)}</pre>
      <details><summary>Inspect raw UCI receipts</summary><pre>{JSON.stringify(result.events, null, 2)}</pre></details>
      <p>No independent verification or operator attestation is implied. Review the receipts, run the offline exporter below, then validate with the existing importer. Only after reviewing the exact bundle, explicitly accept it into a separately chosen evidence directory using the importer’s --acknowledge-external-engine flag. No catalog write occurs here.</p>
      <pre>python -m src.browser_reasoning_export receipts.json --output /canonical/independent/bundle.json{`\n`}python -m src.reasoning_import validate /canonical/independent/bundle.json</pre>
      <p>After operator review only:</p><pre>python -m src.reasoning_import accept /canonical/independent/bundle.json --artifact-dir /canonical/independent/artifacts --acknowledge-external-engine</pre>
      <p>The exporter reports structural validity and quality refusals separately. Importer acceptance is an operator attestation, not proof of chess correctness. Refresh “Inspect existing reasoning evidence” only after an explicit import into the server-configured catalog.</p></>}
  </details>;
}
