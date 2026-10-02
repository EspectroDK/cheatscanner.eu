import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Controller, type ControllerDeps, type Settings } from "../src/main/controller";
import { ReplaySource } from "../src/main/game/replay";
import { GameSource, type RecordedLine } from "../src/main/game/source";
import type { EvidenceClass, MatchState } from "../src/shared/types";

/** A fake Cheatscanner server with the companion endpoints. */
function fakeServer() {
  const s = { confirmed: false, tokens: new Set<string>(), lookups: [] as string[][], authEnabled: true,
              classes: {} as Record<string, EvidenceClass> };
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fetchImpl = (async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const auth = new Headers(init?.headers).get("Authorization")?.replace("Bearer ", "");
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    if (path === "/site-info") return json(200, { name: "Cheatscanner", domain: "cheatscanner.eu", publicUrl: "https://cheatscanner.eu", authEnabled: s.authEnabled });
    if (path === "/companion/pair")
      return json(201, { deviceCode: "device-secret-000000000000", userCode: "K7QF-M2XP", verifyUrl: "https://cheatscanner.eu/#/link?code=K7QF-M2XP", expiresAt: new Date(Date.now() + 900_000).toISOString(), interval: 1 });
    if (path === "/companion/pair/token") {
      if (!s.confirmed) return json(202, { status: "PENDING" });
      s.tokens.add("tok");
      return json(200, { status: "LINKED", token: "tok", user: { steamId: "76561198000000001", personaName: "Nova", avatarUrl: null } });
    }
    if (path === "/me") return auth && s.tokens.has(auth) ? json(200, { id: 1, steamId: "76561198000000001", personaName: "Nova", avatarUrl: null }) : json(401, { detail: "sign in" });
    if (path === "/lobby/risk") {
      if (s.authEnabled && !(auth && s.tokens.has(auth))) return json(401, { detail: "sign in" });
      s.lookups.push(body.players.map((p: { steamId: string }) => p.steamId));
      return json(200, { players: body.players.map((p: { steamId: string }) => ({ steamId: p.steamId, classification: s.classes[p.steamId] ?? "ELEVATED", matchesAnalyzed: 4 })) });
    }
    return json(404, { detail: "not found" });
  }) as typeof fetch;
  return { s, fetchImpl };
}

const LINES: RecordedLine[] = [
  { t: 0, kind: "running", value: true },
  { t: 10, kind: "info", category: "match_info", key: "roster_0", value: JSON.stringify({ nickname: "a", steamid: "76561198000000001", team: "CT", is_local: "1" }) },
  { t: 10, kind: "info", category: "match_info", key: "roster_1", value: JSON.stringify({ nickname: "b", steamid: "76561198000000002", team: "T" }) },
];

/** A source the test drives directly. */
class FakeSource extends GameSource {
  readonly kind = "steam" as const;
  refreshed = 0;
  start() {}
  stop() {}
  refresh() {
    this.refreshed++;
  }
  send(m: Partial<MatchState>) {
    this.emit("match", { map: "de_dust2", mode: "competitive", phase: "warmup", localSteamId: null, players: [], ...m });
  }
}

const P = (n: number, name = `p${n}`) => ({ slot: n, name, steamId: String(76561198000000000n + BigInt(n)), side: null, isLocal: n === 0 });

function setup(settings: Settings = {}, source: GameSource = new ReplaySource(LINES), extra: Partial<ControllerDeps> = {}) {
  const server = fakeServer();
  let saved: Settings = settings;
  const opened: string[] = [];
  const c = new Controller({
    version: "test",
    source,
    store: { load: () => saved, save: (x) => (saved = x) },
    serverUrl: "http://localhost:8000",
    deviceName: "GAMING-PC",
    openExternal: (u) => opened.push(u),
    overlay: { hotkey: "Shift+F2", detailHotkey: "F7", mode: "window", visible: false, view: "lobby", siren: true },
    fetchImpl: server.fetchImpl,
    lobby: { debounceMs: 5 },
    ...extra,
  });
  return { c, server, opened, saved: () => saved };
}

