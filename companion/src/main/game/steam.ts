// Live match data without Overwolf:
//   - CS2's Game State Integration feed (gsi.ts) says when a map loads, the mode, warm-up vs live, and
//     our own Steam ID;
//   - Steam's "recently played with" list (coplay.ts, read in a separate process) gives the other
//     players' Steam IDs, reported by CS2 when the match loads. It has no teams.
// Nothing here opens, reads or changes the CS2 process.

import type { MatchState, RosterPlayer } from "../../shared/types";
import type { CoplayResult } from "../steam/coplay";
import type { GameState } from "../steam/gsi";
import { expectedOthers, pickMatchPlayers } from "../steam/pick";
import { GameSource } from "./source";

export interface SteamSourceDeps {
  /** Reads Steam's players list (in a separate process in the app). */
  scan: () => Promise<CoplayResult>;
  /** Starts the game-state listener; returns a stop function. */
  listen: (onState: (s: GameState) => void) => () => void;
  /** Why game-state data may be missing (e.g. CS2's folder wasn't found), or null. */
  gsiProblem?: string | null;
  /** Our Steam ID from the linked account, used until the game reports it. */
  localSteamId?: () => string | null;
  now?: () => number;
  /** Scan intervals (ms). */
  fastMs?: number;
  slowMs?: number;
}

const GSI_SILENT_MS = 35_000; // CS2 sends a heartbeat every 10 s while running.

export class SteamSource extends GameSource {
  readonly kind = "steam" as const;
  private game: GameState | null = null;
  private gsiAt = 0;
  private matchStart: number | null = null;
  private players: RosterPlayer[] = [];
  /** Coplay time (s) of the current match's group; null until found. */
  private groupStart: number | null = null;
  private localName: string | null = null;
  private stopListening: (() => void) | null = null;
  private timer: NodeJS.Timeout | null = null;
  private watchdog: NodeJS.Timeout | null = null;
  private scanning = false;
  private running = false;
  private lastProblem: string | null | undefined = undefined;

  constructor(private deps: SteamSourceDeps) {
    super();
  }

  private get now() {
    return (this.deps.now ?? Date.now)();
  }

  start(): void {
    this.stopListening = this.deps.listen((s) => this.onGameState(s));
    this.watchdog = setInterval(() => this.checkRunning(), 5_000);
    this.setProblem(this.deps.gsiProblem ?? null);
    this.schedule(0);
  }

  stop(): void {
    this.stopListening?.();
    if (this.timer) clearTimeout(this.timer);
    if (this.watchdog) clearInterval(this.watchdog);
    this.timer = this.watchdog = null;
  }

  refresh(): void {
    this.schedule(0);
  }

  private get gsiLive(): boolean {
    return this.gsiAt > 0 && this.now - this.gsiAt < GSI_SILENT_MS;
  }

  private onGameState(s: GameState): void {
    const prevMap = this.game?.map ?? null;
    this.gsiAt = this.now;
    this.game = s;
    if (!this.running) this.setGameRunning(true);
    if (s.map !== prevMap) {
      // A new map (or back to the menu): forget the previous match's players.
      this.matchStart = s.map ? this.now : null;
      this.players = [];
      this.groupStart = null;
      if (s.map) this.schedule(1_500);
    }
    this.scheduleMatch();
  }

  private checkRunning(): void {
    if (this.running && this.gsiAt > 0 && !this.gsiLive) {
      // CS2 closed.
      this.game = null;
      this.matchStart = null;
      this.players = [];
      this.setGameRunning(false);
    }
  }

  private setGameRunning(running: boolean): void {
    this.running = running;
    this.emit("running", running);
    this.scheduleMatch();
  }

  private setProblem(p: string | null): void {
    if (p === this.lastProblem) return;
    this.lastProblem = p;
    this.emit("problem", p);
  }

  /** In a map by the game's own report; without the game-state feed, assume yes and rely on recency. */
  private get inMap(): boolean {
    return this.gsiLive ? !!this.game?.map : this.gsiAt === 0;
  }

  private schedule(delay: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.scan(), delay);
  }

  private nextDelay(): number {
    const fast = this.deps.fastMs ?? 8_000, slow = this.deps.slowMs ?? 60_000;
    if (!this.inMap) return slow;
    const complete = this.players.length >= expectedOthers(this.game?.mode);
    if (!this.gsiLive) return 20_000;
    return complete ? slow : fast;
  }

  private async scan(): Promise<void> {
    this.timer = null;
    if (this.scanning) return;
    if (!this.inMap) return this.schedule(this.nextDelay());
    this.scanning = true;
    try {
      const res = await this.deps.scan();
      this.localName = res.localName;
      const local = this.game?.localSteamId ?? this.deps.localSteamId?.() ?? null;
      const picked = pickMatchPlayers(res.entries, {
        now: Math.floor(this.now / 1000),
        localSteamId: local,
        matchStart: this.matchStart != null ? Math.floor(this.matchStart / 1000) : null,
        expectedOthers: expectedOthers(this.game?.mode),
      });
      if (this.inMap) {
        // Without the game's own map report, a group starting well after the previous one is a new match.
        const groupStart = Math.min(...picked.players.map((e) => e.time));
        if (!this.gsiLive && this.groupStart != null && groupStart - this.groupStart > 600) this.players = [];
        if (picked.players.length) this.groupStart = groupStart;
        // Players stay for the whole match once seen, so a later scan can't drop one.
        const byId = new Map(this.players.map((p) => [p.steamId, p]));
        for (const e of picked.players)
          byId.set(e.steamId, { slot: 0, name: e.name ?? byId.get(e.steamId)?.name ?? "Unknown player", steamId: e.steamId, side: null, isLocal: false });
        this.players = [...byId.values()].slice(0, expectedOthers(this.game?.mode) + 2).map((p, i) => ({ ...p, slot: i + 1 }));
      }
      // Players found but CS2 never sent game state: it was most likely started before the cfg existed.
      const noGsi = this.gsiAt === 0 && this.players.length > 0
        ? "CS2 isn't sending game state, so the overlay can't hide itself when the match goes live. Restart CS2 once."
        : null;
      this.setProblem(this.gsiAt === 0 ? this.deps.gsiProblem ?? noGsi : null);
      this.scheduleMatch();
    } catch (e) {
      this.setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      this.scanning = false;
      this.schedule(this.nextDelay());
    }
  }

  protected currentMatch(): MatchState {
    const localSteamId = this.game?.localSteamId ?? this.deps.localSteamId?.() ?? null;
    if (!this.inMap || this.players.length === 0)
      return { map: this.game?.map ?? null, mode: this.game?.mode ?? null, phase: this.game?.phase ?? null, localSteamId, players: [] };
    const me: RosterPlayer = { slot: 0, name: this.localName ?? "You", steamId: localSteamId, side: null, isLocal: true };
    return {
      map: this.game?.map ?? null,
      mode: this.game?.mode ?? null,
      phase: this.game?.phase ?? null,
      localSteamId,
      players: [me, ...this.players],
    };
  }
}
