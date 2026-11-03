"use client";
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { studyRequest, useStudy, type StudyItem, type StudyPage, type StudyStatus } from '@/lib/studies';
import './study.css';
import LineEditor from './LineEditor';
type Kind = 'study' | 'sub-study' | 'line';
function Message({ loading, error }: { loading?: boolean; error?: string }) { return loading ? <p role="status" className="study-muted">Loading study workspace…</p> : error ? <p role="alert" className="study-error">{error}</p> : null; }
function StoreBanner({ status, error }: { status?: StudyStatus; error?: string }) {
  if (error) return <Message error={error} />;
  if (!status) return <Message loading />;
  if (status.status !== 'AVAILABLE') return <aside className="study-notice"><h2>Study storage needs setup</h2><p>The independent study store is uninitialized or unavailable. No file was created by this page.</p><p>An operator can initialize the configured CHESS_COACH_STUDY_DB, or the default data/studies.db, explicitly:</p><code>.venv/bin/python -m src.study_store init</code><p>After setup, refresh this page. Existing game and evaluation stores are unaffected.</p></aside>;
  return !status.writes_enabled ? <p className="study-notice">Read-only mode · you can browse studies; editing is disabled.</p> : null;
}
function MetadataForm({ kind, initial, disabled, onSave, onCancel }: { kind: Kind; initial?: StudyItem; disabled?: boolean; onSave: (data: { title: string; description: string; source_url?: string | null }) => Promise<void>; onCancel?: () => void }) {
  const [title, setTitle] = useState(initial?.title || ''), [description, setDescription] = useState(initial?.description || ''), [url, setUrl] = useState(initial?.source_url || '');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function save(e: React.FormEvent) {
    e.preventDefault(); if (busy || disabled) return; setBusy(true); setError('');
    try { await onSave({ title: title.trim(), description: description.trim(), ...(kind === 'sub-study' ? { source_url: url.trim() || null } : {}) }); if (!initial) { setTitle(''); setDescription(''); setUrl(''); } }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const label = kind[0].toUpperCase() + kind.slice(1);
  return <form onSubmit={e => void save(e)} className="study-form" aria-label={`${initial ? 'Edit' : 'New'} ${kind}`}>
    <label>{label} title<input required maxLength={160} value={title} disabled={disabled || busy} onChange={e => setTitle(e.target.value)} /></label>
    <label>{label} description<textarea rows={3} maxLength={5000} value={description} disabled={disabled || busy} onChange={e => setDescription(e.target.value)} /></label>
    {kind === 'sub-study' && <label>Source URL (optional)<input type="url" maxLength={2048} placeholder="https://www.youtube.com/watch?v=…" value={url} disabled={disabled || busy} onChange={e => setUrl(e.target.value)} /></label>}
    <Message error={error} /><div className="study-actions"><button disabled={disabled || busy || !title.trim()}>{busy ? 'Saving…' : `${initial ? 'Save' : 'Create'} ${kind}`}</button>{onCancel && <button type="button" onClick={onCancel} disabled={busy}>Cancel edit</button>}</div>
  </form>;
}
function Entity({ item, kind, endpoint, href, writable, refresh, deleted, children, onActivity }: { item: StudyItem; kind: Kind; endpoint: string; href?: string; writable: boolean; refresh: () => void; deleted?: () => void; children?: React.ReactNode; onActivity?: (busy: boolean) => void }) {
  const [editing, setEditing] = useState(false), [confirm, setConfirm] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function remove() { setBusy(true); onActivity?.(true); setError(''); try { await studyRequest(endpoint, 'DELETE'); setConfirm(false); (deleted || refresh)(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); onActivity?.(false); } }
  return <article className="study-card"><div className="study-card-heading"><div><p className="study-kicker">{kind}{item.sort_order !== undefined ? ` · ${item.sort_order + 1}` : ''}</p><h2>{href ? <Link href={href}>{item.title}</Link> : item.title}</h2></div><div className="study-actions"><button disabled={!writable || busy} aria-label={`Edit ${kind} ${item.title}`} onClick={() => { setEditing(v => !v); setConfirm(false); }}>Edit</button><button disabled={!writable || busy} aria-label={`Delete ${kind} ${item.title}`} onClick={() => { setConfirm(true); setEditing(false); }}>Delete</button></div></div>
    <p className="study-description">{item.description || 'No description yet.'}</p>
    {item.source_url && <p><a href={item.source_url} target="_blank" rel="noopener noreferrer">Open source ↗</a><small className="study-url">{item.source_url}</small></p>}
    {editing && <MetadataForm key={item.updated_at} kind={kind} initial={item} disabled={!writable} onCancel={() => setEditing(false)} onSave={async data => { onActivity?.(true); try { await studyRequest(endpoint, 'PUT', data); setEditing(false); refresh(); } finally { onActivity?.(false); } }} />}
    {confirm && <div role="alertdialog" aria-label={`Delete ${kind} ${item.title}?`} className="study-notice"><p>Delete “{item.title}”? {kind === 'study' ? 'All its sub-studies, lines and saved moves will also be deleted.' : kind === 'sub-study' ? 'All its lines and saved moves will also be deleted.' : 'Any saved moves will also be deleted.'} This cannot be undone.</p><div className="study-actions"><button disabled={busy || !writable} onClick={() => void remove()}>Confirm delete</button><button disabled={busy} onClick={() => setConfirm(false)}>Keep {kind}</button></div></div>}
    <Message error={error} />{children}
  </article>;
}
function Pages({ page, setOffset }: { page?: StudyPage; setOffset: (n: number) => void }) {
  if (!page || page.total <= page.limit) return null;
  return <div className="study-actions"><button disabled={!page.offset} onClick={() => setOffset(Math.max(0, page.offset - page.limit))}>Previous page</button><span>{page.offset + 1}–{Math.min(page.total, page.offset + page.limit)} of {page.total}</span><button disabled={page.next_offset === null} onClick={() => setOffset(page.next_offset!)}>Next page</button></div>;
}
function Ordering({ page, index, endpoint, writable, refresh }: { page: StudyPage; index: number; endpoint: string; writable: boolean; refresh: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  if (page.offset || page.next_offset !== null || page.total < 2) return null;
  async function move(delta: number) { const ids = page.items.map(x => x.id); [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]]; setBusy(true); setError(''); try { await studyRequest(endpoint, 'POST', { ids }); refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  return <div className="study-actions"><button disabled={!writable || busy || !index} aria-label={`Move ${page.items[index].title} up`} onClick={() => void move(-1)}>↑</button><button disabled={!writable || busy || index === page.items.length - 1} aria-label={`Move ${page.items[index].title} down`} onClick={() => void move(1)}>↓</button><Message error={error} /></div>;
}
function SubStudy({ item, writable, refresh }: { item: StudyItem; writable: boolean; refresh: () => void }) {
  const [open, setOpen] = useState(false), [revision, setRevision] = useState(0), [offset, setOffset] = useState(0); const reload = () => setRevision(n => n + 1);
  const lines = useStudy<StudyPage>(open ? `/sub-studies/${item.id}/lines?limit=50&offset=${offset}` : null, revision);
  return <Entity item={item} kind="sub-study" endpoint={`/sub-studies/${item.id}`} writable={writable} refresh={refresh}>
    <button aria-expanded={open} onClick={() => setOpen(v => !v)}>{open ? 'Close' : 'Open'} sub-study · {item.title}</button>
    {open && <section className="study-lines" aria-label={`Lines in ${item.title}`}><h3>Study lines</h3><Message loading={lines.loading} error={lines.error} />
      {lines.data?.items.length === 0 && <p>No lines here yet. Add your first preparation line.</p>}
      {lines.data?.items.map((line, index) => <div key={line.id}><Entity item={line} kind="line" endpoint={`/lines/${line.id}`} href={`/study/lines/${line.id}`} writable={writable} refresh={reload} /><Ordering page={lines.data!} index={index} endpoint={`/sub-studies/${item.id}/lines/reorder`} writable={writable} refresh={reload} /></div>)}
      <Pages page={lines.data} setOffset={setOffset} />
      <h3>New line</h3><MetadataForm kind="line" disabled={!writable} onSave={async data => { await studyRequest(`/sub-studies/${item.id}/lines`, 'POST', data); reload(); }} />
    </section>}
  </Entity>;
}
export function StudyLibrary() {
  const status = useStudy<StudyStatus>('/status'); const available = status.data?.status === 'AVAILABLE', writable = !!(available && status.data?.writes_enabled);
  const [revision, setRevision] = useState(0), [offset, setOffset] = useState(0); const reload = () => setRevision(n => n + 1);
  const studies = useStudy<StudyPage>(available ? `?limit=25&offset=${offset}` : null, revision);
  return <div className="study-workspace"><header><p className="study-kicker">Personal preparation</p><h1>Study Library</h1><p>Organize studies → sub-studies → independent study lines.</p></header><StoreBanner status={status.data} error={status.error} />
    {available && <><section className="study-new"><h2>New study</h2><MetadataForm kind="study" disabled={!writable} onSave={async data => { await studyRequest('', 'POST', data); setOffset(0); reload(); }} /></section><Message loading={studies.loading} error={studies.error} />
      {studies.data?.items.length === 0 && <p className="study-notice">Your study library is empty. Create a study to begin organizing your preparation.</p>}
      {studies.data?.items.map(item => <Entity key={item.id} item={item} kind="study" endpoint={`/${item.id}`} href={`/study/${item.id}`} writable={writable} refresh={reload} />)}<Pages page={studies.data} setOffset={setOffset} /></>}
  </div>;
}
export function StudyDetail({ id }: { id: string }) {
  const router = useRouter(), status = useStudy<StudyStatus>('/status'); const available = status.data?.status === 'AVAILABLE', writable = !!(available && status.data?.writes_enabled);
  const [revision, setRevision] = useState(0), [offset, setOffset] = useState(0); const reload = () => setRevision(n => n + 1);
  const study = useStudy<StudyItem>(available ? `/${id}` : null, revision), subs = useStudy<StudyPage>(available ? `/${id}/sub-studies?limit=50&offset=${offset}` : null, revision);
  return <div className="study-workspace"><Link href="/study">← Study Library</Link><h1>{study.data?.title || 'Study'}</h1><StoreBanner status={status.data} error={status.error} /><Message loading={study.loading} error={study.error} />
    {study.data && <><Entity item={study.data} kind="study" endpoint={`/${id}`} writable={writable} refresh={reload} deleted={() => router.push('/study')} /><h2>Sub-studies</h2><Message loading={subs.loading} error={subs.error} />{subs.data?.items.length === 0 && <p className="study-notice">No sub-studies yet. Group your lines by video, book or theme.</p>}
      {subs.data?.items.map((item, index) => <div key={item.id}><SubStudy item={item} writable={writable} refresh={reload} /><Ordering page={subs.data!} index={index} endpoint={`/${id}/sub-studies/reorder`} writable={writable} refresh={reload} /></div>)}<Pages page={subs.data} setOffset={setOffset} />
      <section className="study-new"><h2>New sub-study</h2><MetadataForm kind="sub-study" disabled={!writable} onSave={async data => { await studyRequest(`/${id}/sub-studies`, 'POST', data); reload(); }} /></section></>}
  </div>;
}
export function StudyLineDetail({ id }: { id: string }) {
  const router = useRouter(), status = useStudy<StudyStatus>('/status'); const available = status.data?.status === 'AVAILABLE', writable = !!(available && status.data?.writes_enabled);
  const [dirty, setDirty] = useState(false), [metadataBusy, setMetadataBusy] = useState(false);
  const [revision, setRevision] = useState(0); const line = useStudy<StudyItem>(available ? `/lines/${id}` : null, revision), parent = useStudy<StudyItem>(line.data ? `/sub-studies/${line.data.sub_study_id}` : null);
  return <div className="study-workspace"><Link href="/study">Study Library</Link>{parent.data && <p><Link href={`/study/${parent.data.study_id}`}>← Parent study · {parent.data.title}</Link>{parent.data.source_url && <> · <a href={parent.data.source_url} target="_blank" rel="noopener noreferrer">Open study source ↗</a></>}</p>}<h1>{line.data?.title || 'Study line'}</h1><StoreBanner status={status.data} error={status.error} /><Message loading={line.loading} error={line.error} />
    {line.data && <><Entity item={line.data} kind="line" endpoint={`/lines/${id}`} writable={writable && !dirty && !metadataBusy} onActivity={setMetadataBusy} refresh={() => setRevision(n => n + 1)} deleted={() => router.push(parent.data ? `/study/${parent.data.study_id}` : '/study')} /><LineEditor key={id} id={id} writable={writable && !metadataBusy} onDirty={setDirty} /><p className="study-muted">Created {line.data.created_at} · Updated {line.data.updated_at}</p></>}
  </div>;
}
