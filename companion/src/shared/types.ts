// State shared by the main process and the windows (desktop window and in-game overlay).

/** Evidence classes, never "cheater" or a probability (analyzer spec section 22). */
export type EvidenceClass = "NORMAL" | "ELEVATED" | "HIGH" | "INSUFFICIENT_DATA";

export type Side = "T" | "CT";

/** One player in the current match, as the game reports it. */
export interface RosterPlayer {
  /** Position in Overwolf's roster (roster_0 .. roster_N). */
  slot: number;
  name: string;
  /** SteamID64 as a string. Null when the game didn't give one (bots, or a roster Overwolf couldn't fill). */
  steamId: string | null;
  side: Side | null;
  isLocal: boolean;
}

/** Map phase from the game: warm-up, then live once the match starts. */
export type MatchPhase = "warmup" | "live" | "intermission" | "gameover";

export interface MatchState {
  map: string | null;
  mode: string | null;
  phase: MatchPhase | null;
  localSteamId: string | null;
  players: RosterPlayer[];
}

export type RowStatus = "loading" | "ok" | "no-steam-id" | "error";

export type AxisLevel = "LOW" | "MEDIUM" | "HIGH";

/** The overlay's extended card (F7), sent by the server for ELEVATED and HIGH players only. */
export interface PlayerDetail {
  /** History evidence score, 0-100. Evidence strength, not a probability. */
  evidenceScore: number;
  highEvidenceMatches: number;
  axes: { wallTracking: AxisLevel; aim: AxisLevel; reaction: AxisLevel };
  /** Latest flagged matches, newest first. */
  recent: { map: string | null; evidenceScore: number; playedAt: string | null }[];
}

/** VAC/game bans on record at Steam: account-level, separate from the evidence class, never an alert.
 * Only sent when there is a VAC or game ban. */
export interface SteamBans {
  vacBans: number;
  gameBans: number;
  daysSinceLastBan: number;
  lastBanOn: string;
  profileUrl: string;
}

export interface LobbyRow extends RosterPlayer {
  classification: EvidenceClass | null;
  matchesAnalyzed: number;
  status: RowStatus;
  detail: PlayerDetail | null;
  bans: SteamBans | null;
}

export interface Account {
  steamId: string;
  personaName: string | null;
  avatarUrl: string | null;
}

export interface Pairing {
  userCode: string;
  verifyUrl: string;
  expiresAt: string;
}

export type GameSourceKind = "steam" | "overwolf" | "replay";

export interface AppState {
  version: string;
  server: {
    url: string;
    /** From the server's /site-info, e.g. "cheatscanner.eu". */
    domain: string | null;
    reachable: boolean | null;
    /** False on a local server without accounts: the app works without linking. */
    authEnabled: boolean | null;
  };
  account: Account | null;
  pairing: Pairing | null;
  /** Shown once, e.g. "The link was removed on the website". */
  notice: string | null;
  game: {
    source: GameSourceKind;
    running: boolean;
    /** Why live data isn't available, in plain words (e.g. no Overwolf developer approval yet). */
    problem: string | null;
  };
  match: { map: string | null; mode: string | null; phase: MatchPhase | null } | null;
  lobby: {
    rows: LobbyRow[];
    updatedAt: string | null;
    error: string | null;
  };
  overlay: {
    hotkey: string;
    /** Shows the extended card of the flagged players. */
    detailHotkey: string;
    /** "overwolf": drawn in the game by Overwolf; "window": a see-through, click-through window on top of
     *  the game (needs CS2 in "Fullscreen Windowed"). */
    mode: "overwolf" | "window" | "none";
    visible: boolean;
    view: "lobby" | "detail";
    /** Play a siren when a HIGH player is found in the match. */
    siren: boolean;
  };
  /** The overlay hotkeys can be changed in Settings (the app's own overlay window; not with Overwolf). */
  hotkeysEditable: boolean;
  /** Start with Windows, minimized to the taskbar; null where it isn't offered (not an installed app). */
  startWithWindows: boolean | null;
  /** Bumped when a HIGH player is found in the current match; the overlay plays the siren on a change. */
  alert: { seq: number; names: string[] };
}

/** What the windows may ask the main process to do (exposed by the preload script). */
export interface Bridge {
  getState(): Promise<AppState>;
  onState(listener: (state: AppState) => void): () => void;
  startLink(): Promise<void>;
  cancelLink(): Promise<void>;
  openLinkPage(): Promise<void>;
  unlink(): Promise<void>;
  /** Changes one overlay hotkey (an Electron accelerator such as "Shift+F2"); false if it can't be used. */
  setHotkey(which: "lobby" | "detail", hotkey: string): Promise<boolean>;
  resetHotkeys(): Promise<boolean>;
  setStartWithWindows(on: boolean): Promise<void>;
  /** Turns the hotkeys off while Settings waits for a key press, so the press reaches the page. */
  pauseHotkeys(paused: boolean): Promise<void>;
  toggleOverlay(): Promise<void>;
  toggleDetail(): Promise<void>;
  setSiren(on: boolean): Promise<void>;
  testSiren(): Promise<void>;
  openPlayer(steamId: string): Promise<void>;
}
