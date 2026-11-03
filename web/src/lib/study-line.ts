import { Chess, DEFAULT_POSITION } from 'chess.js';
export { DEFAULT_POSITION };
export const MAX_STUDY_PLIES = 500;
export type StudyMove = { id?: string; ply_index: number; uci: string; san: string; fen_after: string; has_comment?: boolean; comment?: string };
export type StudySequence = { line_id: string; initial_fen: string; updated_at: string; moves: StudyMove[]; starting_note?: string; notes_enabled?: boolean };
export function terminal(board: Chess) {
  return board.isCheckmate() || board.isStalemate() || board.isInsufficientMaterial() || Number(board.fen().split(' ')[4]) >= 150;
}
export function replayLine(initial: string, ucis: string[]) {
  if (initial.trim().split(/\s+/).length !== 6) throw Error('Paste a complete six-field FEN.');
  if (ucis.length > MAX_STUDY_PLIES) throw Error(`A line supports at most ${MAX_STUDY_PLIES} moves (plies).`);
  const board = new Chess(initial), initial_fen = board.fen({ forceEnpassantSquare: true });
  const moves: StudyMove[] = [], counts = new Map<string, number>();
  const count = () => { const identity = board.fen().split(' ').slice(0, 4).join(' '); counts.set(identity, (counts.get(identity) || 0) + 1); return counts.get(identity)!; };
  let repeated = count();
  for (const uci of ucis) {
    if (terminal(board) || repeated >= 5) throw Error('No moves allowed after a terminal position.');
    if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(uci)) throw Error('Enter a legal move, with a promotion piece where required.');
    const move = board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
    // chess.js discards an uncapturable target internally even with forceEnpassantSquare.
    // FEN's full target field must match python-chess's en_passant='fen' serialization.
    const fields = board.fen({ forceEnpassantSquare: true }).split(' ');
    if (move.piece === 'p' && Math.abs(Number(move.to[1]) - Number(move.from[1])) === 2) fields[3] = move.from[0] + (move.color === 'w' ? '3' : '6');
    moves.push({ ply_index: moves.length + 1, uci, san: move.san, fen_after: fields.join(' ') });
    repeated = count();
  }
  return { initial_fen, moves, board };
}
export function moveLabel(initial: string, ply: number, san: string) {
  const fields = initial.split(' '), half = (fields[1] === 'b' ? 1 : 0) + ply - 1;
  return `${Number(fields[5]) + Math.floor(half / 2)}${half % 2 ? '…' : '.'} ${san}`;
}
// Exact next moves navigate; divergent moves are returned separately for explicit confirmation.
export function proposeMove(sequence: Pick<StudySequence, 'initial_fen' | 'moves'>, selected: number, input: string) {
  if (selected < 0 || selected > sequence.moves.length) throw Error('Invalid selected position.');
  const prefix = sequence.moves.slice(0, selected).map(m => m.uci);
  const { board } = replayLine(sequence.initial_fen, prefix);
  if (terminal(board)) throw Error('No moves allowed after a terminal position.');
  const played = board.move(input.trim());
  const uci = played.from + played.to + (played.promotion || '');
  if (sequence.moves[selected]?.uci === uci) return { advance: true as const, uci, replacement: false };
  replayLine(sequence.initial_fen, [...prefix, uci]);
  return { advance: false as const, uci, replacement: selected < sequence.moves.length };
}

export type NoteDrafts = Record<string, string>;
export type CommentResult = { line_id: string; move_id: string | null; comment: string; updated_at: string };
export function noteText(line: StudySequence, key: string | null) {
  return key === 'root' ? line.starting_note || '' : line.moves.find(m => m.id === key)?.comment || '';
}
export function editNoteDraft(line: StudySequence, drafts: NoteDrafts, key: string, text: string): NoteDrafts {
  const next = { ...drafts };
  if (text === noteText(line, key)) delete next[key]; else next[key] = text;
  return next;
}
export async function persistNoteDrafts(line: StudySequence, drafts: NoteDrafts,
  write: (path: string, body: { comment: string; expected_updated_at: string }) => Promise<CommentResult>,
  accepted: (line: StudySequence, key: string) => void) {
  let current = line;
  for (const [key, text] of Object.entries(drafts)) {
    if (key !== 'root' && !current.moves.some(m => m.id === key)) throw Error('This move is no longer in the saved line. Reload before editing.');
    const result = await write(`/lines/${current.line_id}/${key === 'root' ? 'starting-note' : `moves/${key}/comment`}`, { comment: text, expected_updated_at: current.updated_at });
    if (result.line_id !== current.line_id || result.move_id !== (key === 'root' ? null : key)) throw Error('Unexpected note identity returned by the server.');
    current = { ...current, updated_at: result.updated_at,
      ...(key === 'root' ? { starting_note: result.comment } : { moves: current.moves.map(m => m.id === key ? { ...m, comment: result.comment, has_comment: !!result.comment } : m) }) };
    // Publish each accepted revision. A later failure retains only the unsaved drafts.
    accepted(current, key);
  }
  return current;
}

export function initialNoteForFen(saved: StudySequence, current: StudySequence, fen: string) {
  // Returning to the saved root before saving must restore its original note.
  return fen === saved.initial_fen ? saved.starting_note || '' : fen === current.initial_fen ? current.starting_note || '' : '';
}
