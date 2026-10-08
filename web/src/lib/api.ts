/**
 * Typed fetch wrappers for the FastAPI chess coach API.
 * All functions are read-only (GET requests only).
 */

import type {
  GamesListResponse,
  Game,
  GameAnalysis,
  MovesResponse,
  CriticalResponse,
  GameOpening,
  Episode,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ---- Games ----

export interface ListGamesParams {
  player?: string;
  result?: string;
  analysis_status?: string;
  limit?: number;
  offset?: number;
}

export async function listGames(
  params: ListGamesParams = {}
): Promise<GamesListResponse> {
  const query = new URLSearchParams();
  if (params.player) query.set("player", params.player);
  if (params.result) query.set("result", params.result);
  if (params.analysis_status)
    query.set("analysis_status", params.analysis_status);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));

  const qs = query.toString();
  return fetchJSON<GamesListResponse>(`/api/games${qs ? `?${qs}` : ""}`);
}

export async function getGame(gameId: string): Promise<Game> {
  return fetchJSON<Game>(`/api/games/${gameId}`);
}

// ---- Analysis ----

export async function getAnalysis(gameId: string): Promise<GameAnalysis> {
  return fetchJSON<GameAnalysis>(`/api/games/${gameId}/analysis`);
}

export async function getMoves(
  gameId: string,
  opts: {
    color?: string;
    critical_only?: boolean;
    category?: string;
  } = {}
): Promise<MovesResponse> {
  const query = new URLSearchParams();
  if (opts.color) query.set("color", opts.color);
  if (opts.critical_only) query.set("critical_only", "true");
  if (opts.category) query.set("category", opts.category);

  const qs = query.toString();
  return fetchJSON<MovesResponse>(
    `/api/games/${gameId}/moves${qs ? `?${qs}` : ""}`
  );
}

export async function getCritical(gameId: string): Promise<CriticalResponse> {
  return fetchJSON<CriticalResponse>(`/api/games/${gameId}/critical`);
}

export async function getEpisodes(
  gameId: string
): Promise<{ game_id: string; episodes: Episode[] }> {
  return fetchJSON<{ game_id: string; episodes: Episode[] }>(
    `/api/games/${gameId}/episodes`
  );
}

// ---- Opening ----

export async function getOpening(gameId: string): Promise<GameOpening> {
  return fetchJSON<GameOpening>(`/api/games/${gameId}/opening`);
}
