import { acquireBrowserEngine } from "./browser-engine-lease";
import { DepthCohort, ENGINE_URL, parseInfo, parseOption, positionCommand, projectLiveLine } from "./browser-stockfish";
import type { LiveLine, LivePosition, UciOption } from "./browser-stockfish";
export type LiveSnapshot = {
  key: string; enabled: boolean; status: "idle" | "loading" | "ready" | "searching" | "complete" | "stopped" | "error" | "terminal";
  engine: string; lines: LiveLine[]; depth: number; complete: boolean; expected: number; options: Record<string, number | boolean>;
  elapsed: number; workerAlive: boolean; workerStarts: number; capability?: string; message?: string; error?: string; bestmove?: string;
};
export interface UciWorker {
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent) => void) | null;
  postMessage(command: string): void;
  terminate(): void;
}
type ControllerOptions = { worker?: () => UciWorker; lease?: (fallback: boolean) => Promise<() => void>; now?: () => number };
export class BrowserStockfishController {
  private worker?: UciWorker;
  private epoch = 0;
  private release?: () => void;
  private acquiring = false;
  private disposed = false;
  private fallback = false;
  private position?: LivePosition;
  private cohort = new DepthCohort(3);
  private configuredMultiPV = 3;
  private phase: "closed" | "uci" | "config" | "reset" | "search" | "stopping" | "barrier" | "idle" = "closed";
  private advertised = new Map<string, UciOption>();
  private stageTimer?: ReturnType<typeof setTimeout>;
  private searchTimer?: ReturnType<typeof setTimeout>;
  private navigationTimer?: ReturnType<typeof setTimeout>;
  private searchStarted = 0;
  private view: LiveSnapshot = { key: "", enabled: false, status: "idle", engine: "", lines: [], depth: 0, complete: false, expected: 3, options: {}, elapsed: 0, workerAlive: false, workerStarts: 0 };
  constructor(private publish: (state: LiveSnapshot) => void, private dependencies: ControllerOptions = {}) {}
  private update(change: Partial<LiveSnapshot>) { this.view = { ...this.view, ...change }; if (!this.disposed) this.publish({ ...this.view, lines: [...this.view.lines], options: { ...this.view.options } }); }
  private clearTimers() { clearTimeout(this.stageTimer); clearTimeout(this.searchTimer); clearTimeout(this.navigationTimer); }
  private teardownWorker() {
    this.epoch++; this.clearTimers();
    if (this.worker) { this.worker.onmessage = null; this.worker.onerror = null; this.worker.onmessageerror = null; this.worker.terminate(); this.worker = undefined; }
    this.phase = "closed";
  }
  private fatal(message: string) { this.teardownWorker(); this.release?.(); this.release = undefined; this.update({ enabled: false, status: "error", workerAlive: false, error: message }); }
  private send(command: string) {
    if (!this.worker) return false;
    try { this.worker.postMessage(command); return true; } catch { this.fatal("Engine command delivery failed. Start again explicitly."); return false; }
  }
  private deadline(ms: number, message: string) { clearTimeout(this.stageTimer); this.stageTimer = setTimeout(() => this.fatal(message), ms); }
  start(position: LivePosition, allowUncoordinated = false) {
    if (this.disposed) return;
    this.fallback = allowUncoordinated;
    this.update({ enabled: true, error: undefined, lines: [], depth: 0, complete: false, bestmove: undefined });
    this.select(position);
  }
  select(position: LivePosition) {
    if (this.disposed) return;
    const changed = this.position?.key !== position.key; this.position = position;
    if (changed) this.update({ key: position.key, lines: [], depth: 0, complete: false, elapsed: 0, bestmove: undefined, error: undefined, status: this.view.enabled ? "loading" : "idle" });
    if (position.terminal) {
      this.teardownWorker(); this.release?.(); this.release = undefined;
      this.update({ key: position.key, status: "terminal", workerAlive: false, lines: [], depth: 0, complete: false, message: position.terminal }); return;
    }
    if (!this.view.enabled) return;
    if (!this.worker && !this.acquiring) { void this.open(); return; }
    if (!changed) return;
    if (this.phase === "search") {
      clearTimeout(this.searchTimer); this.phase = "stopping"; if (!this.send("stop")) return;
      // A single-thread engine may not service stop while calculating. Replacement
      // here is intentional navigation cancellation, never automatic crash recovery.
      clearTimeout(this.stageTimer); this.stageTimer = setTimeout(() => {
        if (this.view.enabled && this.phase === "stopping") { this.teardownWorker(); this.createWorker(); }
      }, 750);
    } else if (this.phase === "idle") {
      clearTimeout(this.navigationTimer); this.navigationTimer = setTimeout(() => this.resetSearch(), 120);
    }
  }
  private async open() {
    this.acquiring = true; this.update({ status: "loading", message: "Acquiring browser engine slot…" });
    try {
      const release = await (this.dependencies.lease || acquireBrowserEngine)(this.fallback);
      if (this.disposed || !this.view.enabled || this.position?.terminal) { release(); return; }
      this.release = release; this.createWorker();
    } catch (error) { if (!this.disposed && this.view.enabled) this.fatal(error instanceof Error ? error.message : "Browser engine slot unavailable."); }
    finally { this.acquiring = false; }
  }
  private createWorker() {
    if (!this.view.enabled || !this.position || this.position.terminal || this.disposed) return;
    try {
      const worker = (this.dependencies.worker || (() => new Worker(ENGINE_URL)))(); this.worker = worker; const epoch = ++this.epoch;
      this.phase = "uci"; this.advertised.clear();
      this.update({ status: "loading", engine: "", options: {}, capability: undefined, workerAlive: true, workerStarts: this.view.workerStarts + 1, message: "Loading pinned Stockfish WASM and UCI…" });
      worker.onmessage = event => {
        if (this.disposed || epoch !== this.epoch || worker !== this.worker) return;
        if (typeof event.data !== "string" || event.data.length > 65536) { this.fatal("Invalid or oversized engine output."); return; }
        const lines = event.data.split(/\r?\n/); if (lines.length > 128) { this.fatal("Engine output budget exceeded."); return; }
        for (const line of lines) { if (epoch !== this.epoch) break; this.receive(line.trim()); }
      };
      worker.onerror = () => { if (epoch === this.epoch) this.fatal("Stockfish worker failed to load or crashed. Check browser WASM support and local engine assets; no automatic restart."); };
      worker.onmessageerror = () => { if (epoch === this.epoch) this.fatal("Stockfish worker message decoding failed."); };
      this.deadline(15000, "Stockfish UCI startup timed out. No automatic restart."); this.send("uci");
    } catch { this.fatal("Browser Worker creation failed. A supported browser and local engine assets are required."); }
  }
  private receive(line: string) {
    if (!line || line.length > 8192) return;
    if (this.phase === "uci") {
      if (line.startsWith("id name ")) this.update({ engine: line.slice(8, 168) });
      const option = parseOption(line); if (option && this.advertised.size < 100) this.advertised.set(option.name.toLowerCase(), option);
      if (line === "uciok") this.configure();
      return;
    }
    if (line === "readyok") {
      if (this.phase === "config" || this.phase === "barrier") this.resetSearch();
      else if (this.phase === "reset") this.beginSearch();
      return;
    }
    if (line.startsWith("bestmove ")) {
      clearTimeout(this.searchTimer);
      if (this.phase === "stopping") { clearTimeout(this.stageTimer); this.phase = "barrier"; this.deadline(3000, "Engine cancellation readiness timed out."); this.send("isready"); }
      else if (this.phase === "search" && this.position) {
        const move = line.split(/\s+/)[1];
        let pushed = false;
        try {
          if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move)) throw new Error("Invalid bestmove");
          this.position.board.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move.slice(4) || undefined }); pushed = true;
        } catch { this.fatal("Engine reported an illegal bestmove."); return; }
        finally { if (pushed) this.position.board.undo(); }
        this.phase = "idle"; this.update({ status: "complete", bestmove: move, elapsed: this.now() - this.searchStarted, complete: this.cohort.complete && this.view.lines[0]?.moves[0] === move, message: this.view.lines.length ? "Bounded search finished; estimates are not best-move proofs." : "Search finished without an admissible PV." });
      }
      return;
    }
    if (this.phase !== "search" || !this.position) return;
    const info = parseInfo(line); if (!info) return;
    try {
      const projected = projectLiveLine(this.position, info);
      if (this.cohort.accept(projected)) this.update({ lines: this.cohort.lines, depth: this.cohort.depth, complete: this.cohort.complete, elapsed: this.now() - this.searchStarted });
    } catch { /* Reject the whole illegal/inconsistent PV; never display a prefix. */ }
  }
  private configure() {
    if (!this.view.engine.toLowerCase().includes("stockfish")) { this.fatal("Unexpected UCI engine identity."); return; }
    const hash = this.advertised.get("hash"), threads = this.advertised.get("threads"), mp = this.advertised.get("multipv");
    if (!hash || hash.type !== "spin" || hash.min === undefined || hash.max === undefined || hash.min < 1 || hash.min > 16 || hash.max < hash.min) { this.fatal("Engine does not advertise a conservative Hash setting."); return; }
    if (threads && (threads.type !== "spin" || threads.min === undefined || threads.min > 1 || !threads.max)) { this.fatal("Engine cannot be configured with Threads=1."); return; }
    if (mp && (mp.type !== "spin" || mp.min === undefined || mp.max === undefined || mp.min < 1 || mp.min > 3 || mp.max < mp.min)) { this.fatal("Engine cannot support the approved MultiPV setting."); return; }
    const multipv = mp?.max !== undefined ? Math.min(3, mp.max) : 1;
    if (multipv < 1) { this.fatal("Invalid engine MultiPV capability."); return; }
    this.configuredMultiPV = multipv;
    const options: Record<string, number | boolean> = { Hash: Math.min(16, hash.max) };
    if (threads) options.Threads = 1;
    if (mp) options.MultiPV = multipv;
    if (this.advertised.has("ponder")) options.Ponder = false;
    this.phase = "config";
    for (const [name, value] of Object.entries(options)) if (!this.send(`setoption name ${name} value ${value}`)) return;
    this.update({ options, expected: multipv, capability: multipv < 3 ? `Engine supports only ${multipv} PV(s), not three.` : undefined, message: multipv < 3 ? `Engine capability: only ${multipv} PV(s); missing lines will not be invented.` : "MultiPV=3 sent; awaiting readyok. Observed output confirms returned line count." });
    this.deadline(5000, "Engine option readiness timed out."); this.send("isready");
  }
  private resetSearch() {
    if (!this.worker || !this.view.enabled || !this.position || this.position.terminal) return;
    clearTimeout(this.stageTimer); this.phase = "reset";
    this.update({ status: "ready", lines: [], depth: 0, complete: false, bestmove: undefined });
    if (!this.send("ucinewgame")) return;
    this.deadline(5000, "Engine reset readiness timed out."); this.send("isready");
  }
  private beginSearch() {
    if (!this.position || !this.view.enabled) return;
    clearTimeout(this.stageTimer); this.phase = "search"; this.cohort = new DepthCohort(Math.min(this.configuredMultiPV, this.position.legalCount)); this.searchStarted = this.now();
    this.update({ key: this.position.key, status: "searching", expected: this.cohort.expected, lines: [], depth: 0, complete: false, elapsed: 0, message: "Depth 14 or 10 seconds, whichever is reached first." });
    if (!this.send(positionCommand(this.position)) || !this.send("go depth 14 movetime 10000")) return;
    this.searchTimer = setTimeout(() => {
      if (this.phase !== "search") return;
      if (this.send("stop")) this.searchTimer = setTimeout(() => this.fatal("Search deadline exceeded; worker terminated."), 750);
    }, 10000);
  }
  private now() { return (this.dependencies.now || (() => performance.now()))(); }
  stop() {
    if (this.disposed) return;
    this.teardownWorker(); this.release?.(); this.release = undefined;
    this.update({ enabled: false, status: "stopped", workerAlive: false, message: "Worker terminated. Last valid estimate is retained only for this position." });
  }
  dismiss() { this.stop(); this.update({ status: "idle", lines: [], depth: 0, complete: false, error: undefined, message: undefined }); }
  dispose() { this.disposed = true; this.teardownWorker(); this.release?.(); this.release = undefined; this.view.enabled = false; }
}
