"use client";
import { useEffect, useState } from "react";

export type Metric = { value: number | null; numerator?: number | null; denominator?: number | null; unit: string; provenance: string; status: string; method: string; sample: { games: number; preliminary?: boolean }; coverage: { observed?: number; eligible?: number }; limitations?: string[] };
export type Outcomes = { total_games: number; completed_games: number; wins: number; draws: number; losses: number; unfinished: number; unknown: number; win_rate: Metric; score_rate: Metric };
export type Identity = { source: string; username: string; games: number; matching: string };
export type GameRow = { game_id: string; white: string; black: string; result: string; date: string | null; source: string; time_control: string | null; white_rating: number | null; black_rating: number | null; analysis_status: string; player_color: string; legacy_analysis_available: boolean; eco: string | null; first_ply: number | null };
export type GamesPage = { position_coverage?: { total_games: number; complete_games: number; partial_index: boolean; absence_is_conclusive: boolean } | null; items: GameRow[]; selected_total: number; matching_total: number; generated_at: string; next_cursor: string | null; legacy_analysis_counts: { stored_analysis: number; by_status: Record<string, number> } };
export type Position = { position_id: number; canonical_fen: string; distinct_game_count: number; occurrence_count: number; first_ply: number };
export type Profile = {
  generated_at: string; selector: { source: string; username: string; matching: string };
  completeness: { eligible_games: number; processed_games: number; replay_complete: boolean | null; budget_exhausted: boolean };
  coverage: { ambiguous_identity_games: number; replayed_games: number; replay_failed_games: number; unsupported_games: number; clock_moves: number | null; emt_moves: number | null; metadata: Record<string, number> };
  overview?: { outcomes: Outcomes; by_color: Record<string, Outcomes>; ratings_by_time_control: { time_control: string; median: number; observations: number; preliminary: boolean }[]; time_controls: { time_control: string; total_games: number }[] };
  trends?: { monthly: (Outcomes & { month: string })[]; monthly_bucket_count: number; rating_monthly: { time_control: string; month: string; median: number; observations: number }[]; recent_calendar: Outcomes & { from: string; through: string; timezone: string }; limitations: string[] };
  repertoire?: { openings: (Outcomes & { color: string; name: string; variation: string | null; eco: string | null; confidence: string; frequency_by_color: Metric; evidence_game_ids: string[] })[]; opening_bucket_count: number; recognized_games: number; classification_provenance: string };
  move_tendencies?: { event_frequencies: Record<string, Metric>; castled_game_fraction: Metric; development_at_player_move10: Metric; queen_before_move6: Metric; repeated_piece_opening_moves: Metric; mean_recorded_game_length_plies: Metric; phase_distribution: Record<string, Metric> };
  time_management?: { clock_convention: string; games_with_durations: number; usable_duration_moves: number; duration_sources: Record<string, { mean_seconds: Metric; median_seconds: Metric; observations: number }>; remaining_clock_checkpoints: Record<string, { mean_seconds: Metric; observations: number }>; limitations: string[] };
  positions?: { coverage: { total_games: number; indexed_games: number; partial_index: boolean; schema_available: boolean }; frequent_reached: Position[]; player_decisions: Position[]; continuations: { position_id: number; outgoing_occurrences: number; items: { uci: string; occurrence_count: number; distinct_game_count: number; move_frequency: Metric; outcomes: Outcomes }[] }[]; window: { min_ply: number; max_ply: number } };
  engine_analysis_availability: { games_with_stored_analysis: number }; limitations: string[];
};

const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const cache = new Map<string, { value: unknown; expires: number; bytes: number; timer?: ReturnType<typeof setTimeout> }>();
const pending = new Map<string, Promise<unknown>>();
let replayTail: Promise<unknown> = Promise.resolve();
function drop(key: string) { const entry = cache.get(key); if (entry?.timer) clearTimeout(entry.timer); cache.delete(key); }
export function clearDashboardCache() { for (const key of cache.keys()) drop(key); }
export function readDashboard<T>(path: string, signal?: AbortSignal, heavy = false, serializedBody?: string): Promise<T> {
  const key = path + (serializedBody || "");
  const retain = !path.startsWith("/api/progressive-analysis/") && !path.startsWith("/api/games/") && path !== "/api/evaluations/lookup-batch";
  const found = retain ? cache.get(key) : undefined;
  if (found && found.expires > Date.now()) { cache.delete(key); cache.set(key, found); return Promise.resolve(found.value as T); }
  if (pending.has(key)) return (pending.get(key) as Promise<T>).catch(error => {
    if (error instanceof DOMException && error.name === "AbortError" && !signal?.aborted) return readDashboard<T>(path, signal, heavy, serializedBody);
    throw error;
  });
  const execute = async () => {
    if (signal?.aborted) throw new DOMException("Request superseded", "AbortError");
    const response = await fetch(base + path, { cache: "no-store", signal: heavy ? undefined : signal,
      ...(serializedBody ? { method: "POST", headers: { "Content-Type": "application/json" }, body: serializedBody } : {}) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
    const bytes = new TextEncoder().encode(JSON.stringify(data)).length;
    if (retain && bytes <= 2 * 1024 * 1024) {
      drop(key);
      const entry: { value: unknown; expires: number; bytes: number; timer?: ReturnType<typeof setTimeout> } = { value: data, expires: Date.now() + (heavy ? 60000 : 15000), bytes };
      cache.set(key, entry);
      entry.timer = setTimeout(() => drop(key), 300000);
      entry.timer.unref?.();
      while (cache.size > 8 || [...cache.values()].reduce((n, item) => n + item.bytes, 0) > 2 * 1024 * 1024) drop(cache.keys().next().value!);
    }
    return data;
  };
  const request = heavy ? replayTail.catch(() => undefined).then(execute) : execute();
  if (heavy) replayTail = request.catch(() => undefined);
  pending.set(key, request);
  request.then(() => pending.delete(key), () => pending.delete(key));
  return request as Promise<T>;
}
export function useDashboardRead<T>(path: string | null, heavy = false, revision = 0, body?: unknown) {
  const serializedBody = body === undefined ? undefined : JSON.stringify(body);
  const key = `${path}|${serializedBody}|${revision}`;
  const [value, setValue] = useState<{ key: string; data?: T; error?: string }>();
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    readDashboard<T>(path, controller.signal, heavy, serializedBody).then(data => {
      if (!controller.signal.aborted) setValue({ key, data });
    }).catch(error => { if (!controller.signal.aborted) setValue({ key, error: error instanceof Error ? error.message : "Request failed" }); });
    return () => controller.abort();
  }, [path, key, heavy, serializedBody]);
  return { data: value?.key === key ? value.data : undefined, error: value?.key === key ? value.error : undefined,
    loading: path !== null && (value?.key !== key) };
}
export function metricText(metric?: Metric, digits = 1) {
  if (!metric || metric.value === null || !Number.isFinite(metric.value)) return "Not enough data";
  return metric.unit === "fraction" ? `${(metric.value * 100).toFixed(digits)}%` : `${metric.value.toFixed(digits)}${metric.unit === "seconds" ? " s" : metric.unit === "plies" ? " plies" : ""}`;
}
