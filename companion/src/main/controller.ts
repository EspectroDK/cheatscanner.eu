// The app's state and flows (linking, lobby lookups, server checks), without any Electron code so it
// can be tested directly. main.ts connects it to windows, IPC and Overwolf.

import { EventEmitter } from "node:events";
import { cleanHotkeys, DEFAULT_HOTKEYS, hotkeyProblem, type HotkeyName, type Hotkeys } from "../shared/hotkeys";
import type { Account, AppState, MatchState } from "../shared/types";
import { ApiError, Backend } from "./backend";
import type { GameSource } from "./game/source";
import { LobbyService, type LobbyOptions } from "./lobby";

export interface Settings {
  /** The server the token belongs to. */
  serverUrl?: string;
  token?: string | null;
  account?: Account | null;
  /** Siren when a HIGH player is found (default on). */
  siren?: boolean;
  /** Overlay hotkeys chosen in Settings (default Shift+F2 and F7). */
  hotkeys?: Partial<Hotkeys>;
}

export interface SettingsStore {
  load(): Settings;
  save(s: Settings): void;
}

export interface ControllerDeps {
  version: string;
  source: GameSource;
  store: SettingsStore;
  /** Fixed for the app (https://cheatscanner.eu when packaged); not a user setting. */
  serverUrl: string;
  deviceName: string;
  openExternal: (url: string) => void;
  overlay: AppState["overlay"];
  fetchImpl?: typeof fetch;
  lobby?: LobbyOptions;
  /** Registers the overlay hotkeys with the system; returns why it failed (e.g. taken by another program). */
  applyHotkeys?: (h: Hotkeys) => string | null;
  /** Starting with Windows (a login item); absent where it isn't offered. */
  startup?: { get(): boolean; set(on: boolean): void };
  /** How often to retry an unreachable server (ms). */
  serverRetryMs?: number;
}

export class Controller extends EventEmitter<{ state: [AppState] }> {
  readonly backend: Backend;
  private lobby: LobbyService;
  private settings: Settings;
  private pairTimer: NodeJS.Timeout | null = null;
  private serverTimer: NodeJS.Timeout | null = null;
  private publicUrl: string | null = null;
  /** Players the siren already went off for, in the current match. */
  private alerted = new Set<string>();
  private matchKey: string | null = null;
  state: AppState;

  constructor(private deps: ControllerDeps) {
    super();
    const url = deps.serverUrl.trim().replace(/\/+$/, "");
    const loaded = deps.store.load();
    // A token belongs to one server: one saved for another address isn't sent anywhere.
    this.settings = loaded.serverUrl && loaded.serverUrl !== url
      ? { ...loaded, serverUrl: url, token: null, account: null }
      : { ...loaded, serverUrl: url };
    this.backend = new Backend(url, this.settings.token ?? null, deps.fetchImpl);
    this.lobby = new LobbyService((ids) => this.lookup(ids), () => this.publish(), deps.lobby);
    this.state = {
      version: deps.version,
      server: { url, domain: null, reachable: null, authEnabled: null },
      account: this.settings.token ? this.settings.account ?? null : null,
      pairing: null,
      notice: null,
      game: { source: deps.source.kind, running: false, problem: null },
      match: null,
      lobby: { rows: [], updatedAt: null, error: null },
      overlay: { ...deps.overlay, siren: this.settings.siren ?? true },
      hotkeysEditable: !!deps.applyHotkeys,
      startWithWindows: deps.startup ? deps.startup.get() : null,
      alert: { seq: 0, names: [] },
    };
  }

  start(): void {
    this.startHotkeys();
    const src = this.deps.source;
    src.on("running", (running) => this.update({ game: { ...this.state.game, running } }));
    src.on("problem", (problem) => this.update({ game: { ...this.state.game, problem } }));
    src.on("match", (m: MatchState) => this.onMatch(m));
    src.start();
    void this.refreshServer();
  }

  stop(): void {
    this.deps.source.stop();
    this.lobby.dispose();
    if (this.pairTimer) clearTimeout(this.pairTimer);
    if (this.serverTimer) clearTimeout(this.serverTimer);
  }

