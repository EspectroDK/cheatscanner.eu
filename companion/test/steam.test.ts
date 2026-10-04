import { request } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SteamSource } from "../src/main/game/steam";
import type { CoplayEntry, CoplayResult, FriendInGame } from "../src/main/steam/coplay";
import { gsiConfig, libraryPaths, parseGsi, startGsiServer, type GameState } from "../src/main/steam/gsi";
import { expectedOthers, pickFriends, pickMatchPlayers } from "../src/main/steam/pick";
import type { MatchState } from "../src/shared/types";
import { ago, flagged } from "../src/renderer/parts";

const ME = "76561198000000000";
const id = (n: number) => String(76561198000000000n + BigInt(n));
const E = (n: number, time: number, appId = 730): CoplayEntry => ({ steamId: id(n), name: `p${n}`, appId, time });

describe("picking the match from Steam's players list", () => {
  const now = 1_800_000_000;

  it("takes the group reported together, not the newest entries (a real Dust II match)", () => {
    const entries = [
      ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => E(n, now - 62)), // the 9 on the scoreboard, "1 minute ago"
      E(20, now - 3), E(21, now - 2),                             // "Now", not in the match
      ...[30, 31, 32].map((n) => E(n, now - 18 * 60)),            // the previous match
    ];
    const r = pickMatchPlayers(entries, { now, localSteamId: ME, matchStart: now - 90 });
    expect(r.players.map((p) => p.steamId).sort()).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9].map(id).sort());
    expect(r.others.map((p) => p.name)).toEqual(["p20", "p21"]);
  });

  it("keeps players reported a few seconds apart together, and skips other games, old entries and me", () => {
    const entries = [E(1, now - 40), E(2, now - 35), E(3, now - 30), E(4, now - 400, 440), { ...E(0, now - 30), steamId: ME }, E(5, now - 3 * 3600)];
    const r = pickMatchPlayers(entries, { now, localSteamId: ME });
    expect(r.players.map((p) => p.name)).toEqual(["p1", "p2", "p3"]);
  });

  it("fills a short group with a player Steam re-reported a bit later, but not with the previous match", () => {
    const entries = [
      ...[1, 2, 3, 4, 5, 6, 7, 8].map((n) => E(n, now - 300)),  // 8 reported together
      E(9, now - 200),                                          // the 9th, re-reported later
      ...[30, 31, 32].map((n) => E(n, now - 8 * 60)),           // the previous match
    ];
    const r = pickMatchPlayers(entries, { now, localSteamId: ME });
    expect(r.players.map((p) => p.name).sort()).toEqual(["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"]);
    expect(r.others.map((p) => p.name)).toEqual(["p30", "p31", "p32"]);
  });

  it("caps a group at the players a match can have", () => {
    const entries = Array.from({ length: 12 }, (_, i) => E(i + 1, now - 60 + i));
    expect(pickMatchPlayers(entries, { now, expectedOthers: 9 }).players).toHaveLength(9);
    expect(expectedOthers("scrimcomp2v2")).toBe(3);
    expect(expectedOthers("competitive")).toBe(9);
    expect(pickMatchPlayers([], { now }).players).toEqual([]);
  });
});

describe("topping up with Steam friends", () => {
  const F = (n: number, presence: Record<string, string>): FriendInGame => ({ steamId: id(n), name: `f${n}`, presence });

  it("adds friends in our party first, then friends on the same map, up to the missing count", () => {
    const friends = [
      F(1, { "game:map": "de_mirage" }),
      F(2, { "game:map": "de_nuke", steam_player_group: "77" }),   // our party (map key differs or stale)
      F(3, { "game:map": "de_dust2" }),                            // another match
      F(4, { "game:map": "DE_MIRAGE" }),
    ];
    const opts = { map: "de_mirage", localPresence: { steam_player_group: "77" }, exclude: new Set<string>(), max: 2 };
    expect(pickFriends(friends, opts).map((f) => f.name)).toEqual(["f2", "f1"]);
    expect(pickFriends(friends, { ...opts, max: 9 }).map((f) => f.name)).toEqual(["f2", "f1", "f4"]);
    expect(pickFriends(friends, { ...opts, exclude: new Set([id(2)]) }).map((f) => f.name)).toEqual(["f1", "f4"]);
    expect(pickFriends(friends, { ...opts, max: 0 })).toEqual([]);
  });

  it("ignores an empty party id and needs a map to match on", () => {
    const friends = [F(1, { steam_player_group: "0", "game:map": "de_mirage" }), F(2, {})];
    expect(pickFriends(friends, { map: null, localPresence: { steam_player_group: "0" }, exclude: new Set(), max: 9 })).toEqual([]);
    expect(pickFriends(friends, { map: "de_mirage", exclude: new Set(), max: 9 }).map((f) => f.name)).toEqual(["f1"]);
  });
});

