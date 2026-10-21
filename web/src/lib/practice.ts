export type Question = { id: string; tier: string; objective: string; prompt: string; fen: string; root_fen: string; moves: string[]; learner_color: "white" | "black"; history_complete: boolean; source: { game_id: string; ply: number; freshness: string }; limitations: string[] };
export type Feedback = { code: string; message: string; fen?: string; level?: number; solution?: { san: string; uci: string; theme: string; explanation: string; provenance: string; limitations: string[] } };
export type Attempt = { id: string; puzzle_id: string; revision: number; state: string; assisted: boolean; hints: number; feedback: Feedback | null; created_at?: number };
export type Session = { question: Question; attempt: Attempt; events?: { attempt: Attempt }[] };
const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export async function practiceRead<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${base}/api/practice${path}`, { cache: "no-store", signal });
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || `Request failed (${response.status})`); }
  return response.json();
}
export async function practiceWrite<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${base}/api/practice${path}`, { method: "POST", cache: "no-store", signal, headers: { "Content-Type": "application/json", "X-Practice-Action": "local-practice" }, body: JSON.stringify(body) });
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || `Action failed (${response.status})`); }
  return response.json();
}
export async function pgnHash(pgn: string) {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(pgn))), n => n.toString(16).padStart(2, "0")).join("");
}
export function feedbackLabel(code: string) {
  return ({ OBJECTIVE_MET: "Checkmate objective met", OBJECTIVE_NOT_MET: "Not mate in one", DEMONSTRATED_TARGET: "Guided idea demonstrated", LEGAL_ALTERNATIVE_UNASSESSED: "Legal alternative · unassessed", ILLEGAL_MOVE: "Illegal move", HINT: "Assisted practice", REVEALED: "Witness revealed", ABANDONED: "Attempt closed" } as Record<string, string>)[code] || code;
}
