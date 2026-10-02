// Runs in its own utility process (see main.ts): reads Steam's players list once and reports back.
// Kept out of the app's main process so a Steam client quirk can't take the app down.

import { readCoplay } from "./coplay";

const port = (process as unknown as { parentPort: { on(e: "message", f: () => void): void; postMessage(m: unknown): void } }).parentPort;

port.on("message", () => {
  try {
    port.postMessage({ ok: true, result: readCoplay() });
  } catch (e) {
    port.postMessage({ ok: false, error: e instanceof Error ? e.message : String(e) });
  }
});
