import { replayPGN } from "./pgn-context";
const worker = self as unknown as { onmessage: ((event: MessageEvent<string>) => void) | null; postMessage: (data: unknown) => void };
worker.onmessage = event => {
  try { worker.postMessage({ replay: replayPGN(event.data) }); }
  catch (error) { worker.postMessage({ error: error instanceof Error ? error.message : "Invalid PGN" }); }
};
