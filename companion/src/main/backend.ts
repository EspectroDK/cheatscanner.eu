// Client for the Cheatscanner server (api/companion.py and friends). Runs in the main process, so
// the token never reaches a web page and no CORS setup is needed on the server.

import type { Account, EvidenceClass, PlayerDetail, SteamBans } from "../shared/types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export interface SiteInfo {
  name: string;
  domain: string;
  publicUrl: string;
  authEnabled: boolean;
}

export interface PairStart {
  deviceCode: string;
  userCode: string;
  verifyUrl: string;
  expiresAt: string;
  interval: number;
}

export type PairPoll =
  | { status: "PENDING" }
  | { status: "EXPIRED" }
  | { status: "LINKED"; token: string; user: Account };

export interface LobbyAnswer {
  steamId: string | null;
  classification: EvidenceClass | null;
  matchesAnalyzed: number;
  /** Last name the server saw for this player. */
  name?: string | null;
  /** The F7 card, for ELEVATED and HIGH players only. */
  detail?: PlayerDetail | null;
  /** VAC/game bans on record at Steam, only when there is one. */
  bans?: SteamBans | null;
}

type Fetch = typeof fetch;

export class Backend {
  constructor(public baseUrl: string, public token: string | null = null, private fetchImpl: Fetch = fetch,
              private timeoutMs = 10_000) {}

  siteInfo(): Promise<SiteInfo> {
    return this.call("GET", "/site-info");
  }

  me(): Promise<Account & { id: number }> {
    return this.call("GET", "/me");
  }

  startPairing(deviceName: string): Promise<PairStart> {
    return this.call("POST", "/companion/pair", { deviceName });
  }

  async pollPairing(deviceCode: string): Promise<PairPoll> {
    const res = await this.raw("POST", "/companion/pair/token", { deviceCode });
    if (res.status === 202) return { status: "PENDING" };
    if (res.status === 410) return { status: "EXPIRED" };
    if (!res.ok) throw await toError(res);
    return (await res.json()) as PairPoll;
  }

  async lobbyRisk(players: { steamId: string | null }[]): Promise<LobbyAnswer[]> {
    const body = await this.call<{ players: LobbyAnswer[] }>("POST", "/lobby/risk", { players });
    return body.players;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await this.raw(method, path, body);
    if (!res.ok) throw await toError(res);
    return (await res.json()) as T;
  }

  private raw(method: string, path: string, body?: unknown): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    return this.fetchImpl(this.baseUrl.replace(/\/+$/, "") + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
  }
}

async function toError(res: Response): Promise<ApiError> {
  let detail = res.statusText || `HTTP ${res.status}`;
  try {
    const j = (await res.json()) as { detail?: unknown };
    if (j && typeof j.detail === "string") detail = j.detail;
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, detail);
}
