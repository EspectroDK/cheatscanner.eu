// Picks the current match's players out of Steam's "recently played with" list.
//
// Seen on a real PC (2026-09-29, Competitive Dust II, start of the match): all 9 other players on the
// scoreboard were listed with one shared time, plus 2 newer entries ("Now") that were NOT in the match.
// So the newest entries aren't enough: the match is the biggest group of players reported together.

import { CS2_APP_ID, type CoplayEntry } from "./coplay";

export interface PickOptions {
  /** Unix seconds now. */
  now: number;
  /** Our own SteamID64, left out of the list. */
  localSteamId?: string | null;
  /** Unix seconds when the current map loaded (from the game's state feed). Unknown: null. */
  matchStart?: number | null;
  /** Other players in this mode (9 in Competitive/Premier, 3 in Wingman). */
  expectedOthers?: number;
  /** Entries reported this close together (s) count as one group. */
  groupGapS?: number;
  /** Without a known match start, only look this far back (s). */
  recentS?: number;
}

export interface PickResult {
  players: CoplayEntry[];
  /** Other recent CS2 entries that didn't make it into the match group (shown nowhere; kept for logs). */
  others: CoplayEntry[];
}

export function pickMatchPlayers(entries: CoplayEntry[], o: PickOptions): PickResult {
  const expected = o.expectedOthers ?? 9;
  const gap = o.groupGapS ?? 20;
  // Steam can report players a little before the game-state feed tells us the map loaded.
  const since = o.matchStart != null ? o.matchStart - 180 : o.now - (o.recentS ?? 15 * 60);
  const seen = new Set<string>();
  const recent = entries
    .filter((e) => e.appId === CS2_APP_ID && e.steamId !== o.localSteamId && e.time >= since && e.time <= o.now + 60)
    .filter((e) => (seen.has(e.steamId) ? false : (seen.add(e.steamId), true)))
    .sort((a, b) => a.time - b.time);

  const groups: CoplayEntry[][] = [];
  for (const e of recent) {
    const g = groups.at(-1);
    if (g && e.time - g[g.length - 1].time <= gap) g.push(e);
    else groups.push([e]);
  }
  if (groups.length === 0) return { players: [], others: [] };

  // Biggest group wins; on a tie the newer one (a new match beats the previous one).
  let best = groups[0];
  for (const g of groups) if (g.length >= best.length) best = g;
  // A group bigger than a match holds late reports too; the roster is the first ones reported.
  const players = best.slice(0, expected);
  // Steam sometimes re-reports a player later (seen on a real PC: 8 of 9 in the group, one apart).
  // Fill a short group with the entries closest in time, never ones from before this match's group.
  const start = best[0].time;
  const floor = o.matchStart != null ? Math.max(o.matchStart - 180, start - 120) : start - 120;
  const inGroup = new Set(players.map((p) => p.steamId));
  const fill = recent
    .filter((e) => !inGroup.has(e.steamId) && e.time >= floor)
    .sort((a, b) => Math.abs(a.time - start) - Math.abs(b.time - start));
  players.push(...fill.slice(0, Math.max(0, expected - players.length)));
  const picked = new Set(players.map((p) => p.steamId));
  return { players, others: recent.filter((e) => !picked.has(e.steamId)) };
}

/** Other players in a mode, from the game-state feed's map.mode. */
export function expectedOthers(mode: string | null | undefined): number {
  const m = (mode ?? "").toLowerCase();
  if (m.includes("wingman") || m.includes("scrimcomp2v2")) return 3;
  if (m.includes("casual") || m.includes("deathmatch") || m.includes("armsrace")) return 19;
  return 9;
}
