/**
 * TypeScript types mirroring the Python backend data structures.
 * These represent the shapes returned by the FastAPI read-only endpoints.
 */

// ---- Game Library ----

export interface GameOpening {
  eco: string | null;
  opening_name: string | null;
  variation_name: string | null;
  full_name: string | null;
  matched_ply: number | null;
  theory_exit_ply: number | null;
  confidence: string | null;
}

export interface Game {
  game_id: string;
  fingerprint: string;
  source: string;
  source_game_id: string | null;
  white: string;
  black: string;
  result: string;
  date: string | null;
  event: string | null;
  site: string | null;
  round: string | null;
  time_control: string | null;
  white_rating: number | null;
  black_rating: number | null;
  eco: string | null;
  ply_count: number;
  pgn: string;
  analysis_status: string;
  analysis_reference: string | null;
  imported_at: string;
  analyzed_at: string | null;
  opening: GameOpening | null;
}

export interface GamesListResponse {
  total: number;
  offset: number;
  limit: number;
  games: Game[];
}

// ---- Move Analysis ----

export interface Explanation {
  summary: string;
  what_happened: string;
  why_it_matters: string;
  better_move: string | null;
  variation: string | null;
  motif: string | null;
  confidence: string;
  limitations: string | null;
}

export interface TacticalFinding {
  category: string;
  confidence: string;
  evidence: Record<string, unknown>;
  subcategories?: string[];
}

export interface Candidate {
  move: string;
  evaluation: number;
  principal_variation: string[];
}

export interface PositionFeatures {
  fen: string;
  side_to_move: string;
  material: { white: number; black: number; difference: number };
  piece_counts: Record<string, Record<string, number>>;
  checks_available: string[];
  captures_available: string[];
  in_check: boolean;
  is_checkmate: boolean;
  legal_moves: number;
}

export interface MoveData {
  ply: number;
  move_number: number;
  color: "White" | "Black";
  played_move: string;
  played_move_uci: string;
  best_move: string;
  best_move_uci: string;
  is_forced: boolean;
  evaluation_before: number;
  evaluation_after: number;
  raw_centipawn_loss: number;
  centipawn_loss: number;
  classification: string;
  tactical_finding: TacticalFinding | null;
  explanation: Explanation | null;
  is_critical: boolean;
  episode_index: number | null;
  fen_before: string;
  fen_after: string;
  clock_seconds: number | null;
  principal_variation: string[];
  candidates: Candidate[];
  position_features: PositionFeatures | null;
}

// ---- Episodes ----

export interface EpisodeMove {
  move_number: number;
  color: string;
  ply?: number;
  played_move?: string;
  centipawn_loss?: number;
  classification?: string;
  tactical_finding?: TacticalFinding | null;
  explanation?: Explanation | null;
}

export interface Episode {
  episode_index: number;
  start_ply?: number;
  end_ply?: number;
  category?: string;
  move_count?: number;
  moves: EpisodeMove[];
}

// ---- Analysis ----

export interface AnalysisSummary {
  total_plies: number;
  total_full_moves: number;
  critical_positions_count: number;
  critical_episodes_count: number;
  tactical_motifs_count: Record<string, number>;
}

export interface GameAnalysis {
  schema_version: string;
  metadata: Record<string, string>;
  summary: AnalysisSummary;
  moves: MoveData[];
  episodes: Episode[];
}

// ---- Critical endpoint ----

export interface EpisodeGroup {
  episode_index: number;
  moves: MoveData[];
}

export interface CriticalResponse {
  game_id: string;
  critical_count: number;
  episode_groups: EpisodeGroup[];
  standalone_critical: MoveData[];
}

// ---- Moves endpoint ----

export interface MovesResponse {
  game_id: string;
  total: number;
  moves: MoveData[];
}

// ---- Classification helpers ----

export type MoveClassification =
  | "best"
  | "excellent"
  | "good"
  | "inaccuracy"
  | "mistake"
  | "blunder"
  | "book"
  | "forced";

export function classificationColor(cls: string): string {
  switch (cls) {
    case "best":
    case "excellent":
      return "#22c55e"; // green
    case "good":
    case "book":
      return "#6b7280"; // gray
    case "inaccuracy":
      return "#eab308"; // yellow
    case "mistake":
      return "#f97316"; // orange
    case "blunder":
      return "#ef4444"; // red
    case "forced":
      return "#8b5cf6"; // purple
    default:
      return "#6b7280";
  }
}

export function classificationLabel(cls: string): string {
  return cls.charAt(0).toUpperCase() + cls.slice(1);
}
