export type TypedScore = { kind: "cp" | "mate" | "terminal_checkmate"; cp: number | null; mate: number | null; winner: string | null };
export type ReasoningStep = { ply: number; mover: string; san: string; events: { from: string; to: string; capture: { piece: string; square: string } | null; promotion: string | null; kind: string }[]; check: boolean; terminal: string | null; provenance: string };
export type ReasoningLine = { steps: ReasoningStep[]; id: string; candidate_uci: string; moves: string[]; sans: string[]; fens: string[]; full_validated_plies: number; display_truncated: boolean; record_id: string | null; opponent_response: { uci: string; san: string; scope: string } | null; origin: string };
export type Reasoning = {
  schema_version: string; status: string; source: { source_freshness: string; root_ply: number; checked_at: string };
  root: { fen: string; side_to_move: string; alternative_uci: string | null; legal_move_count: number | null };
  evidence: { status: string; origin: string | null; interpretation?: string; reason?: string; context_compatibility: string; profiles?: { id: string; settings: Record<string, unknown> }[]; retained_local?: Record<string, unknown> };
  candidates: { uci: string; san: string; origin: string; score: TypedScore | null; quality: string; achieved_depth?: number; requested_depth?: number | null; requested_depth_met?: boolean; record_id: string | null; terminal_outcome?: { kind: string; winner: string | null; termination: string } | null }[];
  comparison: { status: string; method?: string; reason?: string; preferred_recorded_uci?: string | null; score_relation?: string; cp_differences?: { a: string; b: string; for_root_mover: number; recorded_scores_close: boolean }[]; available_profiles?: string[]; compared_moves?: string[] };
  lines: ReasoningLine[]; checkpoints: { id: string; fen: string; features: Record<string, unknown>; events?: { san?: string; uci?: string; from: string; to: string }[]; changed_features?: { feature: string; color: string | null; attribution: string }[]; findings: { motif: string; provenance: string; squares: string[]; facts: Record<string, unknown> }[]; coverage: Record<string, unknown> }[];
  explanations: { claim_id: string; class: string; text: string }[]; claims: { id: string; references: string[] }[];
  coverage: { partial: boolean; child_pushes: number; candidates_returned?: number; evaluated_candidates?: number }; limitations: string[];
};
export type Availability = { status: string; bundles: { id: string; origin: string; profiles: string[] }[] };
export function reasoningKey(game: string, pgn: string, ply: number, alternative: string, bundle: string, profile: string, disabled: boolean) {
  return JSON.stringify([game, pgn, ply, alternative, bundle, profile, disabled]);
}
export function currentReasoningView(viewKey: string, selectionKey: string, aborted: boolean) {
  return viewKey === selectionKey && !aborted;
}
export function reasoningScore(score: TypedScore | null) {
  if (!score) return "Unavailable · not zero";
  if ((score.kind === "cp" && score.cp == null) || (score.kind === "mate" && score.mate == null)) return "Score unavailable";
  if (score.kind === "cp") return `${(score.cp ?? 0) >= 0 ? "+" : ""}${((score.cp ?? 0) / 100).toFixed(2)} pawns (White)`;
  if (score.kind === "mate") return `Reported mate ${score.mate} (White-relative)`;
  return `Terminal checkmate · ${score.winner} wins`;
}
export async function loadReasoning(game: string, pgn: string, ply: number, signal: AbortSignal, options: { availability?: boolean; alternative?: string; bundle?: string; profile?: string } = {}): Promise<Reasoning | Availability> {
  if (!Number.isInteger(ply) || ply < 0 || ply > 10000 || new TextEncoder().encode(pgn).length > 2 * 1024 * 1024) throw new Error("Position or PGN exceeds the reasoning bounds");
  if (options.alternative && !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(options.alternative)) throw new Error("Use legal coordinate notation, including promotion if needed (a7a8n)");
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(pgn))), n => n.toString(16).padStart(2, "0")).join("");
  if (signal.aborted) throw new DOMException("Selection changed", "AbortError");
  const query = new URLSearchParams({ ply: String(ply), expected_pgn_sha256: hash });
  if (options.alternative) query.set("alternative_uci", options.alternative);
  if (options.bundle) query.set("bundle_id", options.bundle);
  if (options.profile) query.set("profile_id", options.profile);
  const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const response = await fetch(`${base}/api/games/${encodeURIComponent(game)}/reasoning${options.availability ? "/availability" : ""}?${query}`, { cache: "no-store", signal });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Reasoning unavailable (${response.status})`);
  return data;
}
