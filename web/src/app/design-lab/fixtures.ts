import { Chess } from "chess.js";

// Entirely synthetic UI specimens. Never an evaluation/evidence source.
export const reviewMoves = "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O h3 Nb8 d4 Nbd7 Nbd2 Bb7 Bc2 Re8 Nf1 Bf8".split(" ");
export function replay(moves: string[]) {
  const game = new Chess();
  const positions = [game.fen()];
  const history = moves.map(san => {
    const move = game.move(san);
    positions.push(game.fen());
    return { san: move.san, from: move.from, to: move.to };
  });
  return { positions, history };
}
export const review = replay(reviewMoves);
export const variations = [
  { score: "+0.32", moves: ["d4", "exd4", "cxd4", "Bb7"], depth: 22 },
  { score: "+0.24", moves: ["d3", "Bb7", "Nbd2", "Re8"], depth: 22 },
  { score: "+0.18", moves: ["a4", "Bb7", "d3", "Re8"], depth: 22 },
];
export const studyLines = [
  { id: "central", title: "The central break", opening: "Ruy López · Closed variation", moves: [...reviewMoves.slice(0, 18), "d4", "exd4", "cxd4"], selected: 19, note: "The centre is a decision, not a destination.\n\nAfter 10. d4, White asks Black to clarify the tension. Before committing, trace the route of each knight and identify the squares that become available after an exchange.\n\nStudy prompt\nCompare …exd4 with keeping the tension. Which files open? Which pieces would you improve first? The point of this line is to build a habit of asking those questions, rather than memorising a verdict.\n\nFor the next session\nReturn to the position before d4. Write down two candidate moves, then replay both continuations on the board. A familiar structure is a starting point for thought, not a substitute for it." },
  { id: "quiet", title: "The quiet Italian", opening: "Italian Game · Giuoco Pianissimo", moves: "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O Re1 a6 Bb3 Ba7".split(" "), selected: 12, note: "A quieter centre, a different rhythm.\n\nBoth kings are now castled. Compare the pawn structure with the Spanish line. The c3 pawn supports a future d4, but that advance remains a choice, not a forced plan.\n\nAsk yourself\nWhich knight needs a better square? What changes if Black plays …a6? Record your own candidate before continuing the line." },
];
export const studyPositions = studyLines.map(line => replay(line.moves));
