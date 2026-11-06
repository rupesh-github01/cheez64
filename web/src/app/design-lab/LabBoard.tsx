"use client";
import { Chessboard } from "react-chessboard";

export default function LabBoard({ fen, lastMove, flipped }: { fen: string; lastMove?: { from: string; to: string }; flipped: boolean }) {
  return <Chessboard options={{
    id: "crimson-design-board", position: fen, boardOrientation: flipped ? "black" : "white", allowDragging: false,
    darkSquareStyle: { backgroundColor: "#726766" }, lightSquareStyle: { backgroundColor: "#d5cfc3" },
    squareStyles: lastMove ? { [lastMove.from]: { backgroundColor: "#ad7d68" }, [lastMove.to]: { backgroundColor: "#c09677" } } : {},
    boardStyle: { borderRadius: "2px" }, animationDurationInMs: 0,
  }} />;
}
