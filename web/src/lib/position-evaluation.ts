import { Chess } from "chess.js";
import type { Replay } from "@/components/PlayerDashboard/pgn-context";
export type Score = { kind: "cp"; cp: number } | { kind: "mate"; mate: number } | { kind: "terminal_checkmate"; mate: 0; winner: "white" | "black" };
export type CacheResult = { status: string; reason?: string; context_compatibility: string; partial: boolean; available_truncated?: boolean; available: { config_hash: string; source: { kind: string; engine_name?: string }; available_depths: number[] }[]; evaluation: null | { perspective: string; achieved_depth: number; returned_multipv: number; source: { kind: string; engine_name?: string; engine_version?: string }; config: { multipv: number; depth: number | null; time_seconds: number | null }; pvs: (Score & { moves: string[] })[]; limitations: string[] } };
export function scoreText(score?: Score) {
  if (!score) return "Unavailable";
  if (score.kind === "cp") return `${score.cp >= 0 ? "+" : ""}${(score.cp / 100).toFixed(2)}`;
  if (score.kind === "mate") return `${score.mate > 0 ? "+" : "−"}M${Math.abs(score.mate)}`;
  return `Checkmate · ${score.winner} wins`;
}
export function whiteFill(score?: Score): number | undefined {
  if (!score) return undefined;
  if (score.kind === "terminal_checkmate") return score.winner === "white" ? 100 : 0;
  if (score.kind === "mate") return score.mate > 0 ? 100 : 0;
  // A visual compression of centipawns, not a calibrated winning probability.
  return Math.max(4, Math.min(96, 100 / (1 + Math.exp(-0.00368208 * score.cp))));
}
function validScore(pv: Score) {
  return pv.kind === "cp" ? Number.isInteger(pv.cp) : pv.kind === "mate" ? Number.isInteger(pv.mate) && pv.mate !== 0 : pv.kind === "terminal_checkmate" && pv.mate === 0 && ["white", "black"].includes(pv.winner);
}
export function legalLines(replay: Replay, ply: number, result: CacheResult) {
  const e = result.evaluation;
  if (!e) return [];
  if (!["CACHE_HIT", "CLOUD_HIT"].includes(result.status) || !["TRUSTED_CONTEXT_MATCH", "ADVISORY_CONTEXT_UNVERIFIED"].includes(result.context_compatibility) || e.perspective !== "white" || !["local_stockfish", "lichess_cloud"].includes(e.source?.kind) || (e.source.kind === "lichess_cloud" && result.context_compatibility !== "ADVISORY_CONTEXT_UNVERIFIED") || !Number.isInteger(e.achieved_depth) || e.achieved_depth < 0 || !Array.isArray(e.pvs) || e.pvs.length < 1 || e.pvs.length > 5 || e.pvs.length !== e.returned_multipv) throw new Error("Invalid evaluation contract");
  const roots = new Set<string>();
  const lines = e.pvs.map(pv => {
    if (!validScore(pv) || !Array.isArray(pv.moves) || pv.moves.length > 64) throw new Error("Invalid score or PV bounds");
    const board = new Chess(replay.root_fen);
    for (const uci of replay.moves.slice(0, ply)) board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined });
    if (board.fen() !== replay.fens[ply]) throw new Error("History mismatch");
    if (pv.kind === "terminal_checkmate") {
      if (pv.moves.length || !board.isCheckmate() || pv.winner !== (board.turn() === "w" ? "black" : "white")) throw new Error("Unproven terminal score");
    } else if (!pv.moves.length) throw new Error("Empty nonterminal PV");
    const root = pv.moves[0] || "terminal";
    if (roots.has(root)) throw new Error("Duplicate PV root"); roots.add(root);
    const sans: string[] = [];
    for (const uci of pv.moves) {
      if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(uci) || board.isCheckmate() || board.isStalemate()) throw new Error("Illegal PV continuation");
      const move = board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined });
      sans.push(move.san);
    }
    return { score: pv as Score, sans };
  });
  return lines.slice(0, 3); // One actual record, never a union of single-PV searches.
}
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export async function fetchEvaluation(context: unknown, signal: AbortSignal, config = "", cloudKey?: string): Promise<CacheResult> {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const cloud = cloudKey !== undefined;
  const item = { context, require_trusted: false, allow_advisory: true, allow_partial: true, allow_cloud: cloud, min_pvs: 1, ...(cloud ? { source: "lichess_cloud", cloud_multipv: 3 } : config ? { config_hash: config } : {}) };
  const response = await fetch(`${API}/api/evaluations/${cloud ? "resolve" : "lookup-batch"}`, { method: "POST", cache: "no-store", signal, headers: { "Content-Type": "application/json", ...(cloud ? { "X-Evaluation-Key": cloudKey } : {}) }, body: JSON.stringify(cloud ? item : { items: [item] }) });
  if (!response.ok) throw new Error(response.status === 403 ? "Resolution refused: server read-only or operator key unavailable/invalid." : `Evaluation service unavailable (${response.status}).`);
  const data = await response.json();
  const result = cloud ? data : data.items?.[0];
  if (!result || typeof result.status !== "string" || !Array.isArray(result.available)) throw new Error("Invalid evaluation response; no score available.");
  return result;
}
export async function evaluationStatus(signal: AbortSignal): Promise<{ cloud_enabled: boolean }> {
  const response = await fetch(`${API}/api/evaluations/status`, { cache: "no-store", signal });
  if (!response.ok) throw new Error("Evaluation status unavailable");
  return response.json();
}
