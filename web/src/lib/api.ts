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
  DashboardData,
  CoachStatusResponse,
  CoachChatResponse,
  CoachMessage,
  ManagementSummaryResponse,
  ImportJob,
  ImportsListResponse,
  StartImportParams,
  AnalysisQueueResponse,
  AnalysisJob,
  WorkerStatusResponse,
  QueueAnalysisParams,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = "";
    try {
      const errJson = await res.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail || `API ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function fetchMutate<T>(path: string, body?: unknown, method: string = "POST"): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const errJson = await res.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail || `API ${res.status}`);
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

export async function getMove(
  gameId: string,
  ply: number
): Promise<{ game_id: string; ply: number; move: import("./types").MoveData }> {
  return fetchJSON<{
    game_id: string;
    ply: number;
    move: import("./types").MoveData;
  }>(`/api/games/${gameId}/moves/${ply}`);
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

// ---- Dashboard ----

export async function getDashboard(player?: string): Promise<DashboardData> {
  const query = new URLSearchParams();
  if (player) query.set("player", player);
  const qs = query.toString();
  return fetchJSON<DashboardData>(`/api/dashboard${qs ? `?${qs}` : ""}`);
}

// ---- Coach ----

export interface AskCoachParams {
  question: string;
  game_id?: string;
  ply?: number;
  hint_level?: number;
  messages?: CoachMessage[];
  player?: string;
}

export async function getCoachStatus(): Promise<CoachStatusResponse> {
  return fetchJSON<CoachStatusResponse>("/api/coach/status");
}

export async function askCoach(params: AskCoachParams): Promise<CoachChatResponse> {
  const query = new URLSearchParams();
  query.set("question", params.question);
  if (params.game_id) query.set("game_id", params.game_id);
  if (params.ply !== undefined && params.ply !== null) query.set("ply", String(params.ply));
  if (params.hint_level) query.set("hint_level", String(params.hint_level));
  if (params.messages && params.messages.length > 0) {
    query.set("messages", JSON.stringify(params.messages));
  }
  if (params.player) query.set("player", params.player);

  return fetchJSON<CoachChatResponse>(`/api/coach/chat?${query.toString()}`);
}

// ---- Management API ----

export async function getManagementSummary(): Promise<ManagementSummaryResponse> {
  return fetchJSON<ManagementSummaryResponse>("/api/management/summary");
}

export async function startImport(params: StartImportParams): Promise<{ message: string; job: ImportJob }> {
  return fetchMutate<{ message: string; job: ImportJob }>("/api/management/imports", params);
}

export async function listImports(params: { status?: string; limit?: number; offset?: number } = {}): Promise<ImportsListResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  const qs = query.toString();
  return fetchJSON<ImportsListResponse>(`/api/management/imports${qs ? `?${qs}` : ""}`);
}

export async function getImport(jobId: string): Promise<{ job: ImportJob }> {
  return fetchJSON<{ job: ImportJob }>(`/api/management/imports/${jobId}`);
}

export async function resumeImport(jobId: string): Promise<{ message: string; job: ImportJob }> {
  return fetchMutate<{ message: string; job: ImportJob }>(`/api/management/imports/${jobId}/resume`);
}

export async function queueAnalysis(params: QueueAnalysisParams): Promise<{ message: string; queued_count: number; job_ids?: string[]; batch_id?: string }> {
  return fetchMutate<{ message: string; queued_count: number; job_ids?: string[]; batch_id?: string }>("/api/management/analysis/queue", params);
}

export async function listAnalysisQueue(params: { status?: string; limit?: number; offset?: number } = {}): Promise<AnalysisQueueResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  const qs = query.toString();
  return fetchJSON<AnalysisQueueResponse>(`/api/management/analysis/queue${qs ? `?${qs}` : ""}`);
}

export async function retryAnalysisJob(jobId: string): Promise<{ message: string; job: AnalysisJob }> {
  return fetchMutate<{ message: string; job: AnalysisJob }>(`/api/management/analysis/queue/${jobId}/retry`);
}

export async function cancelAnalysisJob(jobId: string): Promise<{ message: string; job: AnalysisJob }> {
  return fetchMutate<{ message: string; job: AnalysisJob }>(`/api/management/analysis/queue/${jobId}/cancel`);
}

export async function retryAllFailedAnalysisJobs(): Promise<{ message: string; retried_count: number }> {
  return fetchMutate<{ message: string; retried_count: number }>("/api/management/analysis/queue/retry-all");
}

export async function getWorkerStatus(): Promise<WorkerStatusResponse> {
  return fetchJSON<WorkerStatusResponse>("/api/management/workers");
}