describe("CS2 game state feed", () => {
  it("reads map, mode, phase and our Steam ID", () => {
    expect(parseGsi({ provider: { steamid: ME }, map: { name: "de_dust2", mode: "competitive", phase: "warmup" } }))
      .toEqual({ map: "de_dust2", mode: "competitive", phase: "warmup", localSteamId: ME });
    expect(parseGsi({ provider: { steamid: "123" } })).toEqual({ map: null, mode: null, phase: null, localSteamId: null });
    expect(parseGsi(null).map).toBeNull();
  });

  it("writes a cfg with our token and finds Steam libraries", () => {
    expect(gsiConfig("abc", 1234)).toContain('"uri"        "http://127.0.0.1:1234/gsi"');
    expect(gsiConfig("abc")).toContain('"token" "abc"');
    const vdf = `"libraryfolders" { "0" { "path" "C:\\\\Program Files (x86)\\\\Steam" } "1" { "path" "D:\\\\SteamLibrary" } }`;
    expect(libraryPaths(vdf)).toEqual(["C:\\Program Files (x86)\\Steam", "D:\\SteamLibrary"]);
  });

  it("accepts only payloads with our token", async () => {
    const got: GameState[] = [];
    const port = 37000 + Math.floor(Math.random() * 1000);
    const server = startGsiServer("secret-token", (s) => got.push(s), port);
    await new Promise((r) => server.once("listening", r));
    const post = (body: unknown) => new Promise<void>((resolve) => {
      const req = request({ host: "127.0.0.1", port, path: "/gsi", method: "POST" }, (res) => { res.resume(); res.on("end", resolve); });
      req.end(JSON.stringify(body));
    });
    await post({ auth: { token: "wrong" }, map: { name: "de_nuke" } });
    await post({ auth: { token: "secret-token" }, map: { name: "de_mirage", phase: "live" } });
    server.close();
    expect(got.map((g) => g.map)).toEqual(["de_mirage"]);
  });
});

