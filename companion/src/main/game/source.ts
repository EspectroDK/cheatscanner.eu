// Where live match data comes from. The default is SteamSource (Steam's players list plus CS2's game state
// feed, no Overwolf); OverwolfSource uses Overwolf's CS2 game events; the replay source plays a recorded
// (or hand-written) session so the app can be tried without CS2.

import { EventEmitter } from "node:events";
import type { GameSourceKind, MatchState } from "../../shared/types";
import { GepState, type InfoUpdate } from "./gep";

export const CS2_GAME_ID = 22730;

/** One line of a recording (JSON Lines). `t` is milliseconds since the recording started. */
export type RecordedLine =
  | { t: number; kind: "info"; feature?: string; category: string; key: string; value: unknown }
  | { t: number; kind: "running"; value: boolean };

export interface GameSourceEvents {
  match: [MatchState];
  running: [boolean];
  problem: [string | null];
  line: [RecordedLine];
}

export abstract class GameSource extends EventEmitter<GameSourceEvents> {
  abstract readonly kind: GameSourceKind;
  protected state = new GepState();
  private started = Date.now();
  private pending = false;

  abstract start(): void;
  abstract stop(): void;

  /** Ask for fresh data now (e.g. after a hotkey). Sources that are pushed to can ignore it. */
  refresh(): void {}

  protected currentMatch(): MatchState {
    return this.state.match();
  }

  protected setRunning(running: boolean): void {
    this.emit("line", { t: Date.now() - this.started, kind: "running", value: running });
    if (!running) this.state.reset();
    this.emit("running", running);
    this.scheduleMatch();
  }

  protected applyInfo(u: InfoUpdate): void {
    this.emit("line", { t: Date.now() - this.started, kind: "info", feature: u.feature, category: u.category, key: u.key, value: u.value });
    if (this.state.apply(u)) this.scheduleMatch();
  }

  /** Several roster updates usually arrive together; tell listeners once. */
  protected scheduleMatch(): void {
    if (this.pending) return;
    this.pending = true;
    setImmediate(() => {
      this.pending = false;
      this.emit("match", this.currentMatch());
    });
  }
}
