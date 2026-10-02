import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchState } from "../src/shared/types";
import { LobbyService } from "../src/main/lobby";

const player = (slot: number, steamId: string | null) => ({ slot, name: `p${slot}`, steamId, side: "CT" as const, isLocal: false });
const match = (...ids: (string | null)[]): MatchState => ({ map: null, mode: null, localSteamId: null, players: ids.map((id, i) => player(i, id)) });

describe("LobbyService", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("asks once for a filling lobby, and not again for known players", async () => {
    const lookup = vi.fn(async (ids: string[]) => ids.map((steamId) => ({ steamId, classification: "NORMAL" as const, matchesAnalyzed: 3 })));
    const lobby = new LobbyService(lookup, () => {}, { debounceMs: 100 });
    lobby.setMatch(match("76561198000000001"));
    lobby.setMatch(match("76561198000000001", "76561198000000002", null));
    expect(lobby.rows().map((r) => r.status)).toEqual(["loading", "loading", "no-steam-id"]);
    await vi.advanceTimersByTimeAsync(150);
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(lookup.mock.calls[0][0]).toEqual(["76561198000000001", "76561198000000002"]);
    expect(lobby.rows()[0]).toMatchObject({ status: "ok", classification: "NORMAL", matchesAnalyzed: 3 });

    // Same players again (e.g. a kills update): no new request.
    lobby.setMatch(match("76561198000000002", "76561198000000001"));
    await vi.advanceTimersByTimeAsync(500);
    expect(lookup).toHaveBeenCalledTimes(1);
  });

  it("shows the error and retries", async () => {
    let fail = true;
    const lookup = vi.fn(async (ids: string[]) => {
      if (fail) throw new Error("the server can't be reached");
      return ids.map((steamId) => ({ steamId, classification: "HIGH" as const, matchesAnalyzed: 9 }));
    });
    const lobby = new LobbyService(lookup, () => {}, { debounceMs: 10, retryMs: 1000 });
    lobby.setMatch(match("76561198000000001"));
    await vi.advanceTimersByTimeAsync(20);
    expect(lobby.error).toBe("the server can't be reached");
    expect(lobby.rows()[0].status).toBe("error");
    fail = false;
    await vi.advanceTimersByTimeAsync(1000);
    expect(lobby.error).toBeNull();
    expect(lobby.rows()[0]).toMatchObject({ status: "ok", classification: "HIGH" });
  });
});
