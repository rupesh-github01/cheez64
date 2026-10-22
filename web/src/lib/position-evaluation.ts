import { Chess } from "chess.js";
import type { Replay } from "@/components/PlayerDashboard/pgn-context";
export type Score = { kind: "cp"; cp: number } | { kind: "mate"; mate: number } | { kind: "terminal_checkmate"; mate: 0; winner: "white" | "black" };
export type CacheResult = { status: string; reason?: string; context_compatibility: string; partial: boolean; requested_pvs?: number; pv_goal_met?: boolean; cloud_resolution_status?: string; cloud_resolution_reason?: string; available_truncated?: boolean; available: { config_hash: string; source: { kind: string; engine_name?: string }; available_depths: number[] }[]; evaluation: null | { perspective: string; achieved_depth: number; returned_multipv: number; source: { kind: string; engine_name?: string; engine_version?: string }; config: { multipv: number; depth: number | null; time_seconds: number | null }; pvs: (Score & { moves: string[] })[]; limitations: string[] } };
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
  const item = { context, require_trusted: false, allow_advisory: true, allow_partial: true, allow_cloud: cloud, min_pvs: 1, ...(cloud ? { source: "lichess_cloud", cloud_multipv: 3, prefer_cloud_pvs: true } : config ? { config_hash: config } : {}) };
  const response = await fetch(`${API}/api/evaluations/${cloud ? "resolve" : "lookup-batch"}`, { method: "POST", cache: "no-store", signal, headers: { "Content-Type": "application/json", ...(cloud ? { "X-Evaluation-Key": cloudKey } : {}) }, body: JSON.stringify(cloud ? item : { items: [item] }) });
  if (!response.ok) throw new Error(response.status === 403 ? "Resolution refused: server read-only or operator key unavailable/invalid." : `Evaluation service unavailable (${response.status}).`);
  const data = await response.json();
  const result = cloud ? data : data.items?.[0];
  if (!result || typeof result.status !== "string" || !Array.isArray(result.available)) throw new Error("Invalid evaluation response; no score available.");
  return result;
}
export async function evaluationStatus(signal: AbortSignal): Promise<{ cloud_enabled: boolean; cloud_resolution_allowed?: boolean; read_only?: boolean; cloud_operator_key_configured?: boolean }> {
  const response = await fetch(`${API}/api/evaluations/status`, { cache: "no-store", signal });
  if (!response.ok) throw new Error("Evaluation status unavailable");
  return response.json();
}

