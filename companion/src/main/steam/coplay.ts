// Reads Steam's "recently played with" list (the coplay list, Steam > View > Players) from the Steam
// client that is already running on this PC. CS2 reports everyone in your match to Steam when the match
// loads, so this list holds the other players' Steam IDs a few seconds after you connect.
//
// How: the same way CS2 Player Fetcher does it (github.com/Poggicek/CS2-Player-Fetcher, MIT): load the
// Steam client's own library (steamclient64.dll, path from the registry), ask it for its ISteamClient
// interface, attach to the logged-in user and call ISteamFriends' coplay functions.
//
// Hard rule: this runs in its own process (coplay-worker.ts) and only talks to the Steam client. It never
// opens, reads or changes the CS2 process. Windows only.
//
// The Steam interfaces are C++ classes, so functions are called through their vtables. Slot numbers are
// the declaration order in Valve's public Steamworks headers for these exact interface versions:
//   ISteamClient  "SteamClient021":  0 CreateSteamPipe, 1 BReleaseSteamPipe, 2 ConnectToGlobalUser,
//                                    4 ReleaseUser, 8 GetISteamFriends
//   ISteamFriends "SteamFriends017": 0 GetPersonaName, 3 GetFriendCount, 4 GetFriendByIndex,
//                                    7 GetFriendPersonaName, 8 GetFriendGamePlayed, 45 GetFriendRichPresence,
//                                    46 GetFriendRichPresenceKeyCount, 47 GetFriendRichPresenceKeyByIndex,
//                                    50 GetCoplayFriendCount, 51 GetCoplayFriend, 52 GetFriendCoplayTime,
//                                    53 GetFriendCoplayGame
// x64 Windows has one calling convention: `this` is the first argument. GetCoplayFriend returns a
// CSteamID (a class with constructors), which MSVC returns through a hidden pointer passed right after
// `this` (also GetFriendByIndex). A CSteamID argument is 8 bytes and trivially copyable, so it travels as a plain uint64.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

export const CS2_APP_ID = 730;

export interface CoplayEntry {
  steamId: string;
  /** Steam's display name for the player, when the Steam client knows it. */
  name: string | null;
  appId: number;
  /** Unix seconds when the game reported playing with this player. */
  time: number;
}

/** A Steam friend who is playing CS2 right now, with the game's rich presence (e.g. game:map). */
export interface FriendInGame {
  steamId: string;
  name: string | null;
  presence: Record<string, string>;
}

export interface CoplayResult {
  localName: string | null;
  entries: CoplayEntry[];
  /** Friends playing CS2 now. Steam leaves friends out of the players list, so a friend in your match
   * (often your party) is only found here. Missing on older app builds. */
  friends?: FriendInGame[];
  /** Our own rich presence (e.g. steam_player_group, the party), when Steam gives it. */
  localPresence?: Record<string, string>;
}

const CLIENT_VERSION = "SteamClient021";
const FRIENDS_VERSION = "SteamFriends017";

/** steamclient64.dll of the running Steam client (HKCU\Software\Valve\Steam\ActiveProcess). */
export function steamClientDllPath(): string | null {
  try {
    const out = execFileSync("reg", ["query", "HKCU\\Software\\Valve\\Steam\\ActiveProcess", "/v", "SteamClientDll64"],
      { encoding: "utf8", windowsHide: true, timeout: 5000 });
    const m = /SteamClientDll64\s+REG_\w+\s+(.+)/.exec(out);
    const path = m?.[1]?.trim();
    return path && existsSync(path) ? path : null;
  } catch {
    return null;
  }
}

export class CoplayError extends Error {}

/** k_EFriendFlagImmediate: regular friends. */
const FRIEND_FLAG_IMMEDIATE = 0x04;

