// Plays a recorded session (JSON Lines, see RecordedLine) as if CS2 were running. Used for development
// before Overwolf's approval, for tests, and to replay your own recordings (see recorder.ts).

import { readFileSync } from "node:fs";
import type { InfoUpdate } from "./gep";
import { GameSource, type RecordedLine } from "./source";

export function parseRecording(text: string): RecordedLine[] {
  const lines: RecordedLine[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const s = raw.trim();
    if (!s || s.startsWith("//")) continue;
    const o = JSON.parse(s);
    if (typeof o.t !== "number") throw new Error(`recording line without a time: ${s.slice(0, 80)}`);
    if (o.kind === "running" || o.kind === "info") lines.push(o);
  }
  return lines.sort((a, b) => a.t - b.t);
}

export interface ReplayOptions {
  /** 2 = twice as fast. */
  speed?: number;
  /** Start again this long after the last line (ms); 0 = play once. */
  loopPauseMs?: number;
}

export class ReplaySource extends GameSource {
  readonly kind = "replay" as const;
  private timers: NodeJS.Timeout[] = [];

  constructor(private lines: RecordedLine[], private opts: ReplayOptions = {}) {
    super();
  }

  static fromFile(path: string, opts?: ReplayOptions): ReplaySource {
    return new ReplaySource(parseRecording(readFileSync(path, "utf8")), opts);
  }

  start(): void {
    const speed = this.opts.speed ?? 1;
    const last = this.lines.at(-1)?.t ?? 0;
    for (const line of this.lines) {
      this.timers.push(setTimeout(() => this.play(line), line.t / speed));
    }
    const pause = this.opts.loopPauseMs ?? 0;
    if (pause > 0)
      this.timers.push(setTimeout(() => {
        this.timers = [];
        this.setRunning(false);
        this.start();
      }, last / speed + pause));
  }

  stop(): void {
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
  }

  private play(line: RecordedLine): void {
    if (line.kind === "running") this.setRunning(line.value);
    else this.applyInfo(line as InfoUpdate);
  }
}
