// Saves what Overwolf reports during a session to a JSON Lines file that ReplaySource can play back.
// Off by default; started with `--record`. The file stays on this PC (it holds the lobby's names and
// Steam IDs) and is meant for pinning down Overwolf's exact CS2 data on the first real match.

import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import type { GameSource, RecordedLine } from "./source";

export function record(source: GameSource, dir: string): string {
  mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const file = join(dir, `cs2-session-${stamp}.jsonl`);
  source.on("line", (line: RecordedLine) => appendFileSync(file, JSON.stringify(line) + "\n"));
  return file;
}
