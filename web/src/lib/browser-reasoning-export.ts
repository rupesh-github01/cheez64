/** Explicit, unattested browser receipts. No application API, catalog or cache writes. */
import type { Replay } from '@/components/PlayerDashboard/pgn-context';
import { BrowserStockfishController, type LiveSnapshot } from './browser-stockfish-controller';
import { ENGINE_IDENTITY, livePosition } from './browser-stockfish';
export type ReceiptEvent = { seq: number; elapsed_ms: number; worker: string; session: string; kind: string; text: string };
export type BrowserEvidencePackage = {
  schema_version: 'browser-reasoning-receipts/1'; session: string; context: { fen: string; variant: 'standard'; root_fen: string; moves: string[]; root_history_complete: boolean; draw_policy: 'fide_claims_and_automatic_v1' };
  artifacts: { js_sha256: string; wasm_sha256: string }; events: ReceiptEvent[]; truncated: boolean;
  result?: LiveSnapshot; error?: string; trust: 'BROWSER_OBSERVED_NOT_ATTESTED';
};
async function measuredAsset(name: string, expected: string, signal: AbortSignal, maximum: number) {
  const response = await fetch(`/engines/stockfish-19.0.1/${name}`, { signal, cache: 'no-store' });
  if (!response.ok || !response.body) throw Error('Engine asset unavailable');
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try { for (;;) { const { done, value } = await reader.read(); if (done) break; size += value.length; if (size > maximum) throw Error('Engine asset size bound'); chunks.push(value); } }
  finally { await reader.cancel(); }
  const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(x => x.toString(16).padStart(2, '0')).join('');
  if (hash !== expected) throw Error('Engine asset identity mismatch'); return hash;
}
export async function collectBrowserEvidence(replay: Replay, ply: number, progress: (state: LiveSnapshot) => void, signal: AbortSignal, fallback = false): Promise<BrowserEvidencePackage> {
  const session = crypto.randomUUID(), started = performance.now(), position = livePosition(replay, ply, session);
  if (position.terminal || !replay.root_history_complete) throw Error('A nonterminal position with complete initial-board history is required.');
  const abort = new AbortController(); const cancel = () => abort.abort(); signal.addEventListener('abort', cancel, { once: true }); if (signal.aborted) cancel();
  const deadline = setTimeout(cancel, 45000);
  const pack: BrowserEvidencePackage = { schema_version: 'browser-reasoning-receipts/1', session, context: { fen: position.fen, variant: 'standard', root_fen: position.root, moves: position.moves, root_history_complete: replay.root_history_complete, draw_policy: 'fide_claims_and_automatic_v1' }, artifacts: { js_sha256: '', wasm_sha256: '' }, events: [], truncated: false, trust: 'BROWSER_OBSERVED_NOT_ATTESTED' };
  let engine: BrowserStockfishController | undefined, finish: (() => void) | undefined, count = 0, size = 0;
  const stop = () => { pack.error = 'Collection cancelled or overall deadline reached'; engine?.stop(); finish?.(); };
  abort.signal.addEventListener('abort', stop, { once: true });
  try {
    pack.artifacts.js_sha256 = await measuredAsset('stockfish-19-lite-single.js', ENGINE_IDENTITY.js_sha256, abort.signal, 65536);
    pack.artifacts.wasm_sha256 = await measuredAsset('stockfish-19-lite-single.wasm', ENGINE_IDENTITY.wasm_sha256, abort.signal, 2 * 1024 * 1024);
    if (abort.signal.aborted) throw Error('Collection cancelled');
    const done = new Promise<void>(resolve => { finish = resolve; });
    engine = new BrowserStockfishController(state => { pack.result = state; progress(state); if (state.status === 'complete' || state.status === 'error') { pack.error = state.error; finish?.(); } }, {
      reasoningEvidence: true,
      observe: (kind, text) => {
        size += text.length; if (size > 1024 * 1024 || pack.events.length >= 8192) { pack.truncated = true; throw Error('Evidence transcript budget exceeded'); }
        if (kind === 'worker_started') count++;
        pack.events.push({ seq: pack.events.length, elapsed_ms: performance.now() - started, worker: `${session}:${count}`, session, kind, text });
      },
    });
    engine.start(position, fallback); await done;
  } catch (error) { pack.error = error instanceof Error ? error.message : 'Evidence collection failed'; }
  finally { engine?.dispose(); clearTimeout(deadline); signal.removeEventListener('abort', cancel); abort.signal.removeEventListener('abort', stop); }
  return pack;
}
