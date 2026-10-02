// Turns Overwolf Game Events Provider (GEP) info updates for CS2 into a MatchState.
//
// Known from Overwolf's CS2 docs and samples (researched 2026-09-27):
//   match_info.roster_0 .. roster_N  JSON strings: nickname, steamid, team, is_local, kills, ...
//   live_data.provider              JSON with steam_id of the local player
// NOT yet confirmed on a live Competitive/Premier match: whether other players' steamid is filled in
// (Overwolf hints "roster not supported on trusted mode"), the exact team values, and the keys for
// map and mode. So everything here accepts several shapes and treats a missing Steam ID as normal.
// The recorder (recorder.ts) saves the real updates from a first real match so this can be pinned down.

import type { MatchState, RosterPlayer, Side } from "../../shared/types";

export interface InfoUpdate {
  feature?: string;
  category: string;
  key: string;
  value: unknown;
}

const MAP_KEYS = ["map", "map_name", "mapname"];
const MODE_KEYS = ["mode", "game_mode", "gamemode", "match_type"];
const PHASE_KEYS = ["phase", "game_phase", "map_phase"];

export class GepState {
  private info = new Map<string, Map<string, unknown>>();

  reset(): void {
    this.info.clear();
  }

  /** Applies one update. Returns true when the derived MatchState may have changed. */
  apply(u: InfoUpdate): boolean {
    if (!u || typeof u.category !== "string" || typeof u.key !== "string") return false;
    let cat = this.info.get(u.category);
    if (!cat) this.info.set(u.category, (cat = new Map()));
    if (u.value === null || u.value === undefined || u.value === "") cat.delete(u.key);
    else cat.set(u.key, u.value);
    return true;
  }

  /** Applies the object gep.getInfo(gameId) returns: { category: { key: value } } (possibly under .info). */
  applySnapshot(snapshot: unknown): void {
    const root = asObject(snapshot);
    const info = asObject(root?.info) ?? root;
    if (!info) return;
    for (const [category, keys] of Object.entries(info)) {
      const obj = asObject(keys);
      if (obj) for (const [key, value] of Object.entries(obj)) this.apply({ category, key, value });
    }
  }

  match(): MatchState {
    const matchInfo = this.info.get("match_info") ?? new Map();
    const players: RosterPlayer[] = [];
    for (const [key, value] of matchInfo) {
      const m = /^roster_(\d+)$/.exec(key);
      if (!m) continue;
      const p = parseRosterEntry(Number(m[1]), value);
      if (p) players.push(p);
    }
    players.sort((a, b) => a.slot - b.slot);

    const provider = asObject(parseJson(this.info.get("live_data")?.get("provider")));
    const localFromProvider = steamIdOf(provider?.steam_id ?? provider?.steamid);
    const localFromRoster = players.find((p) => p.isLocal)?.steamId ?? null;
    const localSteamId = localFromProvider ?? localFromRoster;
    if (localSteamId) for (const p of players) if (p.steamId === localSteamId) p.isLocal = true;

    const phase = this.firstString(PHASE_KEYS)?.toLowerCase();
    return {
      map: this.firstString(MAP_KEYS),
      mode: this.firstString(MODE_KEYS),
      phase: phase === "warmup" || phase === "live" || phase === "intermission" || phase === "gameover" ? phase : null,
      localSteamId,
      players,
    };
  }

  private firstString(keys: string[]): string | null {
    for (const cat of ["match_info", "game_info"]) {
      const c = this.info.get(cat);
      for (const k of keys) {
        const v = c?.get(k);
        if (typeof v === "string" && v.trim()) return v.trim();
      }
    }
    return null;
  }
}

export function parseRosterEntry(slot: number, value: unknown): RosterPlayer | null {
  const o = asObject(parseJson(value));
  if (!o) return null;
  const name = String(o.nickname ?? o.name ?? "").trim();
  const steamId = steamIdOf(o.steamid ?? o.steam_id ?? o.steamId);
  if (!name && !steamId) return null; // an emptied slot
  return {
    slot,
    name: name || "Unknown player",
    steamId,
    side: sideOf(o.team ?? o.side),
    isLocal: truthy(o.is_local ?? o.isLocal ?? o.local),
  };
}

/** A SteamID64 (17 digits, starts with 7656) or null. "0", "", "BOT" and account ids are not usable.
 *  A SteamID64 sent as a JSON number has already lost its last digits (too big for a double), so only strings count. */
export function steamIdOf(v: unknown): string | null {
  const s = typeof v === "string" ? v.trim() : "";
  return /^7656\d{13}$/.test(s) ? s : null;
}

export function sideOf(v: unknown): Side | null {
  const s = String(v ?? "").trim().toUpperCase();
  if (s === "CT" || s === "3" || s.includes("COUNTER")) return "CT";
  if (s === "T" || s === "2" || s.includes("TERROR")) return "T";
  return null;
}

function truthy(v: unknown): boolean {
  return v === true || v === 1 || v === "1" || v === "true" || v === "True";
}

function parseJson(v: unknown): unknown {
  if (typeof v !== "string") return v;
  const s = v.trim();
  if (!s.startsWith("{") && !s.startsWith("[")) return v;
  try {
    return JSON.parse(s);
  } catch {
    return v;
  }
}

function asObject(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}
