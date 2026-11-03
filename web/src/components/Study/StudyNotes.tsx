"use client";
export default function StudyNotes({ label, text, enabled, currentDirty, count, busy, message, onChange, onSave, onSaveAll, onDiscard }: {
  label: string; text: string; enabled: boolean; currentDirty: boolean; count: number; busy: boolean; message: string;
  onChange: (text: string) => void; onSave: () => void; onSaveAll: () => void; onDiscard: () => void;
}) {
  return <section className="study-notes" aria-label="Study notes"><h3>{label}</h3>
    <p className="study-muted">Your own study notes · plain text · up to 5,000 characters. Drafts stay with their move when you navigate.</p>
    <label htmlFor="study-note">Study note</label><textarea id="study-note" rows={7} maxLength={5000} value={text} disabled={!enabled || busy} placeholder="Explain this position, its ideas and what to remember…" onChange={e => onChange(e.target.value)} />
    <p role="status">{busy ? 'Saving notes…' : currentDirty ? 'Unsaved note for this position' : message || (text ? 'Saved note' : 'No note yet')}{count > 0 && ` · ${count} unsaved ${count === 1 ? 'note' : 'notes'} in this line`}</p>
    <div className="study-actions"><button disabled={!enabled || busy || !currentDirty} onClick={onSave}>Save note</button><button disabled={!enabled || busy || !count} onClick={onSaveAll}>Save all notes</button><button disabled={!enabled || busy || !text} onClick={() => onChange('')}>Clear comment</button><button disabled={!enabled || busy || !currentDirty} onClick={onDiscard}>Discard note changes</button></div>
  </section>;
}
