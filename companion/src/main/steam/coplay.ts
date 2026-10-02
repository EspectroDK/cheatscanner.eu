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
//   ISteamFriends "SteamFriends017": 0 GetPersonaName, 7 GetFriendPersonaName, 50 GetCoplayFriendCount,
//                                    51 GetCoplayFriend, 52 GetFriendCoplayTime, 53 GetFriendCoplayGame
// x64 Windows has one calling convention: `this` is the first argument. GetCoplayFriend returns a
// CSteamID (a class with constructors), which MSVC returns through a hidden pointer passed right after
// `this`. A CSteamID argument is 8 bytes and trivially copyable, so it travels as a plain uint64.

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

export interface CoplayResult {
  localName: string | null;
  entries: CoplayEntry[];
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

export function readCoplay(): CoplayResult {
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
    return { localName, entries };
  } finally {
    if (user) call(client, 4, P.releaseUser, pipe, user);
    call(client, 1, P.releasePipe, pipe);
  }
}

function cleanName(s: string | null): string | null {
  const t = (s ?? "").trim();
  return t && t !== "[unknown]" ? t : null;
}
