import { readDashboard } from "./player-dashboard";
export type UnderstandingFinding = { id: string; motif: string; stage: string; provenance: string; squares: string[]; facts: Record<string, unknown>; limitations: string[] };
type ColorFeatures = { king_safety: { king_square: string; geometrically_attacked_ring: string[] }; pawn_structure: { isolated: string[]; passed_by_pawn_geometry: string[]; doubled_files: string[] }; files: { open: string[]; semi_open: string[] }; development: { status: string }; space: { count: number; denominator: number }; [key: string]: unknown };
export type Understanding = {
  schema_version: string; generated_at: string; source: { exact_pgn_sha256: string; source_freshness: string };
  positions: { before: { fen: string } | null; after: { fen: string } | null };
  features: { after: { colors: Record<string, ColorFeatures>; material: unknown } | null; before: unknown };
  findings: UnderstandingFinding[]; explanations: { template_id: string; what_changed: string; why_examine: string; examine_next: string; provenance: string; supports: string[] }[];
  changes: { created: string[]; removed: string[]; persisted: string[]; changed: { color: string | null; feature: string; attribution: string }[]; events: { kind: string; san?: string; from: string; to: string; capture?: { piece: string; square: string } | null; promotion?: string | null }[] };
  coverage: { partial: boolean; budget_exhausted: boolean; reason?: string; omitted_findings: number; detectors: Record<string, Record<string, { status: string; [key: string]: unknown }>> };
  engine_evidence: { requested: boolean; status: string; score_change_cp_white?: number; score_change_limitations?: string; contexts?: { stage: string; context_compatibility: string; understanding_admission: string; available_configs?: string[]; rejected_reasons?: string[] }[]; accepted: { stage: string; work_id: string; config_hash: string; understanding_admission: string; evaluation: { achieved_depth: number; config: { depth: number | null; time_seconds: number | null; source: Record<string, unknown>; applied_options: Record<string, unknown> }; pvs: { kind: string; cp: number | null; mate: number | null; winner: string | null; moves: string[]; available_plies: number }[] } }[] };
  limitations: string[];
};
export function understandingKey(gameID: string, pgn: string, ply: number, disabled: boolean) {
  return JSON.stringify([gameID, pgn, ply, disabled]);
}
export function typedScore(pv: { kind: string; cp: number | null; mate: number | null; winner: string | null }) {
  if (pv.kind === "cp" && pv.cp !== null) return `${pv.cp >= 0 ? "+" : ""}${(pv.cp / 100).toFixed(2)} pawns (White)`;
  if (pv.kind === "mate" && pv.mate !== null) return `Mate ${pv.mate > 0 ? "+" : ""}${pv.mate} (White perspective)`;
  if (pv.kind === "terminal_checkmate") return `Checkmate · ${pv.winner} wins`;
  return "Score unavailable";
}
export async function loadUnderstanding(gameID: string, pgn: string, ply: number, signal: AbortSignal, evidence = false, config = "") {
  const bytes = new TextEncoder().encode(pgn);
  if (bytes.length > 2 * 1024 * 1024) throw new Error("PGN exceeds the 2 MiB understanding limit");
  if (!Number.isInteger(ply) || ply < 0 || ply > 10000) throw new Error("Invalid selected ply");
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  if (signal.aborted) throw new DOMException("Selection changed", "AbortError");
  const sha = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("");
  const query = new URLSearchParams({ ply: String(ply), expected_pgn_sha256: sha, evidence: evidence ? "accepted_local" : "none" });
  if (config) query.set("config_hash", config);
  return readDashboard<Understanding>(`/api/games/${encodeURIComponent(gameID)}/understanding?${query}`, signal);
}
