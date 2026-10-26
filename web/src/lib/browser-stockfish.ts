import { Chess } from "chess.js";
import type { Replay } from "@/components/PlayerDashboard/pgn-context";
import type { Score } from "./position-evaluation";
export const ENGINE_URL = "/engines/stockfish-19.0.1/stockfish-19-lite-single.js";
export const ENGINE_IDENTITY = {
  package: "stockfish@19.0.1", build: "lite-single", license: "GPL-3.0",
  js_sha256: "ccb130d7ff29408531c47d859a49f91d59d4f482d05a53eb1a3231a4f624c91a",
  wasm_sha256: "ee4f115bdb2d32cadf96cada104a05b5ec257d7ad6f57582190b49bc71f096de",
  nnue: "nn-61e7af4bb97d.nnue (embedded)", initial_linear_bytes: 134217728, maximum_linear_bytes: 2147483648,
};
export type Bound = "exact" | "lower" | "upper";
export type LiveLine = { rank: number; depth: number; score: Score; bound: Bound; moves: string[]; sans: string[]; nodes?: number; time?: number };
export type UciOption = { name: string; type: string; min?: number; max?: number; default?: string };
export type LivePosition = { key: string; root: string; moves: string[]; fen: string; board: Chess; repetitions: Map<string, number>; terminal?: string; terminalScore?: Score; legalCount: number };
const boardKey = (b: Chess) => b.fen().split(" ").slice(0, 4).join(" ");
function terminal(b: Chess, count: number): { terminal?: string; terminalScore?: Score } {
  if (b.isCheckmate()) return { terminal: "Checkmate", terminalScore: { kind: "terminal_checkmate", mate: 0, winner: b.turn() === "w" ? "black" : "white" } };
  const reason = b.isStalemate() ? "Stalemate" : b.isInsufficientMaterial() ? "Insufficient material" : Number(b.fen().split(" ")[4]) >= 150 ? "Automatic 75-move draw" : count >= 5 ? "Automatic fivefold repetition draw" : undefined;
  return reason ? { terminal: reason, terminalScore: { kind: "cp", cp: 0 } } : {};
}
export function livePosition(replay: Replay, ply: number, key: string): LivePosition {
  if (!Number.isInteger(ply) || ply < 0 || ply > 1000 || ply >= replay.fens.length) throw new Error("Live analysis requires a complete legal prefix of at most 1,000 plies.");
  const board = new Chess(replay.root_fen); const repetitions = new Map([[boardKey(board), 1]]);
  const moves = replay.moves.slice(0, ply);
  for (const uci of moves) {
    if (terminal(board, repetitions.get(boardKey(board)) || 0).terminal) throw new Error("Game history continues after an automatic terminal position.");
    if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(uci)) throw new Error("Invalid history move.");
    board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined });
    const k = boardKey(board); repetitions.set(k, (repetitions.get(k) || 0) + 1);
  }
  if (board.fen() !== replay.fens[ply]) throw new Error("Selected position does not match its full history.");
  return { key, root: replay.root_fen, moves, fen: board.fen(), board, repetitions, legalCount: board.moves().length, ...terminal(board, repetitions.get(boardKey(board)) || 0) };
}
export function positionCommand(position: LivePosition) {
  return `position fen ${position.root}${position.moves.length ? ` moves ${position.moves.join(" ")}` : ""}`;
}
export type ParsedInfo = { rank: number; depth: number; kind: "cp" | "mate"; value: number; bound: Bound; moves: string[]; nodes?: number; time?: number };
export function parseInfo(line: string): ParsedInfo | undefined {
  if (line.length > 8192 || !line.startsWith("info ") || line.startsWith("info string ")) return;
  const tokens = line.trim().split(/\s+/); const pvAt = tokens.indexOf("pv"); if (pvAt < 0) return;
  const fields = tokens.slice(0, pvAt); const moves = tokens.slice(pvAt + 1);
  const number = (name: string) => { const i = fields.indexOf(name); return i >= 0 && /^-?\d+$/.test(fields[i + 1] || "") ? Number(fields[i + 1]) : undefined; };
  const depth = number("depth"), rank = number("multipv") ?? 1, at = fields.indexOf("score");
  if (!depth || depth < 1 || depth > 14 || (fields.includes("multipv") && number("multipv") === undefined) || rank < 1 || rank > 3 || fields.filter(x => x === "score").length !== 1 || fields.filter(x => x === "depth").length !== 1 || fields.filter(x => x === "multipv").length > 1) return;
  const kind = fields[at + 1], raw = fields[at + 2];
  if (!["cp", "mate"].includes(kind) || !/^-?\d+$/.test(raw || "") || !moves.length || moves.length > 64 || moves.some(m => !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(m))) return;
  const value = Number(raw); if (!Number.isSafeInteger(value) || Math.abs(value) > 10000000 || (kind === "mate" && (!value || Math.abs(value) > 512))) return;
  if (fields.includes("lowerbound") && fields.includes("upperbound")) return;
  const nodes = number("nodes"), time = number("time");
  if ([nodes, time].some(v => v !== undefined && (!Number.isSafeInteger(v) || v < 0))) return;
  return { rank, depth, kind: kind as "cp" | "mate", value, moves, bound: fields.includes("lowerbound") ? "lower" : fields.includes("upperbound") ? "upper" : "exact", nodes, time };
}
export function parseOption(line: string): UciOption | undefined {
  if (line.length > 1024) return;
  const match = /^option name (.{1,80}?) type (spin|check|button|string|combo)(?: (.*))?$/.exec(line); if (!match) return;
  const tail = match[3] || "";
  const integer = (name: string) => { const m = new RegExp(`(?:^| )${name} (-?\\d+)(?: |$)`).exec(tail); return m ? Number(m[1]) : undefined; };
  return { name: match[1], type: match[2], min: integer("min"), max: integer("max"), default: /(?:^| )default (.*?)(?: min | max | var |$)/.exec(tail)?.[1] };
}
export function projectLiveLine(position: LivePosition, info: ParsedInfo): LiveLine {
  const b = position.board, repetitions = new Map(position.repetitions); let pushed = 0; const sans: string[] = [];
  const sign = b.turn() === "w" ? 1 : -1;
  const score: Score = info.kind === "cp" ? { kind: "cp", cp: sign * info.value } : { kind: "mate", mate: sign * info.value };
  const bound = sign === 1 || info.bound === "exact" ? info.bound : info.bound === "lower" ? "upper" : "lower";
  try {
    for (const uci of info.moves) {
      if (terminal(b, repetitions.get(boardKey(b)) || 0).terminal) throw new Error("PV continues after a terminal position");
      const move = b.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined }); pushed++; sans.push(move.san);
      const k = boardKey(b); repetitions.set(k, (repetitions.get(k) || 0) + 1);
    }
    if (b.isCheckmate() && (score.kind !== "mate" || (b.turn() === "b") !== (score.mate > 0))) throw new Error("Reported score conflicts with the mating PV");
    return { ...info, score, bound, sans };
  } finally { while (pushed-- > 0) b.undo(); }
}
export class DepthCohort {
  depth = 0; private ranks = new Map<number, LiveLine>();
  constructor(readonly expected: number) {}
  accept(line: LiveLine) {
    if (line.depth < this.depth || (line.depth > this.depth && line.rank !== 1)) return false;
    if (line.rank === 1) { this.depth = line.depth; this.ranks.clear(); }
    if (line.depth !== this.depth || line.rank > this.expected) return false;
    if ([...this.ranks.values()].some(r => r.rank !== line.rank && r.moves[0] === line.moves[0])) { this.ranks.delete(line.rank); return false; }
    this.ranks.set(line.rank, line); return true;
  }
  get lines() { return [...this.ranks.values()].sort((a, b) => a.rank - b.rank); }
  get complete() { return this.ranks.size === this.expected && this.lines.every((r, i) => r.rank === i + 1 && r.bound === "exact"); }
}
