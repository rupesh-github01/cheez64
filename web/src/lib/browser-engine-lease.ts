const LOCK_NAME = "pcis-browser-stockfish-v1";
let localOwner = false;
export const browserLocksSupported = () => typeof navigator !== "undefined" && !!navigator.locks?.request;
export async function acquireBrowserEngine(allowUncoordinated = false): Promise<() => void> {
  if (localOwner) throw new Error("Live analysis is already active in this tab.");
  if (!browserLocksSupported()) {
    if (!allowUncoordinated) throw new Error("Web Locks unavailable. Close other analysis tabs and explicitly accept tab-only exclusion.");
    localOwner = true; return () => { localOwner = false; };
  }
  return new Promise((resolve, reject) => {
    navigator.locks.request(LOCK_NAME, { mode: "exclusive", ifAvailable: true }, lock => {
      if (!lock) { reject(new Error("Live analysis is active in another tab. Stop it there first.")); return; }
      localOwner = true;
      return new Promise<void>(release => resolve(() => { localOwner = false; release(); }));
    }).catch(reject);
  });
}
export async function assertNoBrowserEngine() {
  const held = browserLocksSupported() ? (await navigator.locks.query()).held?.some(lock => lock.name === LOCK_NAME) : false;
  if (localOwner || held) throw new Error("Stop browser Live Analysis before queueing backend engine analysis.");
}
