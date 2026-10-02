import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { GepState, parseRosterEntry, sideOf, steamIdOf } from "../src/main/game/gep";
import { parseRecording } from "../src/main/game/replay";

describe("roster entries", () => {
  it("reads Overwolf's JSON string", () => {
    const p = parseRosterEntry(3, JSON.stringify({ nickname: "Brakk", steamid: "76561198000000006", team: "T", is_local: "0" }));
    expect(p).toEqual({ slot: 3, name: "Brakk", steamId: "76561198000000006", side: "T", isLocal: false });
  });

  it("keeps a player whose Steam ID the game didn't give", () => {
    const p = parseRosterEntry(0, { nickname: "Tamsin", steamid: "0", team: "CT" });
    expect(p?.steamId).toBeNull();
    expect(p?.name).toBe("Tamsin");
  });

  it("drops emptied slots and junk", () => {
    expect(parseRosterEntry(0, "")).toBeNull();
    expect(parseRosterEntry(0, "{}")).toBeNull();
    expect(parseRosterEntry(0, "not json")).toBeNull();
  });

  it("accepts several team spellings", () => {
    expect(["CT", "ct", 3, "Counter-Terrorist"].map(sideOf)).toEqual(["CT", "CT", "CT", "CT"]);
    expect(["T", 2, "TERRORIST"].map(sideOf)).toEqual(["T", "T", "T"]);
    expect(sideOf("spectator")).toBeNull();
  });

  it("only accepts SteamID64s", () => {
    expect(steamIdOf("76561198000000006")).toBe("76561198000000006");
    expect(steamIdOf(76561198000000006)).toBeNull(); // a JSON number has lost precision
    expect(steamIdOf("123")).toBeNull();
    expect(steamIdOf("BOT")).toBeNull();
  });
});

describe("GepState", () => {
  it("builds the match from updates and marks the local player", () => {
    const g = new GepState();
    g.apply({ category: "live_data", key: "provider", value: JSON.stringify({ steam_id: "76561198000000002" }) });
    g.apply({ category: "match_info", key: "roster_1", value: JSON.stringify({ nickname: "b", steamid: "76561198000000002", team: "CT" }) });
    g.apply({ category: "match_info", key: "roster_0", value: JSON.stringify({ nickname: "a", steamid: "76561198000000001", team: "T" }) });
    g.apply({ category: "match_info", key: "map", value: "de_mirage" });
    const m = g.match();
    expect(m.map).toBe("de_mirage");
    expect(m.players.map((p) => p.name)).toEqual(["a", "b"]);
    expect(m.players[1].isLocal).toBe(true);
    expect(m.localSteamId).toBe("76561198000000002");

    g.apply({ category: "match_info", key: "roster_0", value: "" });
    expect(g.match().players.map((p) => p.name)).toEqual(["b"]);
  });

  it("reads a getInfo snapshot", () => {
    const g = new GepState();
    g.applySnapshot({ info: { match_info: { roster_0: JSON.stringify({ nickname: "a", steamid: "76561198000000001" }) } } });
    expect(g.match().players).toHaveLength(1);
  });

  it("plays the bundled fixture into a full lobby", () => {
    const g = new GepState();
    for (const l of parseRecording(readFileSync("fixtures/premier-mirage.jsonl", "utf8")))
      if (l.kind === "info") g.apply(l);
    const m = g.match();
    expect(m.players).toHaveLength(10);
    expect(m.players.filter((p) => !p.steamId)).toHaveLength(1);
    expect(m.players.filter((p) => p.isLocal).map((p) => p.name)).toEqual(["Nova"]);
  });
});
