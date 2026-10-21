"use client";
import { useState } from "react";
import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
export default function PuzzleBoard({ fen, orientation, disabled, submit }: { fen: string; orientation: "white" | "black"; disabled: boolean; submit: (uci: string) => void }) {
  const [square, setSquare] = useState<string | null>(null);
  const [promotion, setPromotion] = useState<{ from: string; to: string } | null>(null);
  function choose(from: string, to: string) {
    if (disabled) return;
    const board = new Chess(fen);
    const pawn = board.get(from as Parameters<Chess["get"]>[0]);
    if (pawn?.type === "p" && (to[1] === "8" || to[1] === "1")) { setPromotion({ from, to }); return; }
    submit(from + to); setSquare(null);
  }
  return <div><Chessboard options={{ position: fen, boardOrientation: orientation, allowDragging: !disabled, onPieceDrop: ({ sourceSquare, targetSquare }) => { if (targetSquare) choose(sourceSquare, targetSquare); return false; }, onSquareClick: ({ square: target }) => { if (disabled) return; if (square === target) setSquare(null); else if (square) { choose(square, target); setSquare(null); } else setSquare(target); }, squareStyles: square ? { [square]: { backgroundColor: "rgba(234,179,8,.45)" } } : {}, boardStyle: { borderRadius: "8px" } }} />
    {promotion && <fieldset><legend>Choose promotion</legend>{["q", "r", "b", "n"].map(piece => <button type="button" key={piece} disabled={disabled} onClick={() => { submit(promotion.from + promotion.to + piece); setPromotion(null); }}>{({ q: "Queen", r: "Rook", b: "Bishop", n: "Knight" } as Record<string, string>)[piece]}</button>)}<button type="button" onClick={() => setPromotion(null)}>Cancel</button></fieldset>}
  </div>;
}