  /** Server facts, and whether the saved link still works. */
  async refreshServer(): Promise<void> {
    if (this.serverTimer) clearTimeout(this.serverTimer);
    this.serverTimer = null;
    try {
      const info = await this.backend.siteInfo();
      this.publicUrl = info.publicUrl || this.backend.baseUrl;
      this.update({ server: { url: this.backend.baseUrl, domain: info.domain, reachable: true, authEnabled: info.authEnabled } });
    } catch {
      this.update({ server: { ...this.state.server, url: this.backend.baseUrl, reachable: false } });
      this.serverTimer = setTimeout(() => void this.refreshServer(), this.deps.serverRetryMs ?? 30_000);
      return;
    }
    if (this.backend.token) {
      try {
        const me = await this.backend.me();
        this.setAccount({ steamId: me.steamId, personaName: me.personaName, avatarUrl: me.avatarUrl }, this.backend.token);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) this.unlink("The link to your account was removed on the website. Link the app again.");
      }
    }
    this.lobby.clear();
  }

  // ------------------------------------------------------------------ linking

  async startLink(): Promise<void> {
    if (this.state.pairing) {
      this.deps.openExternal(this.state.pairing.verifyUrl);
      return;
    }
    try {
      const p = await this.backend.startPairing(this.deps.deviceName);
      this.update({ pairing: { userCode: p.userCode, verifyUrl: p.verifyUrl, expiresAt: p.expiresAt }, notice: null });
      this.deps.openExternal(p.verifyUrl);
      this.pollLink(p.deviceCode, Math.max(1, p.interval) * 1000);
    } catch (e) {
      this.update({ notice: `Couldn't start linking: ${message(e)}` });
    }
  }

  private pollLink(deviceCode: string, intervalMs: number): void {
    this.pairTimer = setTimeout(async () => {
      if (!this.state.pairing) return;
      try {
        const r = await this.backend.pollPairing(deviceCode);
        if (r.status === "LINKED") {
          this.state.pairing = null;
          this.setAccount(r.user, r.token);
          this.lobby.clear();
          return;
        }
        if (r.status === "EXPIRED") {
          this.update({ pairing: null, notice: "The link code expired. Start again." });
          return;
        }
      } catch {
        /* network hiccup: keep polling until the code expires */
      }
      this.pollLink(deviceCode, intervalMs);
    }, intervalMs);
  }

  cancelLink(): void {
    if (this.pairTimer) clearTimeout(this.pairTimer);
    this.update({ pairing: null });
  }

  openLinkPage(): void {
    if (this.state.pairing) this.deps.openExternal(this.state.pairing.verifyUrl);
  }

  unlink(notice: string | null = null): void {
    this.backend.token = null;
    this.settings = { ...this.settings, token: null, account: null };
    this.deps.store.save(this.settings);
    this.update({ account: null, notice });
    this.lobby.clear();
  }

  private setAccount(account: Account, token: string): void {
    this.backend.token = token;
    this.settings = { ...this.settings, token, account };
    this.deps.store.save(this.settings);
    this.update({ account, notice: null });
  }

  // ------------------------------------------------------------------ settings

  /** The saved hotkeys; if the system refuses them (another program took one), the defaults. */
  private startHotkeys(): void {
    const saved = cleanHotkeys(this.settings.hotkeys);
    if (!this.deps.applyHotkeys) return;
    let err = this.deps.applyHotkeys(saved);
    let h = saved;
    if (err && (saved.lobby !== DEFAULT_HOTKEYS.lobby || saved.detail !== DEFAULT_HOTKEYS.detail)) {
      h = { ...DEFAULT_HOTKEYS };
      err = this.deps.applyHotkeys(h) ? err : `${err} Using the default hotkeys instead.`;
    }
    this.state.overlay = { ...this.state.overlay, hotkey: h.lobby, detailHotkey: h.detail };
    if (err) this.state.notice = err;
  }

  private get hotkeys(): Hotkeys {
    return { lobby: this.state.overlay.hotkey, detail: this.state.overlay.detailHotkey };
  }

  /** Changes one overlay hotkey; returns false (with a notice) when it can't be used. */
  setHotkey(which: HotkeyName, hotkey: string): boolean {
    return this.setHotkeys({ ...this.hotkeys, [which]: hotkey });
  }

  resetHotkeys(): boolean {
    return this.setHotkeys({ ...DEFAULT_HOTKEYS });
  }

  private setHotkeys(next: Hotkeys): boolean {
    const problem = hotkeyProblem(next.lobby) ?? hotkeyProblem(next.detail)
      ?? (next.lobby === next.detail ? `${next.lobby} is already the other overlay hotkey.` : null)
      ?? this.deps.applyHotkeys?.(next)
      ?? null;
    if (problem) {
      this.deps.applyHotkeys?.(this.hotkeys);
      this.update({ notice: problem });
      return false;
    }
    this.settings = { ...this.settings, hotkeys: next };
    this.deps.store.save(this.settings);
    this.update({ notice: null, overlay: { ...this.state.overlay, hotkey: next.lobby, detailHotkey: next.detail } });
    return true;
  }

  setOverlayVisible(visible: boolean): void {
    this.update({ overlay: { ...this.state.overlay, visible } });
  }

  /** Lobby hotkey: show the lobby list, or hide the overlay if the list is already showing. */
  toggleOverlay(): void {
    const o = this.state.overlay;
    const hide = o.visible && o.view === "lobby";
    this.update({ overlay: { ...o, visible: !hide, view: "lobby" } });
    if (!hide) this.deps.source.refresh();
  }

  /** Detail hotkey (F7): show the extended card of the flagged players, or hide it again. */
  toggleDetail(): void {
    const o = this.state.overlay;
    const hide = o.visible && o.view === "detail";
    this.update({ overlay: { ...o, visible: !hide, view: "detail" } });
  }

  setStartWithWindows(on: boolean): void {
    if (!this.deps.startup) return;
    this.deps.startup.set(on);
    this.update({ startWithWindows: this.deps.startup.get() });
  }

  setSiren(on: boolean): void {
    this.settings = { ...this.settings, siren: on };
    this.deps.store.save(this.settings);
    this.update({ overlay: { ...this.state.overlay, siren: on } });
  }

  testSiren(): void {
    this.update({ alert: { seq: this.state.alert.seq + 1, names: [] } });
  }

  // ------------------------------------------------------------------ match

  private onMatch(m: MatchState): void {
    const inMatch = m.players.length > 0;
    const prev = this.state.match;
    const key = inMatch ? `${m.map ?? ""}` : null;
    if (key !== this.matchKey) {
      // Another match (or none): the siren may go off again for the new lobby.
      this.matchKey = key;
      this.alerted.clear();
    }
    this.state.match = inMatch ? { map: m.map, mode: m.mode, phase: m.phase } : null;
    const o = this.state.overlay;
    if (inMatch && !prev && m.phase !== "live") {
      // The lobby just appeared in warm-up: show it without a key press.
      this.state.overlay = { ...o, visible: true, view: "lobby" };
    } else if (inMatch && m.phase === "live" && prev?.phase !== "live") {
      // The match started: out of the way until a hotkey brings it back.
      this.state.overlay = { ...o, visible: false };
    }
    this.lobby.setMatch(inMatch ? m : null);
  }

  /** Siren once per HIGH player per match (the overlay plays it when alert.seq changes). */
  private checkAlerts(): void {
    if (!this.state.match) return;
    const fresh = this.state.lobby.rows.filter((r) => r.classification === "HIGH" && r.steamId && !r.isLocal && !this.alerted.has(r.steamId));
    if (fresh.length === 0) return;
    for (const r of fresh) this.alerted.add(r.steamId!);
    if (this.state.overlay.siren) this.state.alert = { seq: this.state.alert.seq + 1, names: fresh.map((r) => r.name) };
  }

  playerUrl(steamId: string): string | null {
    return /^\d{17}$/.test(steamId) ? `${this.publicUrl ?? this.backend.baseUrl}/#/players/${steamId}` : null;
  }

  // ------------------------------------------------------------------ lobby

  private async lookup(steamIds: string[]) {
    if (!this.state.account && this.state.server.authEnabled !== false)
      throw new Error("Link the app to your account to see evidence classes.");
    try {
      return await this.backend.lobbyRisk(steamIds.map((steamId) => ({ steamId })));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        this.unlink("The link to your account was removed on the website. Link the app again.");
        throw new Error("Link the app to your account to see evidence classes.");
      }
      if (e instanceof ApiError && e.status === 403)
        throw new Error(`Finish setting up your account on ${this.state.server.domain ?? "the website"} first.`);
      throw new Error(message(e));
    }
  }

  // ------------------------------------------------------------------ state

  private update(patch: Partial<AppState>): void {
    Object.assign(this.state, patch);
    this.publish();
  }

  private publish(): void {
    this.state.lobby = { rows: this.lobby.rows(), updatedAt: this.lobby.updatedAt, error: this.lobby.error };
    this.checkAlerts();
    this.emit("state", this.state);
  }
}

function message(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError")) return "the server didn't answer";
  if (e instanceof TypeError) return "the server can't be reached";
  return e instanceof Error ? e.message : String(e);
}
