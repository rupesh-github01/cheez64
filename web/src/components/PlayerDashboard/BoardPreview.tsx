"use client";
import { Chessboard } from "react-chessboard";
export default function BoardPreview({ fen }: { fen: string }) {
  const displayFen = fen.split(" ").length === 4 ? `${fen} 0 1` : fen;
  return <div className="pd-board"><Chessboard options={{ position: displayFen, allowDragging: false, darkSquareStyle: { backgroundColor: "#567784" }, lightSquareStyle: { backgroundColor: "#d7dedb" }, boardStyle: { borderRadius: "8px" } }} /></div>;
}
