"use client";
import { useEffect, useState } from "react";
import type { Replay } from "./pgn-context";
export function usePGNReplay(pgn: string, supplied?: Replay): { replay?: Replay; error?: string } {
  const [loaded, setLoaded] = useState<{ pgn: string; replay?: Replay; error?: string }>();
  useEffect(() => {
    if (supplied) return;
    const worker = new Worker(new URL("./pgn.worker.ts", import.meta.url));
    const timeout = setTimeout(() => { worker.terminate(); setLoaded({ pgn, error: "PGN replay exceeded the five-second browser limit" }); }, 5000);
    worker.onmessage = event => { clearTimeout(timeout); setLoaded({ pgn, ...event.data }); worker.terminate(); };
    worker.onerror = () => { clearTimeout(timeout); setLoaded({ pgn, error: "PGN worker unavailable" }); worker.terminate(); };
    worker.postMessage(pgn);
    return () => { clearTimeout(timeout); worker.terminate(); };
  }, [pgn, supplied]);
  return supplied ? { replay: supplied } : loaded?.pgn === pgn ? loaded : {};
}
