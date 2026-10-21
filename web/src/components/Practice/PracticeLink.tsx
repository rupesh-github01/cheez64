"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { pgnHash } from "@/lib/practice";
export default function PracticeLink({ gameID, pgn, ply, disabled = false }: { gameID: string; pgn: string; ply: number; disabled?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <div><button className="crit-nav-btn" disabled={disabled || busy} onClick={async () => {
    setBusy(true); setError("");
    try { const hash = await pgnHash(pgn); router.push(`/practice?${new URLSearchParams({ game: gameID, ply: String(ply), hash })}`); }
    catch { setError("Unable to identify this source. Open practice from the navigation menu."); setBusy(false); }
  }}>Create exercise from this position</button>{disabled && <small> Return to the recorded mainline first.</small>}{error && <p role="alert">{error}</p>}</div>;
}
