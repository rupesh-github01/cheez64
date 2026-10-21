"use client";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useDashboardRead } from "@/lib/player-dashboard";
import { pgnHash, practiceWrite, type Question, type Attempt } from "@/lib/practice";
import { usePractice } from "./usePractice";
import "./practice.css";
type Identity = { source: string; username: string; games: number };
type Game = { game_id: string; white: string; black: string; date: string; result: string };
export default function PracticeLibrary() {
  const query = useSearchParams();
  const [accountQuery, setAccountQuery] = useState("");
  const identities = useDashboardRead<{ items: Identity[]; next_cursor: string | null }>(`/api/players/identities?limit=50&q=${encodeURIComponent(accountQuery)}`);
  const [account, setAccount] = useState<Identity>(); const [selected, setSelected] = useState<string[]>([]);
  const [page, setPage] = useState(""); const [listPage, setListPage] = useState(""); const [historyPage, setHistoryPage] = useState("");
  const [rootPly, setRootPly] = useState(query.get("ply") || "");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [generation, setGeneration] = useState<{ items: Question[]; partial: boolean; examined: unknown[]; skipped: unknown[] }>();
  const [refresh, setRefresh] = useState(0);
  const status = usePractice<{ status: string; writes_enabled: boolean }>("/status");
  const selection = account ? new URLSearchParams({ source: account.source, username: account.username }) : null;
  const games = useDashboardRead<{ items: Game[]; next_cursor: string | null }>(selection ? `/api/players/games?${selection}&limit=20&after=${encodeURIComponent(page)}` : null);
  const puzzles = usePractice<{ items: Question[]; next_cursor: string | null }>(selection ? `/puzzles?${selection}&after=${encodeURIComponent(listPage)}` : null, refresh);
  const history = usePractice<{ items: Attempt[]; next_cursor: string | null }>(selection ? `/history?${selection}&after=${encodeURIComponent(historyPage)}` : null, refresh);
  const [tier, setTier] = useState("all");
  async function discover() {
    if (!account || !selected.length || busy) return;
    if (rootPly !== "" && (!/^\d+$/.test(rootPly) || Number(rootPly) > 10000 || selected.length !== 1)) { setError("For a specific ply, select one game and enter a number from 0 to 10000."); return; }
    setBusy(true); setError("");
    try {
      const requests = [];
      for (const game_id of selected) {
        const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        const response = await fetch(`${base}/api/games/${encodeURIComponent(game_id)}`, { cache: "no-store" });
        if (!response.ok) throw new Error("Source game unavailable");
        const game = await response.json(); const hash = await pgnHash(game.pgn);
        const expected = query.get("game") === game_id ? query.get("hash") : null;
        if (expected && expected !== hash) throw new Error("The review source changed. Reload the game before creating an exercise.");
        requests.push({ game_id, expected_pgn_sha256: hash, plies: rootPly === "" ? [] : [Number(rootPly)] });
      }
      const result = await practiceWrite<{ items: Question[]; partial: boolean; examined: unknown[]; skipped: unknown[] }>("/discover", { account: { source: account.source, username: account.username }, games: requests });
      setGeneration(result); setRefresh(n => n + 1); setListPage("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="practice"><h1>Practice from your games</h1><p>Find a mate-in-one objective or explore a guided tactical idea. No engine or cloud requests.</p>
    {status.loading ? <p role="status">Checking practice store…</p> : status.data?.status !== "AVAILABLE" ? <div className="practice-card"><h2>Practice store unavailable</h2><p>Initialize a dedicated practice database with the documented CLI. Opening this page creates nothing.</p></div> : !status.data.writes_enabled && <p>Practice is in read-only mode. Stored exercises can be viewed; generation and attempts are disabled.</p>}
    <label htmlFor="account-search">Find an account</label><input id="account-search" value={accountQuery} disabled={busy} onChange={e => setAccountQuery(e.target.value)} placeholder="Exact account or search fragment" /><label htmlFor="practice-account">Exact player account</label><select id="practice-account" value={account ? JSON.stringify([account.source, account.username]) : ""} onChange={e => { const found = identities.data?.items.find(a => JSON.stringify([a.source, a.username]) === e.target.value); setAccount(found); setSelected([]); setGeneration(undefined); setPage(""); setListPage(""); setHistoryPage(""); }} disabled={busy}><option value="">Choose player and platform</option>{identities.data?.items.map(a => <option key={JSON.stringify([a.source, a.username])} value={JSON.stringify([a.source, a.username])}>{a.username} · {a.source} · {a.games} games</option>)}</select>
    {identities.loading && <p role="status">Loading accounts…</p>}{identities.error && <p role="alert">{identities.error}</p>}{identities.data?.items.length === 0 && <p>{accountQuery ? "No accounts match this search." : "No imported player accounts. Import games in the library first."}</p>}{identities.data?.next_cursor && <p>Showing the first 50 matching accounts. Narrow the search to find another account.</p>}
    {query.get("game") && <p>From review: select the account and game {query.get("game")} below. The selected position is not generated automatically.</p>}
    {account && <><h2>Select up to three games</h2>{games.loading && <p role="status">Loading games…</p>}{games.error && <p role="alert">{games.error}</p>}{games.data?.items.length === 0 && <p>No games match this exact account.</p>}{games.data?.items.map(g => <label className="practice-game" key={g.game_id}><input type="checkbox" disabled={busy || (!selected.includes(g.game_id) && selected.length >= 3)} checked={selected.includes(g.game_id)} onChange={e => setSelected(value => e.target.checked ? [...value, g.game_id] : value.filter(id => id !== g.game_id))} /><span>{g.white} — {g.black} · {g.result}<br /><small>{g.date || "Date unknown"} · {g.game_id}</small></span></label>)}<button disabled={!page || busy} onClick={() => setPage("")}>First games</button><button disabled={!games.data?.next_cursor || busy} onClick={() => setPage(games.data?.next_cursor || "")}>Next games</button>
    <label htmlFor="root-ply">Optional position ply (one game only; blank = bounded sample)</label><input id="root-ply" inputMode="numeric" value={rootPly} onChange={e => setRootPly(e.target.value)} disabled={busy} />
    <button disabled={busy || !selected.length || status.data?.status !== "AVAILABLE" || !status.data.writes_enabled} onClick={() => void discover()}>{busy ? "Finding exercises…" : "Find exercises"}</button>
    {error && <p role="alert">{error}</p>}{generation && <p role="status">Found {generation.items.length} exercises from {generation.examined.length} examined roots. {generation.partial ? "Discovery was partial; some positions were skipped." : "This is a bounded sample, not a full tactical scan."}{!generation.items.length && " Try another selected position or game; no usable witness was found in this sample."}</p>}
    <h2>Saved exercises</h2><label htmlFor="tier">Evidence tier</label><select id="tier" value={tier} onChange={e => setTier(e.target.value)}><option value="all">All on this page</option><option value="VERIFIED_PUZZLE">Verified objectives</option><option value="GUIDED_EXERCISE">Guided exercises</option></select>
    {puzzles.loading && <p role="status">Loading exercises…</p>}{puzzles.error && <p role="alert">{puzzles.error}</p>}{puzzles.data?.items.length === 0 && <p>No saved exercises for this account. Choose games and find exercises explicitly.</p>}{puzzles.data?.items.filter(q => tier === "all" || tier === q.tier).map(q => <div className="practice-card" key={q.id}><a href={`/practice/${q.id}`}>{q.tier === "VERIFIED_PUZZLE" ? "Mate-in-one objective" : "Guided tactical exercise"}</a><p>{q.learner_color} to move · Source ply {q.source.ply} · {q.source.freshness === "CURRENT_MATCH_AT_CHECK" ? "Source current at check" : "Source changed or missing"}</p></div>)}<button disabled={!listPage} onClick={() => setListPage("")}>First exercises</button><button disabled={!puzzles.data?.next_cursor} onClick={() => setListPage(puzzles.data?.next_cursor || "")}>Next exercises</button>
    <h2>Practice history</h2><p>Practice outcomes describe these attempts, not your chess strength. Repeats and guided alternatives are not independent skill evidence.</p>{history.error && <p role="alert">{history.error}</p>}{history.data?.items.length === 0 && <p>No attempts yet.</p>}{history.data?.items.map(a => <div className="practice-card" key={a.id}><a href={`/practice/${a.puzzle_id}?attempt=${a.id}`}>{a.state.replaceAll("_", " ")}</a><p>{a.assisted ? "Assisted" : "Unassisted"} · {a.created_at ? new Date(a.created_at * 1000).toLocaleString() : ""}</p></div>)}<button disabled={!historyPage} onClick={() => setHistoryPage("")}>First attempts</button><button disabled={!history.data?.next_cursor} onClick={() => setHistoryPage(history.data?.next_cursor || "")}>Next attempts</button>
    </>}
  </div>;
}