/** `localSteamId`: our own Steam ID when known, to read our own rich presence too. */
export function readCoplay(localSteamId?: string | null): CoplayResult {
  if (process.platform !== "win32") throw new CoplayError("Reading Steam's players list only works on Windows.");
  const dll = steamClientDllPath();
  if (!dll) throw new CoplayError("Steam isn't running (or isn't signed in). Start Steam and try again.");

  // Loaded lazily: koffi is a native module and only needed on Windows.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const koffi = require("koffi") as typeof import("koffi");
  const lib = koffi.load(dll);
  const CreateInterface = lib.func("void *CreateInterface(const char *name, _Out_ int *code)");

  const client = CreateInterface(CLIENT_VERSION, [0]);
  if (!client) throw new CoplayError("This Steam version doesn't offer the players list interface.");

  const slot = (iface: unknown, index: number) => koffi.decode(koffi.decode(iface, "void *"), index * 8, "void *");
  const P = {
    createPipe: koffi.proto("int CS_CreateSteamPipe(void *self)"),
    releasePipe: koffi.proto("bool CS_BReleaseSteamPipe(void *self, int pipe)"),
    connectUser: koffi.proto("int CS_ConnectToGlobalUser(void *self, int pipe)"),
    releaseUser: koffi.proto("void CS_ReleaseUser(void *self, int pipe, int user)"),
    getFriends: koffi.proto("void *CS_GetISteamFriends(void *self, int user, int pipe, const char *version)"),
    personaName: koffi.proto("const char *SF_GetPersonaName(void *self)"),
    friendName: koffi.proto("const char *SF_GetFriendPersonaName(void *self, uint64_t steamId)"),
    count: koffi.proto("int SF_GetCoplayFriendCount(void *self)"),
    friendAt: koffi.proto("void *SF_GetCoplayFriend(void *self, _Out_ uint64_t *ret, int index)"),
    time: koffi.proto("int SF_GetFriendCoplayTime(void *self, uint64_t steamId)"),
    game: koffi.proto("uint32_t SF_GetFriendCoplayGame(void *self, uint64_t steamId)"),
    friendCount: koffi.proto("int SF_GetFriendCount(void *self, int flags)"),
    friendByIndex: koffi.proto("void *SF_GetFriendByIndex(void *self, _Out_ uint64_t *ret, int index, int flags)"),
    // FriendGameInfo_t: CGameID (8), game IP (4), game port (2), query port (2), lobby CSteamID (8).
    gamePlayed: koffi.proto("bool SF_GetFriendGamePlayed(void *self, uint64_t steamId, void *info)"),
    presence: koffi.proto("const char *SF_GetFriendRichPresence(void *self, uint64_t steamId, const char *key)"),
    presenceKeys: koffi.proto("int SF_GetFriendRichPresenceKeyCount(void *self, uint64_t steamId)"),
    presenceKey: koffi.proto("const char *SF_GetFriendRichPresenceKeyByIndex(void *self, uint64_t steamId, int index)"),
  };
  const call = (iface: unknown, index: number, proto: unknown, ...args: unknown[]) =>
    koffi.call(slot(iface, index), proto as never, iface, ...args);

  const pipe = call(client, 0, P.createPipe) as number;
  if (!pipe) throw new CoplayError("Couldn't connect to the Steam client.");
  let user = 0;
  try {
    user = call(client, 2, P.connectUser, pipe) as number;
    if (!user) throw new CoplayError("Couldn't attach to the signed-in Steam user.");
    const friends = call(client, 8, P.getFriends, user, pipe, FRIENDS_VERSION);
    if (!friends) throw new CoplayError("This Steam version doesn't offer the players list interface.");

    const localName = (call(friends, 0, P.personaName) as string | null) || null;
    const n = call(friends, 50, P.count) as number;
    const entries: CoplayEntry[] = [];
    for (let i = 0; i < Math.min(n, 500); i++) {
      const out = [0n];
      call(friends, 51, P.friendAt, out, i);
      const id = BigInt(out[0]);
      if (!id) continue;
      entries.push({
        steamId: id.toString(),
        name: cleanName(call(friends, 7, P.friendName, id) as string | null),
        appId: call(friends, 53, P.game, id) as number,
        time: call(friends, 52, P.time, id) as number,
      });
    }

    const presenceOf = (id: bigint): Record<string, string> => {
      const out: Record<string, string> = {};
      const keys = call(friends, 46, P.presenceKeys, id) as number;
      for (let i = 0; i < Math.min(keys, 64); i++) {
        const key = call(friends, 47, P.presenceKey, id, i) as string | null;
        if (!key) continue;
        out[key] = (call(friends, 45, P.presence, id, key) as string | null) ?? "";
      }
      return out;
    };
    // Friends are a bonus: if this part fails, the players list above still counts.
    const inGame: FriendInGame[] = [];
    let localPresence: Record<string, string> | undefined;
    try {
      const total = call(friends, 3, P.friendCount, FRIEND_FLAG_IMMEDIATE) as number;
      const info = Buffer.alloc(24);
      for (let i = 0; i < Math.min(total, 2000); i++) {
        const out = [0n];
        call(friends, 4, P.friendByIndex, out, i, FRIEND_FLAG_IMMEDIATE);
        const id = BigInt(out[0]);
        if (!id) continue;
        info.fill(0);
        if (!call(friends, 8, P.gamePlayed, id, info)) continue;
        if (Number(info.readBigUInt64LE(0) & 0xffffffn) !== CS2_APP_ID) continue;
        inGame.push({ steamId: id.toString(), name: cleanName(call(friends, 7, P.friendName, id) as string | null), presence: presenceOf(id) });
      }
      if (localSteamId && /^\d{17}$/.test(localSteamId)) localPresence = presenceOf(BigInt(localSteamId));
    } catch {
      /* keep what we have */
    }
    return { localName, entries, friends: inGame, localPresence };
  } finally {
    if (user) call(client, 4, P.releaseUser, pipe, user);
    call(client, 1, P.releasePipe, pipe);
  }
}

function cleanName(s: string | null): string | null {
  const t = (s ?? "").trim();
  return t && t !== "[unknown]" ? t : null;
}
