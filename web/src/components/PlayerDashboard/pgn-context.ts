import { Chess, DEFAULT_POSITION } from "chess.js";
export type Replay = { root_fen: string; root_history_complete: boolean; fens: string[]; moves: string[]; sans: string[] };
// Executed in a disposable worker by the browser. Never silently truncate history.
export function replayPGN(pgn: string): Replay {
  if (new TextEncoder().encode(pgn).length > 2 * 1024 * 1024) throw new Error("PGN exceeds the 2 MiB browser limit");
  const chess = new Chess(); chess.loadPgn(pgn);
  const variant = chess.getHeaders().Variant?.toLowerCase();
  if (variant && !["standard", "chess"].includes(variant)) throw new Error("Only standard chess PGNs are supported");
  const history = chess.history({ verbose: true });
  if (history.length > 10000) throw new Error("PGN exceeds the 10,000-ply browser limit");
  const root = history[0]?.before || chess.fen();
  return { root_fen: root, root_history_complete: root === DEFAULT_POSITION,
    fens: [root, ...history.map(m => m.after)], moves: history.map(m => m.from + m.to + (m.promotion || "")), sans: history.map(m => m.san) };
}
export function selectedPly(value: number, count: number) { return Number.isInteger(value) && value >= 0 && value <= count ? value : 0; }
export function lookupContext(replay: Replay, ply: number) {
  if (ply > 1000) throw new Error("Local lookup supports at most 1,000 plies of history");
  if (selectedPly(ply, replay.moves.length) !== ply) throw new Error("Invalid ply");
  return { variant: "standard", fen: replay.fens[ply], root_fen: replay.root_fen, moves: replay.moves.slice(0, ply), root_history_complete: replay.root_history_complete };
}
