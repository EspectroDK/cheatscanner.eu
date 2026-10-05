// CS2 Game State Integration (GSI): Valve's own, supported way for a program to follow the game. CS2 reads
// gamestate_integration_*.cfg from its cfg folder at start-up and then POSTs JSON to the address in it.
// For other players' Steam IDs it is no use during a live match (allplayers is spectator-only), but it
// tells us when a map loads, when warm-up ends and the match goes live, the mode, and our own Steam ID.

import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";

export const GSI_PORT = 37215;
const CFG_NAME = "gamestate_integration_cheatscanner.cfg";

export type MapPhase = "warmup" | "live" | "intermission" | "gameover";

export interface GameState {
  map: string | null;
  mode: string | null;
  phase: MapPhase | null;
  localSteamId: string | null;
}

/** The fields we use from one GSI payload. */
export function parseGsi(body: unknown): GameState {
  const o = (body && typeof body === "object" ? body : {}) as Record<string, any>;
  const map = o.map && typeof o.map === "object" ? o.map : null;
  const phase = String(map?.phase ?? "");
  const sid = String(o.provider?.steamid ?? "");
  return {
    map: typeof map?.name === "string" && map.name ? map.name : null,
    mode: typeof map?.mode === "string" && map.mode ? map.mode : null,
    phase: (["warmup", "live", "intermission", "gameover"] as const).find((p) => p === phase) ?? null,
    localSteamId: /^7656\d{13}$/.test(sid) ? sid : null,
  };
}

export function gsiConfig(token: string, port = GSI_PORT): string {
  return `"Cheatscanner"
{
  "uri"        "http://127.0.0.1:${port}/gsi"
  "timeout"    "1.0"
  "buffer"     "0.1"
  "throttle"   "0.5"
  "heartbeat"  "10.0"
  "auth"       { "token" "${token}" }
  "data"
  {
    "provider"  "1"
    "map"       "1"
    "round"     "1"
  }
}
`;
}

/** "path" values from Steam's libraryfolders.vdf. */
export function libraryPaths(vdf: string): string[] {
  return [...vdf.matchAll(/"path"\s+"([^"]+)"/g)].map((m) => m[1].replace(/\\\\/g, "\\"));
}

function steamPath(): string | null {
  if (process.platform === "linux") {
    // Native Steam: ~/.steam/debian-installation (Debian, Ubuntu, Linux Mint, Pop!_OS, Zorin) or
    // ~/.local/share/Steam (everything else); on the Debian family one is normally a symlink to the other.
    for (const rel of [".steam/debian-installation", ".local/share/Steam"]) {
      const dir = join(homedir(), rel);
      if (existsSync(join(dir, "steamapps"))) return dir;
    }
    return null;
  }
  try {
    const out = execFileSync("reg", ["query", "HKCU\\Software\\Valve\\Steam", "/v", "SteamPath"],
      { encoding: "utf8", windowsHide: true, timeout: 5000 });
    return /SteamPath\s+REG_\w+\s+(.+)/.exec(out)?.[1]?.trim() ?? null;
  } catch {
    return null;
  }
}

/** CS2's cfg folder, found through Steam's library list. */
export function findCs2CfgDir(): string | null {
  if (process.platform !== "win32" && process.platform !== "linux") return null;
  const steam = steamPath();
  if (!steam) return null;
  const libs = [steam];
  const vdf = join(steam, "steamapps", "libraryfolders.vdf");
  if (existsSync(vdf)) libs.push(...libraryPaths(readFileSync(vdf, "utf8")));
  for (const lib of libs) {
    const dir = join(lib, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg");
    if (existsSync(dir)) return dir;
  }
  return null;
}

export type InstallResult = { status: "installed" | "unchanged"; path: string } | { status: "not-found" };

/** Writes (or refreshes) our GSI file. CS2 only reads it at start-up. */
export function installGsiConfig(token: string, dir = findCs2CfgDir()): InstallResult {
  if (!dir) return { status: "not-found" };
  const path = join(dir, CFG_NAME);
  const text = gsiConfig(token);
  if (existsSync(path) && readFileSync(path, "utf8") === text) return { status: "unchanged", path };
  writeFileSync(path, text);
  return { status: "installed", path };
}

export function newGsiToken(): string {
  return randomBytes(16).toString("hex");
}

/** Listens on 127.0.0.1 only. Payloads without our token are ignored. */
export function startGsiServer(token: string, onState: (s: GameState) => void, port = GSI_PORT): Server {
  const server = createServer((req, res) => {
    if (req.method !== "POST" || req.url !== "/gsi") {
      res.writeHead(404).end();
      return;
    }
    let size = 0;
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > 1_000_000) req.destroy();
      else chunks.push(c);
    });
    req.on("end", () => {
      res.writeHead(200).end();
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (body?.auth?.token !== token) return;
        onState(parseGsi(body));
      } catch {
        /* not JSON */
      }
    });
  });
  server.on("error", (e) => console.warn("game state listener:", e.message));
  server.listen(port, "127.0.0.1");
  return server;
}
