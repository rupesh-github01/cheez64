"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { usePractice } from "./usePractice";
import { practiceWrite, feedbackLabel, type Question, type Session } from "@/lib/practice";
import "./practice.css";
const Board = dynamic(() => import("./PuzzleBoard"), { ssr: false, loading: () => <p>Loading board…</p> });
export default function PuzzleSession({ puzzleID }: { puzzleID: string }) {
  const router = useRouter(); const params = useSearchParams(); const aid = params.get("attempt");
  const capability = usePractice<{ writes_enabled: boolean }>("/status");
  const writesEnabled = capability.data?.writes_enabled === true;
  const [refresh, setRefresh] = useState(0);
  const read = usePractice<Session | Question>(aid ? `/attempts/${encodeURIComponent(aid)}` : `/puzzles/${encodeURIComponent(puzzleID)}`, refresh);
  const [local, setLocal] = useState<{ key: string; value: Session }>();
  const key = `${puzzleID}:${aid}`;
  const session = local?.key === key ? local.value : read.data && "attempt" in read.data ? read.data : undefined;
  const q = session?.question || (read.data && "id" in read.data ? read.data : undefined);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [input, setInput] = useState("");
  const pending = useRef<{ signature: string; body: unknown } | null>(null);
  const activeKey = useRef(key);
  useEffect(() => { activeKey.current = key; return () => { activeKey.current = ""; }; }, [key]);
  async function act(kind: string, uci?: string) {
    if (busy || !q || !writesEnabled) return;
    setBusy(true); setError("");
    const capturedKey = key;
    const signature = JSON.stringify([key, session?.attempt.revision, kind, uci]);
    const body = kind === "start" || kind === "retry" ? { puzzle_id: puzzleID, request_key: crypto.randomUUID(), ...(kind === "retry" && session ? { retry_of: session.attempt.id } : {}) } : { kind, revision: session?.attempt.revision, request_key: crypto.randomUUID(), ...(uci ? { uci } : {}) };
    if (pending.current?.signature !== signature) pending.current = { signature, body };
    try {
      if (kind === "start" || kind === "retry") {
        const result = await practiceWrite<Session>("/attempts", pending.current.body);
        if (activeKey.current !== capturedKey) return;
        pending.current = null;
        setLocal({ key: `${puzzleID}:${result.attempt.id}`, value: result });
        router.replace(`/practice/${puzzleID}?attempt=${result.attempt.id}`);
      } else if (session) {
        const result = await practiceWrite<Pick<Session, "attempt">>(`/attempts/${session.attempt.id}/events`, pending.current.body);
        if (activeKey.current !== capturedKey) return;
        pending.current = null; setLocal({ key, value: { ...session, ...result } }); setInput("");
      }
    } catch (e) { if (activeKey.current === capturedKey) setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const feedback = session?.attempt.feedback;
  const stale = q?.source.freshness !== "CURRENT_MATCH_AT_CHECK";
  const playable = !!session && session.attempt.state === "ACTIVE" && !stale && writesEnabled;
  const displayed = feedback?.code === "OBJECTIVE_MET" || feedback?.code === "DEMONSTRATED_TARGET" || feedback?.code === "LEGAL_ALTERNATIVE_UNASSESSED" ? feedback.fen || q?.fen : q?.fen;
  return <div className="practice"><Link href="/practice">← Practice library</Link><h1>Personal chess practice</h1>
    {(read.loading && !q) && <p role="status">Loading exercise…</p>}{read.error && <p role="alert">{read.error}</p>}
    {q && <><p className="practice-badge">{q.tier === "VERIFIED_PUZZLE" ? "Verified mate-in-one objective" : "Guided exercise · optimality unverified"}</p><h2>{q.prompt}</h2>
    {!writesEnabled && <p>Practice writes are disabled or unavailable. View saved state or enable local practice explicitly.</p>}
    {stale && <p role="alert">The source changed or is missing. Regenerate from the current game before continuing.</p>}
    <div className="practice-grid"><div>{displayed && <Board key={`${key}:${session?.attempt.revision}`} fen={displayed} orientation={q.learner_color} disabled={!playable || busy} submit={uci => void act("move", uci)} />}</div><section aria-label="Practice controls">
      {!session ? <button disabled={busy || stale || !writesEnabled} onClick={() => void act("start")}>Start practice</button> : <>
        <p>{session.attempt.state.replaceAll("_", " ")} · {session.attempt.assisted ? "Assisted" : "Unassisted"}</p>
        <form onSubmit={e => { e.preventDefault(); if (/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(input.trim().toLowerCase())) void act("move", input.trim().toLowerCase()); else setError("Use coordinate notation, for example e2e4 or a7a8n."); }}>
          <label htmlFor="puzzle-move">Move (coordinate notation)</label><input id="puzzle-move" autoComplete="off" value={input} disabled={!playable || busy} onChange={e => setInput(e.target.value)} placeholder="e2e4 or a7a8n" /><button disabled={!playable || busy}>Submit move</button>
        </form>
        <div className="practice-actions"><button disabled={!playable || busy || session.attempt.hints >= 2} onClick={() => void act("hint")}>Hint {Math.min(2, session.attempt.hints + 1)}</button><button disabled={stale || busy || !writesEnabled} onClick={() => void act("reveal")}>Reveal witness</button><button disabled={stale || busy || !writesEnabled} onClick={() => void act("retry")}>Retry / reset</button><button disabled={!playable || busy} onClick={() => void act("abandon")}>End attempt</button></div>
        {feedback && <div className="practice-feedback" role="status"><h3>{feedbackLabel(feedback.code)}</h3><p>{feedback.message}</p>{feedback.solution && <><strong>{feedback.solution.san}</strong><p>{feedback.solution.explanation}</p><p>{feedback.solution.provenance} · {feedback.solution.theme.replaceAll("_", " ")}</p></>}</div>}
      </>}
      {busy && <p role="status">Saving…</p>}{error && <p role="alert">{error}</p>}<button disabled={busy} onClick={() => { pending.current = null; setLocal(undefined); setRefresh(n => n + 1); }}>Reload saved state</button>
      <p><a href={`/games/${encodeURIComponent(q.source.game_id)}?ply=${q.source.ply}`}>Open original game at ply {q.source.ply}</a> (may reveal the continuation)</p><details><summary>Evidence and limitations</summary><p>Difficulty: unknown. {q.history_complete ? "History from the initial position." : "History before setup is unknown."}</p>{q.limitations.map(text => <p key={text}>{text}</p>)}</details>
    </section></div></>}
  </div>;
}
