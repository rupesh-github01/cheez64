"use client";
import { useEffect, useState } from 'react';
import PuzzleBoard from '@/components/Practice/PuzzleBoard';
import { studyRequest, useStudy } from '@/lib/studies';
import { DEFAULT_POSITION, MAX_STUDY_PLIES, moveLabel, proposeMove, replayLine, type StudySequence } from '@/lib/study-line';

function Editor({ initial, writable, onDirty, reload }: { initial: StudySequence; writable: boolean; onDirty: (value: boolean) => void; reload: () => void }) {
  const [saved, setSaved] = useState(initial), [line, setLine] = useState(initial), [selected, setSelected] = useState(initial.moves.length);
  const [flipped, setFlipped] = useState(false), [fenInput, setFenInput] = useState(initial.initial_fen), [moveInput, setMoveInput] = useState('');
  const [pending, setPending] = useState<{ kind: 'move'; uci: string } | { kind: 'fen'; fen: string } | null>(null);
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const dirty = line.initial_fen !== saved.initial_fen || JSON.stringify(line.moves.map(m => m.uci)) !== JSON.stringify(saved.moves.map(m => m.uci));
  const fen = selected ? line.moves[selected - 1].fen_after : line.initial_fen;
  const locked = !writable || busy || !!pending;
  useEffect(() => { onDirty(dirty || busy); }, [dirty, busy, onDirty]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const before = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    const leave = (e: MouseEvent) => { const target = e.target instanceof Element ? e.target.closest('a[href]') : null; if (target && !window.confirm('Leave this line and discard unsaved changes?')) { e.preventDefault(); e.stopPropagation(); } };
    window.addEventListener('beforeunload', before); document.addEventListener('click', leave, true);
    return () => { window.removeEventListener('beforeunload', before); document.removeEventListener('click', leave, true); };
  }, [dirty, busy]);
  function applyMove(uci: string) {
    const next = replayLine(line.initial_fen, [...line.moves.slice(0, selected).map(m => m.uci), uci]);
    setLine({ ...line, initial_fen: next.initial_fen, moves: [...line.moves.slice(0, selected), ...next.moves.slice(selected)] }); setSelected(selected + 1); setMoveInput(''); setError(''); setMessage('');
  }
  function play(input: string) {
    if (locked) return;
    try {
      const result = proposeMove(line, selected, input);
      if (result.advance) { setSelected(selected + 1); setMoveInput(''); setError(''); }
      else if (result.replacement) setPending({ kind: 'move', uci: result.uci });
      else applyMove(result.uci);
    } catch (e) { setError(`Move rejected: ${(e as Error).message}`); }
  }
  function applyFen(value: string) { setLine({ ...line, initial_fen: value, moves: [] }); setFenInput(value); setSelected(0); setError(''); setMessage(''); }
  function changeFen(value: string) {
    if (locked) return;
    try { const next = replayLine(value, []).initial_fen; if (line.moves.length || saved.moves.length) setPending({ kind: 'fen', fen: next }); else applyFen(next); }
    catch (e) { setError(`Position rejected: ${(e as Error).message}`); }
  }
  async function save() {
    if (locked || !dirty) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const canonical = await studyRequest<StudySequence>(`/lines/${line.line_id}/sequence`, 'PUT', { initial_fen: line.initial_fen, moves: line.moves.map(m => m.uci), expected_updated_at: saved.updated_at, confirm_discard: confirmed });
      setLine(canonical); setSaved(canonical); setFenInput(canonical.initial_fen); setSelected(Math.min(selected, canonical.moves.length)); setConfirmed(false); setMessage('Line saved.');
    } catch (e) { setError(`Save failed: ${(e as Error).message}`); } finally { setBusy(false); }
  }
  const fields = fen.split(' ');
  return <section className="study-editor" aria-label="Study line editor">
    <div className="study-editor-toolbar"><h2>Line editor</h2><div className="study-actions"><span role="status">{busy ? 'Saving…' : dirty ? 'Unsaved changes' : message || 'Saved sequence'}</span><button disabled={locked || !dirty} onClick={() => void save()}>Save line</button><button disabled={busy} onClick={() => { if (!dirty || window.confirm('Discard unsaved changes and reload the saved line?')) reload(); }}>Reload saved line</button></div></div>
    {error && <p role="alert" className="study-error">{error}</p>}
    <div className="study-editor-grid"><div className="study-board"><PuzzleBoard key={`${fen}:${flipped}`} fen={fen} orientation={flipped ? 'black' : 'white'} disabled={locked} submit={play} />
      <p aria-live="polite">{fields[1] === 'w' ? 'White' : 'Black'} to move · Position {selected} of {line.moves.length}</p>
      <div className="study-actions" aria-label="Move navigation"><button disabled={busy || !!pending || !selected} onClick={() => setSelected(0)} aria-label="Beginning">|←</button><button disabled={busy || !!pending || !selected} onClick={() => setSelected(selected - 1)} aria-label="Previous move">←</button><button disabled={busy || !!pending || selected === line.moves.length} onClick={() => setSelected(selected + 1)} aria-label="Next move">→</button><button disabled={busy || !!pending || selected === line.moves.length} onClick={() => setSelected(line.moves.length)} aria-label="End">→|</button><button onClick={() => setFlipped(!flipped)} disabled={busy || !!pending}>Flip board</button></div>
      <form className="study-form" onSubmit={e => { e.preventDefault(); play(moveInput); }}><label>Play move (SAN or UCI)<input value={moveInput} disabled={locked} placeholder="e4 or e2e4" onChange={e => setMoveInput(e.target.value)} /></label><button disabled={locked || !moveInput.trim()}>Play move</button></form>
    </div><div className="study-moves"><h3>Move history</h3><p className="study-muted">Selected position is highlighted; the latest move is marked separately. Up to {MAX_STUDY_PLIES} plies.</p><button aria-current={selected === 0 ? 'step' : undefined} disabled={busy || !!pending} onClick={() => setSelected(0)}>Initial position</button><ol aria-label="Move history">{line.moves.map((move, index) => <li key={index}><button aria-current={selected === index + 1 ? 'step' : undefined} disabled={busy || !!pending} onClick={() => setSelected(index + 1)}>{moveLabel(line.initial_fen, index + 1, move.san)}</button>{index === line.moves.length - 1 && <small> Latest</small>}{move.has_comment && <small> · Saved note</small>}</li>)}</ol>{!line.moves.length && <p>No moves yet. Play a legal move on the board or enter SAN/UCI.</p>}<h3>Current position FEN</h3><code data-testid="current-fen">{fen}</code></div></div>
    {pending && <div role="alertdialog" aria-label="Replace continuation?" className="study-notice"><p>{pending.kind === 'fen' ? 'Changing the starting position removes the whole continuation.' : 'This move replaces the continuation after the selected position.'} Removed moves and their associated saved notes will be deleted when you save. The unchanged prefix is retained.</p><button onClick={() => { setConfirmed(true); if (pending.kind === 'move') applyMove(pending.uci); else applyFen(pending.fen); setPending(null); }}>Replace continuation</button> <button onClick={() => setPending(null)}>Keep existing continuation</button></div>}
    <section className="study-notice"><h3>Initial position</h3><p>Standard chess · Changes belong only to this line. The server validates the complete position and sequence on Save line.</p><button disabled={locked} onClick={() => changeFen(DEFAULT_POSITION)}>Reset to standard starting position</button><form className="study-form" onSubmit={e => { e.preventDefault(); changeFen(fenInput); }}><label>Custom starting FEN (six fields)<textarea rows={3} maxLength={200} value={fenInput} disabled={locked} onChange={e => setFenInput(e.target.value)} /></label><button disabled={locked}>Set custom starting position</button></form><p className="study-muted">Unsaved changes are local until Save line. No engine analysis or per-move note editing.</p></section>
  </section>;
}
export default function LineEditor({ id, writable, onDirty }: { id: string; writable: boolean; onDirty: (value: boolean) => void }) {
  const [revision, setRevision] = useState(0), sequence = useStudy<StudySequence>(`/lines/${id}/sequence`, revision);
  if (sequence.loading) return <p role="status">Loading saved line…</p>;
  if (sequence.error || !sequence.data) return <p role="alert" className="study-error">{sequence.error || 'Saved line unavailable'}</p>;
  return <Editor key={`${id}:${revision}`} initial={sequence.data} writable={writable} onDirty={onDirty} reload={() => { onDirty(false); setRevision(n => n + 1); }} />;
}