describe("Controller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("links by code, then looks up the lobby", async () => {
    const { c, server, opened, saved } = setup();
    c.start();
    await vi.advanceTimersByTimeAsync(50);
    expect(c.state.server).toMatchObject({ reachable: true, domain: "cheatscanner.eu", authEnabled: true });
    expect(c.state.lobby.rows).toHaveLength(2);
    expect(c.state.lobby.error).toMatch(/Link the app/);
    expect(server.s.lookups).toHaveLength(0);

    await c.startLink();
    expect(c.state.pairing?.userCode).toBe("K7QF-M2XP");
    expect(opened).toEqual(["https://cheatscanner.eu/#/link?code=K7QF-M2XP"]);
    await vi.advanceTimersByTimeAsync(1100);
    expect(c.state.account).toBeNull(); // not confirmed yet

    server.s.confirmed = true;
    await vi.advanceTimersByTimeAsync(1100);
    expect(c.state.pairing).toBeNull();
    expect(c.state.account?.personaName).toBe("Nova");
    expect(saved().token).toBe("tok");

    await vi.advanceTimersByTimeAsync(50);
    expect(server.s.lookups).toEqual([["76561198000000001", "76561198000000002"]]);
    expect(c.state.lobby.rows.map((r) => r.classification)).toEqual(["ELEVATED", "ELEVATED"]);
    c.stop();
  });

  it("drops a token the website revoked", async () => {
    const { c, saved } = setup({ token: "revoked", account: { steamId: "76561198000000001", personaName: "M", avatarUrl: null } });
    c.start();
    await vi.advanceTimersByTimeAsync(50);
    expect(c.state.account).toBeNull();
    expect(c.state.notice).toMatch(/removed on the website/);
    expect(saved().token).toBeNull();
    c.stop();
  });

  it("works without linking against a local server without accounts", async () => {
    const { c, server } = setup();
    server.s.authEnabled = false;
    c.start();
    await vi.advanceTimersByTimeAsync(50);
    expect(server.s.lookups).toHaveLength(1);
    expect(c.state.lobby.rows[0].classification).toBe("ELEVATED");
    c.stop();
  });

  it("builds website links only for Steam IDs", async () => {
    const { c } = setup();
    c.start();
    await vi.advanceTimersByTimeAsync(20);
    expect(c.playerUrl("76561198000000002")).toBe("https://cheatscanner.eu/#/players/76561198000000002");
    expect(c.playerUrl("../evil")).toBeNull();
    c.stop();
  });

  it("shows the overlay in warm-up, hides it when the match goes live, and hotkeys bring it back", async () => {
    const src = new FakeSource();
    const { c, server } = setup({}, src);
    server.s.authEnabled = false;
    c.start();
    await vi.advanceTimersByTimeAsync(20);
    expect(c.state.overlay.visible).toBe(false);

    src.send({ players: [P(0), P(1), P(2)] });
    expect(c.state.overlay).toMatchObject({ visible: true, view: "lobby" });

    src.send({ phase: "live", players: [P(0), P(1), P(2)] });
    expect(c.state.overlay.visible).toBe(false);
    src.send({ phase: "live", players: [P(0), P(1), P(2)] }); // stays hidden on later updates
    expect(c.state.overlay.visible).toBe(false);

    c.toggleDetail();
    expect(c.state.overlay).toMatchObject({ visible: true, view: "detail" });
    c.toggleOverlay(); // switches to the list instead of hiding
    expect(c.state.overlay).toMatchObject({ visible: true, view: "lobby" });
    expect(src.refreshed).toBe(1);
    c.toggleOverlay();
    expect(c.state.overlay.visible).toBe(false);
    c.stop();
  });

  it("raises the siren once per HIGH player per match, unless turned off", async () => {
    const src = new FakeSource();
    const { c, server, saved } = setup({}, src);
    server.s.authEnabled = false;
    server.s.classes[P(2).steamId] = "HIGH";
    c.start();
    await vi.advanceTimersByTimeAsync(20);

    src.send({ players: [P(0), P(1), P(2)] });
    await vi.advanceTimersByTimeAsync(900);
    expect(c.state.alert).toEqual({ seq: 1, names: ["p2"] });

    src.send({ players: [P(0), P(1), P(2), P(3)] }); // more players found: no second siren for p2
    await vi.advanceTimersByTimeAsync(900);
    expect(c.state.alert.seq).toBe(1);

    src.send({ map: null, players: [] }); // back to the menu, then a new match with the same player
    src.send({ map: "de_nuke", players: [P(0), P(2)] });
    await vi.advanceTimersByTimeAsync(20);
    expect(c.state.alert.seq).toBe(2);

    c.setSiren(false);
    expect(saved().siren).toBe(false);
    src.send({ map: null, players: [] });
    src.send({ map: "de_inferno", players: [P(0), P(2)] });
    await vi.advanceTimersByTimeAsync(20);
    expect(c.state.alert.seq).toBe(2);
    c.testSiren();
    expect(c.state.alert.seq).toBe(3);
    c.stop();
  });

  it("changes the overlay hotkeys, and keeps the old ones when a new one can't be used", () => {
    const applied: string[] = [];
    const taken = new Set(["Ctrl+F9"]);
    const applyHotkeys = (h: { lobby: string; detail: string }) => {
      applied.push(`${h.lobby} ${h.detail}`);
      return taken.has(h.lobby) || taken.has(h.detail) ? "Ctrl+F9 is already used by another program." : null;
    };
    const { c, saved } = setup({}, undefined, { applyHotkeys });
    c.start();
    expect(applied).toEqual(["Shift+F2 F7"]);
    expect(c.state.hotkeysEditable).toBe(true);

    expect(c.setHotkey("lobby", "F8")).toBe(true);
    expect(c.state.overlay.hotkey).toBe("F8");
    expect(saved().hotkeys).toEqual({ lobby: "F8", detail: "F7" });

    // A plain letter would stop the user typing it anywhere.
    expect(c.setHotkey("detail", "K")).toBe(false);
    expect(c.state.notice).toMatch(/Ctrl or Alt/);
    expect(c.setHotkey("detail", "F8")).toBe(false);
    expect(c.state.notice).toMatch(/other overlay hotkey/);
    expect(c.setHotkey("detail", "Ctrl+F9")).toBe(false);
    expect(c.state.notice).toMatch(/another program/);
    expect(applied.at(-1)).toBe("F8 F7"); // the old pair is registered again
    expect(c.state.overlay.detailHotkey).toBe("F7");

    expect(c.setHotkey("detail", "Ctrl+Alt+K")).toBe(true);
    expect(c.resetHotkeys()).toBe(true);
    expect(c.state.overlay).toMatchObject({ hotkey: "Shift+F2", detailHotkey: "F7" });
    c.stop();
  });

  it("falls back to the default hotkeys when a saved one is taken at start-up", () => {
    const applyHotkeys = (h: { lobby: string }) => (h.lobby === "F8" ? "F8 is already used by another program." : null);
    const { c } = setup({ hotkeys: { lobby: "F8", detail: "F7" } }, undefined, { applyHotkeys });
    c.start();
    expect(c.state.overlay.hotkey).toBe("Shift+F2");
    expect(c.state.notice).toMatch(/F8 is already used/);
    c.stop();
  });

  it("doesn't send a token saved for another server", () => {
    const account = { steamId: "76561198000000001", personaName: "M", avatarUrl: null };
    expect(setup({ serverUrl: "http://localhost:8000", token: "tok", account }).c.state.account).toEqual(account);
    const { c, saved } = setup({ serverUrl: "https://other.example", token: "tok", account });
    expect(c.state.account).toBeNull();
    expect(c.backend.token).toBeNull();
    expect(saved().token).toBe("tok"); // untouched until something is saved
  });

  it("turns starting with Windows on and off where it's offered", () => {
    expect(setup().c.state.startWithWindows).toBeNull();
    let on = false;
    const { c } = setup({}, undefined, { startup: { get: () => on, set: (v) => (on = v) } });
    expect(c.state.startWithWindows).toBe(false);
    c.setStartWithWindows(true);
    expect(on).toBe(true);
    expect(c.state.startWithWindows).toBe(true);
  });
});