describe("SteamSource", () => {
  beforeEach(() => vi.useFakeTimers({ now: 1_800_000_000_000 }));
  afterEach(() => vi.useRealTimers());

  function make(entries: () => CoplayEntry[], friends: () => FriendInGame[] = () => []) {
    let push: (s: GameState) => void = () => {};
    const scans: number[] = [];
    const src = new SteamSource({
      scan: async (): Promise<CoplayResult> => {
        scans.push(Date.now());
        return { localName: "Nova", entries: entries(), friends: friends() };
      },
      listen: (on) => ((push = on), () => {}),
      fastMs: 1000,
      slowMs: 10_000,
    });
    const matches: MatchState[] = [];
    const problems: (string | null)[] = [];
    src.on("match", (m) => matches.push(m));
    src.on("problem", (p) => problems.push(p));
    return { src, push: (s: Partial<GameState>) => push({ map: null, mode: null, phase: null, localSteamId: ME, ...s }), scans, matches, problems };
  }

  it("lists me and the match's players once a map loads, and forgets them back in the menu", async () => {
    const t = () => Math.floor(Date.now() / 1000);
    const { src, push, matches } = make(() => [1, 2, 3].map((n) => E(n, t() - 5)));
    src.start();
    push({});                                   // CS2 at the main menu
    await vi.advanceTimersByTimeAsync(100);
    expect(matches.at(-1)?.players).toEqual([]);

    push({ map: "de_dust2", mode: "competitive", phase: "warmup" });
    await vi.advanceTimersByTimeAsync(2_000);
    const m = matches.at(-1)!;
    expect(m).toMatchObject({ map: "de_dust2", phase: "warmup", localSteamId: ME });
    expect(m.players.map((p) => p.name)).toEqual(["Nova", "p1", "p2", "p3"]);
    expect(m.players[0].isLocal).toBe(true);

    push({ map: "de_dust2", mode: "competitive", phase: "live" });
    await vi.advanceTimersByTimeAsync(10);
    expect(matches.at(-1)?.phase).toBe("live");

    push({});
    await vi.advanceTimersByTimeAsync(10);
    expect(matches.at(-1)?.players).toEqual([]);
    src.stop();
  });

  it("keeps a player found earlier in the match when a later scan misses them", async () => {
    const t = () => Math.floor(Date.now() / 1000);
    let list = [1, 2, 3].map((n) => E(n, t() - 5));
    const { src, push, matches } = make(() => list);
    src.start();
    push({ map: "de_dust2", mode: "competitive", phase: "warmup" });
    await vi.advanceTimersByTimeAsync(2_000);
    list = [1, 2].map((n) => E(n, t() - 1)).concat(E(3, t() - 30 * 60));
    await vi.advanceTimersByTimeAsync(1_100);
    expect(matches.at(-1)!.players.map((p) => p.name)).toEqual(["Nova", "p1", "p2", "p3"]);
    src.stop();
  });

  it("adds a friend in the match that Steam's players list leaves out", async () => {
    const t = () => Math.floor(Date.now() / 1000);
    const { src, push, matches } = make(
      () => [1, 2, 3, 4, 5, 6, 7, 8].map((n) => E(n, t() - 5)),
      () => [{ steamId: id(50), name: "buddy", presence: { "game:map": "de_mirage" } },
             { steamId: id(51), name: "elsewhere", presence: { "game:map": "de_inferno" } },
             { steamId: id(3), name: "p3", presence: { "game:map": "de_mirage" } }],
    );
    src.start();
    push({ map: "de_mirage", mode: "competitive", phase: "warmup" });
    await vi.advanceTimersByTimeAsync(2_000);
    expect(matches.at(-1)!.players.map((p) => p.name)).toEqual(["Nova", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "buddy"]);
    src.stop();
  });

  it("scans often until the lobby is complete, and reports Steam problems", async () => {
    const t = () => Math.floor(Date.now() / 1000);
    let fail = true;
    const { src, push, scans, problems } = make(() => {
      if (fail) throw new Error("Steam isn't running");
      return Array.from({ length: 9 }, (_, i) => E(i + 1, t() - 5));
    });
    src.start();
    push({ map: "de_dust2", mode: "competitive", phase: "warmup" });
    await vi.advanceTimersByTimeAsync(1_600);
    expect(problems.at(-1)).toMatch(/Steam isn't running/);
    fail = false;
    await vi.advanceTimersByTimeAsync(1_100);
    expect(problems.at(-1)).toBeNull();
    const n = scans.length;
    await vi.advanceTimersByTimeAsync(5_000);   // complete: slow interval now
    expect(scans.length).toBe(n);
    src.stop();
  });
});

describe("overlay helpers", () => {
  it("orders flagged players HIGH first, strongest evidence first", () => {
    const d = (score: number) => ({ evidenceScore: score, highEvidenceMatches: 0, axes: { wallTracking: "LOW", aim: "LOW", reaction: "LOW" } as const, recent: [] });
    const row = (name: string, classification: "HIGH" | "ELEVATED" | "NORMAL", score: number | null) =>
      ({ slot: 0, name, steamId: name, side: null, isLocal: false, classification, matchesAnalyzed: 3, status: "ok" as const, detail: score === null ? null : d(score) });
    expect(flagged([row("a", "ELEVATED", 40), row("b", "HIGH", 70), row("c", "HIGH", 91), row("d", "NORMAL", null)]).map((r) => r.name))
      .toEqual(["c", "b", "a"]);
  });

  it("writes dates like the mock-up", () => {
    const now = Date.parse("2026-09-29T12:00:00Z");
    expect(ago("2026-09-26T10:00:00Z", now)).toBe("3 days ago");
    expect(ago("2026-09-28T10:00:00Z", now)).toBe("yesterday");
    expect(ago(null, now)).toBe("date unknown");
  });
});
