/** Synthetic legal display fixture. No evaluations, player records or API access. */
export const HISTORY = 'e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O h3 Nb8'.split(' ');
export const FEN = 'rnbq1rk1/2p1bppp/p2p1n2/1p2p3/4P3/1BP2N1P/PP1P1PP1/RNBQR1K1 w - - 1 10';
export type PieceKind = 'p' | 'r' | 'n' | 'b' | 'q' | 'k';
export type DisplayPiece = { square: string; kind: PieceKind; color: 'w' | 'b' };
export const PIECES: DisplayPiece[] = FEN.split(' ')[0].split('/').flatMap((rank, i) => {
  let file = 0;
  return [...rank].flatMap(token => {
    if (/\d/.test(token)) { file += Number(token); return []; }
    const square = String.fromCharCode(97 + file++) + (8 - i);
    return [{ square, kind: token.toLowerCase() as PieceKind, color: token === token.toUpperCase() ? 'w' as const : 'b' as const }];
  });
});
export function squarePosition(square: string): [number, number, number] {
  return [square.charCodeAt(0) - 97 - 3.5, 0, 4.5 - Number(square[1])];
}
