// Keeps the lobby rows (roster + evidence class) up to date. Looks up only Steam IDs it hasn't
// seen recently, so a roster update (kills, team switch) doesn't cause a new request.

import type { EvidenceClass, LobbyRow, MatchState, PlayerDetail, SteamBans } from "../shared/types";
import type { LobbyAnswer } from "./backend";

export type Lookup = (steamIds: string[]) => Promise<LobbyAnswer[]>;

interface Cached {
  classification: EvidenceClass | null;
  matchesAnalyzed: number;
  name: string | null;
  detail: PlayerDetail | null;
  bans: SteamBans | null;
  at: number;
}

const UNKNOWN_NAME = "Unknown player";

export interface LobbyOptions {
  /** Wait this long after a roster change before asking, so a filling lobby costs one request (ms). */
  debounceMs?: number;
  /** Ask again for a player after this long (ms). */
  ttlMs?: number;
  /** After a failed lookup, try again after this long (ms). */
  retryMs?: number;
  now?: () => number;
}

export class LobbyService {
  private cache = new Map<string, Cached>();
  private match: MatchState | null = null;
  private timer: NodeJS.Timeout | null = null;
  private inFlight = false;
  private failed = new Set<string>();
  error: string | null = null;
  updatedAt: string | null = null;

  constructor(private lookup: Lookup, private onChange: () => void, private opts: LobbyOptions = {}) {}

  private get now() {
    return (this.opts.now ?? Date.now)();
  }

  setMatch(match: MatchState | null): void {
    this.match = match;
    this.schedule();
    this.onChange();
  }

  /** Forget everything (e.g. after unlinking or a server change). */
  clear(): void {
    this.cache.clear();
    this.failed.clear();
    this.error = null;
    this.updatedAt = null;
    this.schedule();
    this.onChange();
  }

  rows(): LobbyRow[] {
    return (this.match?.players ?? []).map((p) => {
      if (!p.steamId) return { ...p, classification: null, matchesAnalyzed: 0, status: "no-steam-id", detail: null, bans: null };
      const c = this.cache.get(p.steamId);
      if (c)
        return {
          ...p,
          // Steam sometimes doesn't know a stranger's name yet; the server's last known name fills in.
          name: p.name === UNKNOWN_NAME && c.name ? c.name : p.name,
          classification: c.classification, matchesAnalyzed: c.matchesAnalyzed, status: "ok", detail: c.detail, bans: c.bans,
        };
      return { ...p, classification: null, matchesAnalyzed: 0, status: this.failed.has(p.steamId) ? "error" : "loading", detail: null, bans: null };
    });
  }

  private missing(): string[] {
    const ttl = this.opts.ttlMs ?? 15 * 60_000;
    const ids = new Set<string>();
    for (const p of this.match?.players ?? []) {
      if (!p.steamId) continue;
      const c = this.cache.get(p.steamId);
      if (!c || this.now - c.at > ttl) ids.add(p.steamId);
    }
    return [...ids];
  }

  private schedule(delay = this.opts.debounceMs ?? 800): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.missing().length === 0) return;
    this.timer = setTimeout(() => void this.run(), delay);
  }

  private async run(): Promise<void> {
    this.timer = null;
    if (this.inFlight) return this.schedule();
    const ids = this.missing();
    if (ids.length === 0) return;
    this.inFlight = true;
    try {
      const answers = await this.lookup(ids);
      const at = this.now;
      for (const a of answers)
        if (a.steamId) {
          this.cache.set(a.steamId, { classification: a.classification, matchesAnalyzed: a.matchesAnalyzed,
                                      name: a.name ?? null, detail: a.detail ?? null, bans: a.bans ?? null, at });
          this.failed.delete(a.steamId);
        }
      this.error = null;
      this.updatedAt = new Date(at).toISOString();
    } catch (e) {
      for (const id of ids) this.failed.add(id);
      this.error = e instanceof Error ? e.message : String(e);
      this.inFlight = false;
      this.onChange();
      this.schedule(this.opts.retryMs ?? 15_000);
      return;
    }
    this.inFlight = false;
    this.onChange();
    this.schedule();
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
  }
}
