import { useEffect, useState } from 'react';
export type StudyItem = { id: string; title: string; description: string; created_at: string; updated_at: string; study_id?: string; sub_study_id?: string; source_url?: string | null; sort_order?: number; initial_fen?: string };
export type StudyPage = { items: StudyItem[]; total: number; offset: number; limit: number; next_offset: number | null };
export type StudyStatus = { status: string; writes_enabled: boolean; message?: string };
export async function studyRequest<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/studies${path}`, { method, signal, cache: 'no-store', headers: method === 'GET' ? {} : { 'content-type': 'application/json', 'x-study-action': 'local-study' }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) {
    let message = `Study request failed (${response.status})`;
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : response.status === 422 ? 'Check the title, description and HTTP(S) source URL.' : message; } catch { /* Keep a bounded, useful fallback. */ }
    throw Error(message);
  }
  return response.json();
}
export function useStudy<T>(path: string | null, revision = 0) {
  const key = JSON.stringify([path, revision]); const [result, setResult] = useState<{ key: string; data?: T; error?: string }>();
  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    studyRequest<T>(path, 'GET', undefined, controller.signal).then(data => { if (!controller.signal.aborted) setResult({ key, data }); }).catch(e => { if (!controller.signal.aborted) setResult({ key, error: String(e.message) }); });
    return () => controller.abort();
  }, [path, key]);
  return { data: result?.key === key ? result.data : undefined, error: result?.key === key ? result.error : undefined, loading: path !== null && result?.key !== key };
}