export type LegacySample = { value: number | null; color: string; fen: string; schema: string };
export function legacyEstimate(sample: LegacySample | undefined, fen: string): Score | undefined {
  if (!sample || sample.schema !== "1.1.0" || !Number.isInteger(sample.value) || sample.value === null || Math.abs(sample.value) >= 90000 || !["White", "Black"].includes(sample.color)) return undefined;
  try { if (new Chess(sample.fen).fen() !== new Chess(fen).fen()) return undefined; } catch { return undefined; }
  return { kind: "cp", cp: sample.color === "White" ? sample.value : -sample.value };
}
export function unavailableReason(item: CacheResult): string {
  const setup: Record<string, string> = {
    CACHE_NOT_CONFIGURED: "Evaluation cache is not configured on the API server. Set CHESS_COACH_EVAL_CACHE_DB to an explicitly initialized independent cache and restart the API. An empty cache contains no scores or lines.",
    CACHE_LIMITS_INVALID: "The API's CHESS_COACH_EVAL_LIMITS configuration is invalid. Correct it and restart; do not recreate the cache.",
    CACHE_PATH_REFUSED: "The configured evaluation cache path was refused. Use a canonical independent file, never the library, a backup, symlink or hard-link alias. Do not delete existing data.",
    CACHE_FILE_MISSING: "The configured evaluation cache file does not exist. An operator must explicitly initialize that independent file; page loads never create it. Initialization alone supplies no evaluations.",
    CACHE_SCHEMA_INVALID: "The configured file is not an initialized evaluation cache or its schema cannot be read. Verify the path and preserve the file; do not overwrite or migrate it automatically.",
    CACHE_VERSION_INCOMPATIBLE: "The evaluation cache version is incompatible. Preserve it and ask the operator to check the running API version; no automatic migration is performed.",
    CACHE_BUSY: "The evaluation cache is temporarily locked. Retry later; it does not need initialization.",
    CACHE_DATABASE_ERROR: "The evaluation cache could not be read due to a database error. Check permissions, locking and file integrity; do not recreate it as an initialization fix.",
    CACHE_IO_ERROR: "The evaluation cache file could not be accessed. Check filesystem permissions and availability; do not recreate it automatically.",
    CACHE_DATA_INVALID: "An evaluation cache record could not be decoded or validated. Preserve the cache and ask the operator to inspect it; this is not a missing-cache setup error."
  };
  if (!item.evaluation && item.reason && setup[item.reason]) return setup[item.reason];
  if (item.evaluation) return item.context_compatibility === "TRUSTED_CONTEXT_MATCH" ? "Context-compatible cache; producer execution not independently attested" : "Advisory evidence; draw/history compatibility unverified";
  const reasons: Record<string, string> = {
    CACHE_MISS: "No record for this exact history or accepted advisory context; other histories are not searched.",
    CONFIG_REQUIRED: "Several engine configurations exist; select one in the position widget.",
    INCOMPATIBLE: "Records exist but do not meet source, configuration, freshness, depth or PV requirements.",
    UNAVAILABLE: "Evaluation cache/service is absent, incompatible or unavailable.",
    CLOUD_DISABLED: "Server cloud access is disabled or request opt-in is absent.",
    CLOUD_MISS: "No upstream cloud record was available for this request.",
    DEFERRED: "Request deferred by shared rate/lease or batch budget; retry explicitly later.",
    RATE_LIMITED: "Provider cooldown is active; retry explicitly later.",
    HISTORY_LIMIT: "Complete context exceeds 1,000 plies; history is never truncated.",
    INVALID_EVIDENCE: "Returned score or PV failed legal validation."
  };
  return `${reasons[item.status] || "No admissible evaluation was returned."}${item.reason ? ` (${item.reason})` : ""}`;
}
export type CoverageRow = { ply: number; item: CacheResult; lines: number };
export function coverageCounts(rows: CoverageRow[]) {
  return { inspected: rows.length, compatible: rows.filter(r => r.item.evaluation).length,
    trusted_context: rows.filter(r => r.item.evaluation && r.item.context_compatibility === "TRUSTED_CONTEXT_MATCH").length,
    advisory: rows.filter(r => r.item.evaluation && r.item.context_compatibility === "ADVISORY_CONTEXT_UNVERIFIED").length,
    one: rows.filter(r => r.lines === 1).length, two: rows.filter(r => r.lines === 2).length, three_plus: rows.filter(r => r.lines >= 3).length,
    missing: rows.filter(r => r.item.status === "CACHE_MISS").length,
    incompatible: rows.filter(r => ["CONFIG_REQUIRED", "INCOMPATIBLE", "INVALID_EVIDENCE"].includes(r.item.status)).length,
    unavailable: rows.filter(r => !r.item.evaluation && !["CACHE_MISS", "CONFIG_REQUIRED", "INCOMPATIBLE", "INVALID_EVIDENCE"].includes(r.item.status)).length };
}
function rowFor(replay: Replay, ply: number, item: CacheResult): CoverageRow {
  try { return { ply, item, lines: legalLines(replay, ply, item).length }; }
  catch { return { ply, lines: 0, item: { ...item, evaluation: null, status: "INVALID_EVIDENCE", context_compatibility: "INCOMPATIBLE", pv_goal_met: false } }; }
}
export async function inspectCoverage(replay: Replay, plies: number[], signal: AbortSignal, cloudKey?: string): Promise<CoverageRow[]> {
  const cloud = cloudKey !== undefined;
  if (!plies.length || plies.length > (cloud ? 3 : 20) || new Set(plies).size !== plies.length || plies.some(p => !Number.isInteger(p) || p < 0 || p >= replay.fens.length)) throw new Error("Select up to twenty local or three cloud positions.");
  const rows: CoverageRow[] = []; let batch: { ply: number; query: object }[] = [];
  async function flush() {
    if (!batch.length) return;
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    const queries = batch.map(r => r.query);
    const response = await fetch(`${API}/api/evaluations/${cloud ? "resolve-batch" : "lookup-batch"}`, { method: "POST", cache: "no-store", signal, headers: { "Content-Type": "application/json", ...(cloud ? { "X-Evaluation-Key": cloudKey! } : {}) }, body: JSON.stringify({ items: queries }) });
    if (!response.ok) throw new Error(response.status === 403 ? "Resolution refused: read-only server or invalid/unconfigured operator key." : `Coverage unavailable (${response.status}).`);
    const data = await response.json();
    if (!Array.isArray(data.items) || data.items.length !== batch.length) throw new Error("Incomplete coverage response");
    batch.forEach((r, i) => rows.push(rowFor(replay, r.ply, data.items[i]))); batch = [];
  }
  for (const ply of plies) {
    if (ply > 1000) { rows.push({ ply, lines: 0, item: { status: "HISTORY_LIMIT", context_compatibility: "UNAVAILABLE", partial: true, evaluation: null, available: [] } }); continue; }
    const query = { context: { variant: "standard", fen: replay.fens[ply], root_fen: replay.root_fen, moves: replay.moves.slice(0, ply), root_history_complete: replay.root_history_complete }, allow_advisory: true, allow_partial: true, allow_cloud: cloud, ...(cloud ? { source: "lichess_cloud", cloud_multipv: 3, prefer_cloud_pvs: true } : {}) };
    if (new TextEncoder().encode(JSON.stringify({ items: [...batch.map(r => r.query), query] })).length > 96 * 1024) await flush();
    batch.push({ ply, query });
  }
  await flush();
  return rows.sort((a, b) => a.ply - b.ply);
}

export function cloudStatusExplanation(status?: { cloud_enabled: boolean; cloud_resolution_allowed?: boolean; read_only?: boolean; cloud_operator_key_configured?: boolean }) {
  if (!status) return "Cloud availability could not be established; no provider request will be made.";
  if (!status.cloud_enabled) return "Cloud access is disabled on the API server. It is never enabled by a page load.";
  if (status.read_only) return "The API is in read-only mode: cached reads are allowed, but cloud resolution and cache writes are refused.";
  if (status.cloud_operator_key_configured === false) return "Cloud is enabled, but the server operator key is missing or shorter than 16 characters. Resolution is unavailable until the operator configures it.";
  if (status.cloud_resolution_allowed === false) return "Cloud resolution is unavailable under the server's current authorization policy.";
  return "Cloud requires an explicit request and the matching operator key. Results remain advisory; availability and three lines are not guaranteed.";
}
